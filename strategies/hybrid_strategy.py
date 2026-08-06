"""
SoAI 2026 — ADX 混合模式策略（Hybrid Strategy）

核心概念（v2.6）：
  以 ADX(14)（15 分鐘 K 線計算）作為市場狀態判別器，動態切換兩種交易引擎：

    ADX(14) < 25  → 震盪市（無趨勢）→ 網格引擎（低買高賣，吃波動）
    ADX(14) ≥ 25  → 趨勢市（單邊）  → VWAP 回踩引擎（順勢跟單）

模式切換：
  - 切換瞬間強制清倉全部持倉，再進入新模式（避免跨模式持倉混亂）
  - 每日 15:50 後禁止新建倉位，15:55 強制清倉

引擎 A：網格（ADX < 25）
  - 基準價 = 當日開盤價，3 層買入（間距 = 1×ATR）
  - bar 最低價觸及買入價 → 買入 $50K 該層
  - bar 最高價觸及賣出價（買入價+1 間距）→ 賣出該層

引擎 B：VWAP 回踩（ADX ≥ 25）
  - Daily VWAP 多頭過濾（價格 > 當日 VWAP）
  - RS Ratio > EMA（MU 強於 SMH）
  - 5m VWAP 回踩不破 + RSI > 52 + 放量 1.9× → 買入
  - 布林上軌/RSI>70 平 50%，跌破 20-EMA 清倉，ATR 追蹤止損
  - 底倉（stage=2）允許再加倉
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from lumibot.strategies import Strategy as _LumibotStrategy

from strategies.params import (
    TRADE_SYMBOL,
    SLEEPTIME,
    RESAMPLE_MINUTES,
    ADX_PERIOD,
    ADX_GRID_START_THRESHOLD,
    ADX_GRID_HALT_THRESHOLD,
    GRID_NUM_LEVELS,
    GRID_SPACING_ATR,
    GRID_LEVEL_VALUE,
    LAST_ENTRY_TIME_HOUR,
    LAST_ENTRY_TIME_MINUTE,
    ATR_PERIOD,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_TRADES,
    ATR_STOP_MULTIPLIER,
    MAX_POSITION_VALUE,
    TAKE_PROFIT_RATIO,
    RSI_OVERSOLD_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RS_EMA_PERIOD,
    VWAP_LOOKBACK,
    EMA_SLOW,
    VOLUME_SURGE_MULTIPLIER,
)
from strategies.indicators import compute_adx, compute_atr, compute_rsi, compute_rolling_vwap, compute_ema, compute_bollinger_bands
from strategies.signal_generator import check_exit_signals, check_relative_strength, check_vwap_break, check_pullback_entry, Signal, HOLD


class HybridStrategy(_LumibotStrategy):
    MODE_GRID = "GRID"
    MODE_VWAP = "VWAP"

    # 遲滯參數（避免 ADX 在 25 門檻附近抖動造成頻繁切換）：
    #   GRID → VWAP：ADX ≥ 28（明確趨勢）
    #   VWAP → GRID：ADX <  22（明確震盪）
    #   22 ≤ ADX < 28：維持現狀
    HYBRID_ADX_CHOP_ENTER = 22.0
    HYBRID_ADX_TREND_ENTER = 28.0

    # ------------------------------------------------------------------
    # Lifecycle: setup
    # ------------------------------------------------------------------
    def initialize(self):
        self.sleeptime = SLEEPTIME
        self._ny_tz = ZoneInfo("America/New_York")

        # --- 模式狀態 ---
        self._mode = self.MODE_GRID          # 預設網格，由 ADX 決定
        self._adx: float | None = None

        # --- 風控 ---
        self._last_date = None
        self._day_start_value = self.get_portfolio_value()
        self._daily_trade_count = 0
        self._halted = False

        # --- 網格狀態 ---
        self._base_price = 0.0
        self._spacing = 0.0
        self._levels = {}
        self._grid_halted = False

        # --- VWAP 引擎狀態 ---
        self._has_position = False
        self._position_quantity = 0.0
        self._position_stage = 0
        self._entry_price = 0.0
        self._indicators_cache: dict | None = None

        self.log_message(
            f"[HybridStrategy] 初始化完成 | ADX<{ADX_GRID_START_THRESHOLD:.0f}=網格 / "
            f"ADX≥{ADX_GRID_HALT_THRESHOLD:.0f}=VWAP回踩"
        )

    # ------------------------------------------------------------------
    # 每日重置
    # ------------------------------------------------------------------
    def _check_new_day(self, now: datetime):
        cur_date = now.date()
        if self._last_date is not None and cur_date != self._last_date:
            self._base_price = 0.0
            self._spacing = 0.0
            self._levels = {}
            self._grid_halted = False
            self._has_position = False
            self._position_quantity = 0.0
            self._position_stage = 0
            self._entry_price = 0.0
            self._daily_trade_count = 0
            self._halted = False
            self._day_start_value = self.get_portfolio_value()
            self.log_message(f"[HybridStrategy] 新交易日 {cur_date} | 狀態重置")
        self._last_date = cur_date

    def _incr_trade(self):
        self._daily_trade_count += 1

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def on_trading_iteration(self):
        now = self.get_datetime()
        portfolio_value = self.get_portfolio_value()
        self._check_new_day(now)

        if self._halted:
            return

        now_ny = now.astimezone(self._ny_tz)
        t = now_ny.time()

        # 開盤 30 分鐘僅監控
        if time(9, 30) <= t < time(10, 0):
            return

        # 收市管理
        if t >= time(15, 55):
            self._sell_all(now, "15:55 強制清倉")
            return
        entry_cutoff = time(LAST_ENTRY_TIME_HOUR, LAST_ENTRY_TIME_MINUTE)

        # --- 載入資料（一次呼叫，含 ADX） ---
        data = self._load_bars(now)
        if data is None:
            return
        self._adx = data["adx"]

        # --- ADX 模式切換（含遲滯，避免 25 門檻附近抖動） ---
        #  GRID → VWAP：ADX ≥ 28（明確趨勢）
        #  VWAP → GRID：ADX <  22（明確震盪）
        #  22 ≤ ADX < 28：維持現狀
        if self._adx is not None:
            if self._mode == self.MODE_GRID and self._adx >= self.HYBRID_ADX_TREND_ENTER:
                self._switch_mode(now, self.MODE_VWAP)
            elif self._mode == self.MODE_VWAP and self._adx < self.HYBRID_ADX_CHOP_ENTER:
                self._switch_mode(now, self.MODE_GRID)

        # --- 日內熔斷 ---
        daily_pnl = portfolio_value - self._day_start_value
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            self._halted = True
            self._sell_all(now, f"日內熔斷: 虧損 ${abs(daily_pnl):,.0f}")
            self.log_message(f"[HybridStrategy] 日內熔斷，停止交易")
            return

        # --- 執行當前模式 ---
        if self._mode == self.MODE_GRID:
            self._run_grid_mode(now, data, t, entry_cutoff)
        else:
            self._run_vwap_mode(now, data, t, entry_cutoff)

    # ------------------------------------------------------------------
    # 模式切換
    # ------------------------------------------------------------------
    def _switch_mode(self, now: datetime, new_mode: str):
        if self._has_position or any(not l["bought"] or l["qty"] > 0 for l in self._levels.values()):
            self._sell_all(now, f"模式切換 {self._mode}→{new_mode}")
        self._mode = new_mode
        # 重置兩引擎狀態
        self._base_price = 0.0
        self._spacing = 0.0
        self._levels = {}
        self._grid_halted = False
        self._has_position = False
        self._position_quantity = 0.0
        self._position_stage = 0
        self._entry_price = 0.0
        self.log_message(
            f"[HybridStrategy] 模式切換 → {new_mode} | ADX={self._adx:.1f}"
        )

    # ------------------------------------------------------------------
    # 一次載入全部 bar 資料（含 ADX / ATR）
    # ------------------------------------------------------------------
    def _load_bars(self, now: datetime) -> dict | None:
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
            "df5": None,   # 5 分鐘 K 線（VWAP 引擎用）
        }

        # 5 分鐘 K 線
        r5 = (
            df.resample(f"{RESAMPLE_MINUTES}min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
        )
        result["df5"] = r5
        if len(r5) >= ATR_PERIOD + 2:
            atr_s = compute_atr(r5["high"], r5["low"], r5["close"], period=ATR_PERIOD)
            v = atr_s.iloc[-1]
            if not pd.isna(v):
                result["atr"] = float(v)

        # ADX：15 分鐘 K 線
        r15 = (
            df.resample(f"15min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
        )
        if len(r15) >= ADX_PERIOD * 2 + 2:
            adx_s = compute_adx(r15["high"], r15["low"], r15["close"], period=ADX_PERIOD)
            v = adx_s.iloc[-1]
            if not pd.isna(v):
                result["adx"] = float(v)

        return result

    # ==================================================================
    # 引擎 A：網格模式
    # ==================================================================
    def _run_grid_mode(self, now: datetime, data: dict, t: time, entry_cutoff: time):
        price, low, high = data["price"], data["low"], data["high"]

        # 15:50 後禁止新建網格層
        if t >= entry_cutoff:
            self._grid_halted = True

        # 每日初始化網格
        if self._base_price <= 0:
            self._init_grid(data)

        # ADX 熔斷（ADX≥25 已在主流程切換模式；此處雙保險）
        if self._adx is not None and self._adx >= ADX_GRID_HALT_THRESHOLD:
            self._grid_halted = True

        # 先賣後買
        self._grid_check_sells(now, price, high)
        if not self._grid_halted:
            self._grid_check_buys(now, price, low)

    def _init_grid(self, data: dict):
        base = data.get("open") or data["price"]
        self._base_price = float(base)
        atr = data.get("atr")
        self._spacing = atr * GRID_SPACING_ATR if atr and atr > 0 else self._base_price * 0.005
        self._levels = {}
        for i in range(1, GRID_NUM_LEVELS + 1):
            buy_price = self._base_price - i * self._spacing
            self._levels[i] = {
                "bought": False, "qty": 0.0,
                "buy_price": buy_price, "sell_price": buy_price + self._spacing,
            }
        self.log_message(
            f"[HybridStrategy] [GRID] 網格建立 | 基準=${self._base_price:.2f} | "
            f"間距=${self._spacing:.2f}"
        )

    def _grid_check_buys(self, now: datetime, price: float, low: float):
        for i in sorted(self._levels.keys(), reverse=True):
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
                    f"[HybridStrategy] [GRID BUY] {now} | 層{i} @${price:.2f} qty={qty}"
                )

    def _grid_check_sells(self, now: datetime, price: float, high: float):
        for i in sorted(self._levels.keys()):
            level = self._levels[i]
            if not level["bought"] or level["qty"] <= 0:
                continue
            if high >= level["sell_price"] or price >= level["sell_price"]:
                qty = int(level["qty"])
                if qty <= 0:
                    continue
                order = self.create_order(TRADE_SYMBOL, qty, "sell")
                self.submit_order(order)
                level["qty"] = 0.0
                self._incr_trade()
                self.log_message(
                    f"[HybridStrategy] [GRID SELL] {now} | 層{i} @${price:.2f} qty={qty}"
                )

    # ==================================================================
    # 引擎 B：VWAP 回踩模式（含 Daily VWAP / RS / 回踩進場 / 出場）
    # ==================================================================
    def _run_vwap_mode(self, now: datetime, data: dict, t: time, entry_cutoff: time):
        r5 = data["df5"]
        if r5 is None or len(r5) < EMA_SLOW + 2:
            return

        price = data["price"]
        df = r5

        # --- 計算指標 ---
        indicators = {}
        indicators["mu_close"] = df["close"]
        indicators["mu_high"] = df["high"]
        indicators["mu_low"] = df["low"]
        indicators["mu_volume"] = df["volume"]
        indicators["mu_rsi"] = compute_rsi(df["close"], period=14)
        indicators["mu_atr"] = compute_atr(df["high"], df["low"], df["close"], period=ATR_PERIOD)
        indicators["mu_vwap"] = compute_rolling_vwap(df["close"], period=VWAP_LOOKBACK)
        indicators["mu_ema_slow"] = compute_ema(df["close"], period=EMA_SLOW)
        indicators["mu_volume_ma"] = df["volume"].rolling(window=10, min_periods=1).mean()
        indicators["mu_volume_surge"] = df["volume"] > (indicators["mu_volume_ma"] * VOLUME_SURGE_MULTIPLIER)
        bb = compute_bollinger_bands(df["close"], period=20, num_std=2.0)
        indicators["mu_bb_upper"] = bb["bb_upper"]
        indicators["mu_bb_middle"] = bb["bb_middle"]

        # Daily VWAP（當日累積）
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        pv = tp * df["volume"]
        daily_vwap = pv.cumsum() / df["volume"].cumsum().replace(0, np.nan)
        indicators["mu_daily_vwap"] = daily_vwap

        idx = -1
        mu_price = float(df["close"].iloc[idx])

        # --- 出場邏輯（優先） ---
        if self._has_position:
            exit_signals = check_exit_signals(
                price=float(df["close"].iloc[idx]),
                bb_upper=float(indicators["mu_bb_upper"].iloc[idx]),
                rsi=float(indicators["mu_rsi"].iloc[idx]),
                ema_slow=float(indicators["mu_ema_slow"].iloc[idx]),
                position_stage=self._position_stage,
            )
            for sig in exit_signals:
                if sig.action == "SELL_STOP":
                    self._sell_all(now, sig.reason)
                    self._incr_trade()
                elif sig.action == "SELL_TAKE_PROFIT":
                    positions = self.get_positions()
                    mp = next((p for p in positions if p.symbol == TRADE_SYMBOL), None)
                    if mp is not None:
                        qty = max(1, int(float(mp.quantity) * TAKE_PROFIT_RATIO))
                        self._sell_partial(qty, now, sig.reason)
                        self._incr_trade()
                        self._position_stage = 2

            # ATR 追蹤止損
            if self._has_position and self._entry_price > 0:
                atr = float(indicators["mu_atr"].iloc[idx])
                stop = self._entry_price - ATR_STOP_MULTIPLIER * atr
                if mu_price <= stop:
                    self._sell_all(now, f"ATR 追蹤止損: {mu_price:.2f} ≤ {stop:.2f}")
                    self._incr_trade()

        # --- 入場邏輯（無倉 或 底倉 stage=2 可加倉） ---
        can_enter = not self._has_position or self._position_stage == 2
        if can_enter and t < entry_cutoff and self._daily_trade_count < MAX_DAILY_TRADES:
            # Daily VWAP 多頭過濾（趨勢模式下價格須在當日 VWAP 之上）
            d_vwap = float(indicators["mu_daily_vwap"].iloc[idx])
            if pd.isna(d_vwap) or mu_price <= d_vwap:
                return
            # VWAP 回踩進場
            pullback_ok, reason = check_pullback_entry(
                price=mu_price,
                vwap=float(indicators["mu_vwap"].iloc[idx]),
                low=float(indicators["mu_low"].iloc[idx]),
                prev_close=float(df["close"].iloc[idx - 1]) if idx > 0 else mu_price,
                rsi=float(indicators["mu_rsi"].iloc[idx]),
                volume_surge=bool(indicators["mu_volume_surge"].iloc[idx]),
            )
            if not pullback_ok:
                return

            # 倉位
            capital = self.get_cash()
            atr = float(indicators["mu_atr"].iloc[idx])
            risk_amount = capital * 0.02
            stop_dist = ATR_STOP_MULTIPLIER * atr
            if stop_dist <= 0:
                return
            shares = int(risk_amount / stop_dist)
            max_shares = int(MAX_POSITION_VALUE / mu_price)
            shares = min(shares, max_shares)
            if shares <= 0:
                return

            order = self.create_order(TRADE_SYMBOL, shares, "buy")
            self.submit_order(order)
            self._incr_trade()
            self._has_position = True
            self._position_quantity += float(shares)
            self._position_stage = 1
            self._entry_price = mu_price
            self.log_message(
                f"[HybridStrategy] [VWAP BUY] {now} | ADX={self._adx:.1f} | "
                f"shares={shares} @${mu_price:.2f} | {reason}"
            )

    # ==================================================================
    # 共用：賣出
    # ==================================================================
    def _sell_all(self, now: datetime, reason: str):
        qty = 0.0
        positions = self.get_positions()
        for p in positions:
            if p.symbol == TRADE_SYMBOL and float(p.quantity) > 0:
                qty = float(p.quantity)
                break
        if qty <= 0:
            qty = self._position_quantity
        if qty <= 0:
            self._has_position = False
            self._position_stage = 0
            return
        order = self.create_order(TRADE_SYMBOL, qty, "sell")
        self.submit_order(order)
        self._has_position = False
        self._position_quantity = 0.0
        self._position_stage = 0
        self.log_message(f"[HybridStrategy] [SELL ALL] {now} | {reason} | qty={qty}")

    def _sell_partial(self, quantity: int, now: datetime, reason: str):
        if quantity <= 0:
            return
        order = self.create_order(TRADE_SYMBOL, quantity, "sell")
        self.submit_order(order)
        self._position_quantity = max(0.0, self._position_quantity - float(quantity))
        self.log_message(f"[HybridStrategy] [SELL 50%] {now} | {reason} | qty={quantity}")
