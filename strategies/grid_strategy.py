"""
SoAI 2026 — ADX 趨勢強度過濾的日內網格策略

核心概念（v2.5）：
  ADX(14) 是衡量趨勢強度的最佳指標，不分漲跌，只看趨勢夠不夠明顯。
    - ADX < 25        → 市場沒有方向（橫盤震盪）→ 網格最愛環境 → 允許啟動網格
    - ADX ≥ 25（突破） → 單邊趨勢已形成（無論多空）→ 立刻暫停新建網格層

網格機制（每日重置，純日內）：
  1. 基準價：每日開盤價（GRID_ANCHOR = "OPEN"），也可用當日 VWAP
  2. 網格間距：spacing = GRID_SPACING_ATR × ATR(14)（5 分鐘 K 線）
  3. 買入層：基準價下方 1..GRID_NUM_LEVELS 層
       level i 買入價 = base - i × spacing
       當 bar 最低價觸及買入價 → 以固定金額（GRID_LEVEL_VALUE）買入該層
  4. 賣出層：每層買入後，價格回升一個間距即賣出
       level i 賣出價 = 買入價 + spacing
       當 bar 最高價觸及賣出價 → 賣出該層全部持倉（每層盈利 = spacing × 股數）
  5. ADX 熔斷：ADX ≥ 25 → 禁止買入新層（已持倉層仍可正常賣出）

風控：
  - 15:50 後禁止新建網格層（避免 15:55 前無法平倉）
  - 15:55 強制清倉全部持倉
  - 日內虧損熔斷 $15,000
  - 單日最多 20 筆
  - 開盤 30 分鐘（9:30-10:00）僅監控不下單
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from lumibot.strategies import Strategy as _LumibotStrategy

from strategies.params import (
    TRADE_SYMBOL,
    INITIAL_CAPITAL,
    SLEEPTIME,
    RESAMPLE_MINUTES,
    ADX_PERIOD,
    ADX_GRID_START_THRESHOLD,
    ADX_GRID_HALT_THRESHOLD,
    GRID_NUM_LEVELS,
    GRID_SPACING_ATR,
    GRID_LEVEL_VALUE,
    GRID_ANCHOR,
    ADX_BAR_MINUTES,
    LAST_ENTRY_TIME_HOUR,
    LAST_ENTRY_TIME_MINUTE,
    ATR_PERIOD,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_TRADES,
)
from strategies.indicators import compute_adx, compute_atr


class GridStrategy(_LumibotStrategy):
    """
    ADX 過濾的日內網格策略。

    ADX(14) < 25 → 震盪市 → 允許啟動網格（低買高賣）
    ADX(14) ≥ 25 → 趨勢市 → 熔斷，暫停新建網格層
    """

    # ------------------------------------------------------------------
    # Lifecycle: setup
    # ------------------------------------------------------------------
    def initialize(self):
        self.sleeptime = SLEEPTIME

        # 美東時區
        self._ny_tz = ZoneInfo("America/New_York")
        self.intraday_mode = self.sleeptime in ("1M", "5M", "15M", "60M")

        # --- 每日網格狀態 ---
        self._day = None            # 當前交易日（換日重置）
        self._base_price = 0.0      # 網格基準價（每日開盤價）
        self._spacing = 0.0         # 網格間距（ATR × 倍數）
        self._levels = {}           # level_index -> {bought: bool, qty: float, buy_price: float}
        self._grid_halted = False   # ADX 熔斷旗標（True = 禁止新買層）
        self._day_start_value = 0.0

        # --- 風控計數 ---
        self._daily_trade_count = 0
        self._last_date = None
        self._halted = False

        self.log_message(
            f"[GridStrategy] 初始化完成 | ADX<{ADX_GRID_START_THRESHOLD:.0f} 啟動網格 | "
            f"層數={GRID_NUM_LEVELS} | 間距={GRID_SPACING_ATR}×ATR | 每層=${GRID_LEVEL_VALUE:,.0f}"
        )

    # ------------------------------------------------------------------
    # 每日重置
    # ------------------------------------------------------------------
    def _check_new_day(self, now: datetime):
        current_date = now.date()
        if self._last_date is not None and current_date != self._last_date:
            # 換日：重置網格與風控計數
            self._day = current_date
            self._base_price = 0.0
            self._spacing = 0.0
            self._levels = {}
            self._grid_halted = False
            self._daily_trade_count = 0
            self._halted = False
            self._day_start_value = self.get_portfolio_value()
            self.log_message(f"[GridStrategy] 新交易日 {current_date} | 網格重置")
        self._last_date = current_date
        if self._day is None:
            self._day = current_date

    def _incr_trade(self):
        self._daily_trade_count += 1

    def _can_trade(self) -> bool:
        if self._halted:
            return False
        if self._daily_trade_count >= MAX_DAILY_TRADES:
            return False
        return True

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def on_trading_iteration(self):
        if not self.intraday_mode:
            self.log_message("[GridStrategy] 僅支援分鐘模式（1M/5M/15M/60M）")
            return

        now = self.get_datetime()
        portfolio_value = self.get_portfolio_value()
        self._check_new_day(now)

        if self._halted:
            return

        now_ny = now.astimezone(self._ny_tz)
        t = now_ny.time()

        # --- 開盤 30 分鐘僅監控 ---
        if time(9, 30) <= t < time(10, 0):
            return

        # --- 收市管理：15:50 禁新倉、15:55 強制清倉 ---
        # 註：Pandas 回測中 get_historical_prices 只回傳「截至當前」的資料，
        #     無法偵測半日市；固定時段是最穩健的方案（與 VWAP 策略一致）。
        if t >= time(15, 55):
            self._sell_all(now, "15:55 強制清倉")
            self._grid_halted = True
            return
        if t >= time(LAST_ENTRY_TIME_HOUR, LAST_ENTRY_TIME_MINUTE):
            self._grid_halted = True  # 15:50 後禁止新建網格層

        # --- 一次獲取全部歷史資料 ---
        try:
            data = self._load_bars(now)
            if data is None:
                return
            price, low, high = data["price"], data["low"], data["high"]
            adx = data["adx"]
        except Exception as e:
            import traceback
            self.log_message(f"[ERROR] _load_bars 異常: {e}\n{traceback.format_exc()}")
            return

        # --- 每日初始化：基準價 + 間距 ---
        if self._base_price <= 0:
            self._init_grid(now, data)

        # --- 日內虧損熔斷 ---
        daily_pnl = portfolio_value - self._day_start_value
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            self._halted = True
            self.log_message(f"[GridStrategy] 日內熔斷: 虧損 ${abs(daily_pnl):,.0f}，停止交易")
            self._sell_all(now, "日內熔斷")
            return

        # --- ADX 熔斷：ADX ≥ 25 → 暫停新建網格層（已持層仍可賣出） ---
        if adx is not None and adx >= ADX_GRID_HALT_THRESHOLD:
            if not self._grid_halted:
                self.log_message(
                    f"[GridStrategy] ADX={adx:.1f} ≥ {ADX_GRID_HALT_THRESHOLD:.0f} "
                    f"— 單邊趨勢形成，暫停新建網格層"
                )
            self._grid_halted = True

        # --- 檢查各層賣出（先賣後買，優先鎖利） ---
        self._check_sells(now, price, high)

        # --- 檢查各層買入（ADX < 25 才允許） ---
        if not self._grid_halted:
            self._check_buys(now, price, low)
        elif adx is not None and adx < ADX_GRID_START_THRESHOLD and t < time(LAST_ENTRY_TIME_HOUR, LAST_ENTRY_TIME_MINUTE):
            # ADX 回落 < 25 且未過最後進場時間 → 恢復網格
            self._grid_halted = False

    # ------------------------------------------------------------------
    # 一次載入全部 bar 資料（性能優化：避免 catch-up）
    # ------------------------------------------------------------------
    def _load_bars(self, now: datetime) -> dict | None:
        """
        一次呼叫 get_historical_prices，解析出本 iteration 所需的全部資料：
        price / low / high（當前 bar）、mins_to_close（收市檢測）、adx（15m）。
        """
        bars = self.get_historical_prices(TRADE_SYMBOL, 500, "minute")
        if bars is None or bars.df is None or bars.df.empty:
            return None
        df = bars.df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        idx = pd.to_datetime(df.index, utc=True)
        df = df.set_index(idx)
        local = idx.tz_convert(self._ny_tz)
        today = now.astimezone(self._ny_tz).date()
        today_mask = local.date == today
        today_bars = df[today_mask]

        if today_bars.empty:
            return None

        cur = today_bars.iloc[-1]
        result = {
            "open": float(today_bars["open"].iloc[0]),
            "price": float(cur["close"]),
            "low": float(cur["low"]),
            "high": float(cur["high"]),
            "adx": None,
            "atr": None,
        }

        # ATR：5 分鐘 K 線
        r5 = (
            df.resample(f"{RESAMPLE_MINUTES}min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
        )
        if len(r5) >= ATR_PERIOD + 2:
            atr_series = compute_atr(r5["high"], r5["low"], r5["close"], period=ATR_PERIOD)
            atr_val = atr_series.iloc[-1]
            if not pd.isna(atr_val):
                result["atr"] = float(atr_val)

        # ADX：15 分鐘 K 線
        r15 = (
            df.resample(f"{ADX_BAR_MINUTES}min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
        )
        if len(r15) >= ADX_PERIOD * 2 + 2:
            adx_series = compute_adx(r15["high"], r15["low"], r15["close"], period=ADX_PERIOD)
            val = adx_series.iloc[-1]
            if not pd.isna(val):
                result["adx"] = float(val)

        return result

    # ------------------------------------------------------------------
    # 網格初始化（每日）
    # ------------------------------------------------------------------
    def _init_grid(self, now: datetime, data: dict):
        """
        建立當日網格：基準價 + 間距 + 買入層價格。
        """
        base = data.get("open")
        if base is None or base <= 0:
            base = data["price"]
        if base is None or base <= 0:
            return

        self._base_price = float(base)

        # 間距：5 分鐘 K 線的 ATR × 倍數
        atr = data.get("atr")
        if atr and atr > 0:
            self._spacing = atr * GRID_SPACING_ATR
        else:
            self._spacing = self._base_price * 0.005

        # 建立買入層
        self._levels = {}
        for i in range(1, GRID_NUM_LEVELS + 1):
            buy_price = self._base_price - i * self._spacing
            self._levels[i] = {
                "bought": False,
                "qty": 0.0,
                "buy_price": buy_price,
                "sell_price": buy_price + self._spacing,
            }

        self.log_message(
            f"[GridStrategy] 網格建立 | 基準=${self._base_price:.2f} | "
            f"間距=${self._spacing:.2f} | 層1買=${self._levels[1]['buy_price']:.2f} "
            f"層{GRID_NUM_LEVELS}買=${self._levels[GRID_NUM_LEVELS]['buy_price']:.2f}"
        )

    # ------------------------------------------------------------------
    # 網格執行
    # ------------------------------------------------------------------
    def _check_buys(self, now: datetime, price: float, low: float):
        """檢查各買入層：bar 最低價觸及買入價 → 買入固定金額。"""
        for i in sorted(self._levels.keys(), reverse=True):  # 先深層後淺層
            level = self._levels[i]
            if level["bought"]:
                continue
            if low <= level["buy_price"] and price <= level["buy_price"] * 1.01:
                qty = int(GRID_LEVEL_VALUE / price)
                if qty <= 0:
                    continue
                order = self.create_order(TRADE_SYMBOL, qty, "buy")
                self.submit_order(order)
                level["bought"] = True
                level["qty"] = float(qty)
                self._incr_trade()
                self.log_message(
                    f"[Grid BUY] {now} | 層{i} 買入 @${price:.2f} (目標${level['buy_price']:.2f}) "
                    f"qty={qty} | 賣出目標 ${level['sell_price']:.2f}"
                )

    def _check_sells(self, now: datetime, price: float, high: float):
        """檢查各持有層賣出：bar 最高價觸及賣出價 → 賣出該層。"""
        for i in sorted(self._levels.keys()):  # 先淺層後深層
            level = self._levels[i]
            if not level["bought"] or level["qty"] <= 0:
                continue
            if high >= level["sell_price"] or price >= level["sell_price"]:
                qty = int(level["qty"])
                if qty <= 0:
                    continue
                order = self.create_order(TRADE_SYMBOL, qty, "sell")
                self.submit_order(order)
                pnl = qty * (min(price, level["sell_price"]) - level["buy_price"])
                level["qty"] = 0.0
                self._incr_trade()
                self.log_message(
                    f"[Grid SELL] {now} | 層{i} 賣出 @${price:.2f} (目標${level['sell_price']:.2f}) "
                    f"qty={qty} | 盈利≈${pnl:,.0f}"
                )

    def _sell_all(self, now: datetime, reason: str):
        """賣出全部持倉。"""
        positions = self.get_positions()
        for p in positions:
            if p.symbol == TRADE_SYMBOL and float(p.quantity) > 0:
                order = self.create_order(TRADE_SYMBOL, float(p.quantity), "sell")
                self.submit_order(order)
                self.log_message(f"[Grid SELL ALL] {now} | {reason} | qty={p.quantity}")
        # 重置層狀態
        for level in self._levels.values():
            level["qty"] = 0.0
