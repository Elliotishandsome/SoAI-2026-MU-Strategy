"""
SoAI 2026 AI Algorithmic Trading Competition — MU 日內 AI 策略

三層決策架構：
  1. 母系統 — 大盤與板塊情緒過濾（SMH VWAP + VIX 急升檢測）
  2. 核心系統 — 個股相對強弱（RS Ratio = MU/SMH vs. EMA）
  3. 子系統 — 微觀執行觸發（價格突破 VWAP + RSI + 放量）

風控約束：
  - 固定風險 + ATR 自適應倉位模型
  - 單筆頭寸上限 $250,000
  - 日內虧損熔斷 $15,000
  - 單日最多 20 筆交易
  - 開盤 30 分鐘僅監控，收盤前 5 分鐘強制清倉（日線模式自動停用）

支援兩種執行模式：
  - 日線模式（Yahoo backtest）: sleeptime = "1D"
  - 分鐘模式（Pandas backtest）: sleeptime = "5M"

模組化結構:
  strategies/params.py           — 所有可調參數
  strategies/indicators.py       — 技術指標計算（純函數）
  strategies/risk_manager.py     — 風控管理（倉位 + 熔斷 + 時間約束）
  strategies/signal_generator.py — 三層訊號生成
  strategies/strategy.py         — 本檔案：主策略類（Lumibot 入口）
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from lumibot.strategies import Strategy as _LumibotStrategy

from strategies.params import (
    TRADE_SYMBOL,
    TRADE_SYMBOLS,
    SECTOR_ETF,
    VOLATILITY_INDEX,
    BENCHMARK,
    INITIAL_CAPITAL,
    SLEEPTIME,
    RESAMPLE_MINUTES,
    REQUIRE_SMH,
    LAST_ENTRY_TIME_HOUR,
    LAST_ENTRY_TIME_MINUTE,
    BUY_COMMISSION_BPS,
    SELL_COMMISSION_BPS,
    MAX_RISK_RATIO,
    ATR_STOP_MULTIPLIER,
    MAX_POSITION_VALUE,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_TRADES,
    TAKE_PROFIT_RATIO,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_PERIOD,
    ATR_PERIOD,
    VWAP_LOOKBACK,
    EMA_SLOW,
    RS_EMA_PERIOD,
    BB_PERIOD,
    BB_STD_MULTIPLIER,
    VOLUME_MA_PERIOD,
    VOLUME_SURGE_MULTIPLIER,
)
from strategies.indicators import (
    compute_rsi,
    compute_atr,
    compute_rolling_vwap,
    compute_ema,
    compute_bollinger_bands,
    compute_rs_ratio,
    compute_rs_ratio_ema,
    compute_volume_ma,
)
from strategies.risk_manager import RiskManager
from strategies.signal_generator import (
    Signal,
    HOLD,
    check_relative_strength,
    check_vwap_break,
    check_pullback_entry,
    check_exit_signals,
)


class Strategy(_LumibotStrategy):
    """
    MU 日內 AI 交易策略 — 三層決策 + 自適應風控。

    支援日線（Yahoo）和分鐘級（Pandas）兩種回測模式。
    透過 self.intraday_mode 自動適配指標計算邏輯。
    """

    # ------------------------------------------------------------------
    # Lifecycle: setup
    # ------------------------------------------------------------------
    def initialize(self):
        """
        策略初始化 — 設定頻率、標的、風控參數。

        可透過調整 sleeptime 切換模式：
          "1D"  = 日線回測（Yahoo）
          "5M"  = 5 分鐘回測（Pandas CSV）
          "1M"  = 1 分鐘回測（Pandas CSV）
        """
        # --- 交易頻率（由 params.SLEEPTIME 控制） ---
        self.sleeptime = SLEEPTIME

        # --- 判斷模式：日線 vs 分鐘級 ---
        self.intraday_mode = self.sleeptime in ("1M", "5M", "15M", "60M")
        # 美東時區（交易時段判斷用）
        self._ny_tz = ZoneInfo("America/New_York")

        # --- 風控管理器 ---
        self.risk_mgr = RiskManager(
            initial_capital=INITIAL_CAPITAL,
            max_risk_ratio=MAX_RISK_RATIO,
            atr_multiplier=ATR_STOP_MULTIPLIER,
            max_position_value=MAX_POSITION_VALUE,
            daily_loss_limit=DAILY_LOSS_LIMIT,
            max_daily_trades=MAX_DAILY_TRADES,
            take_profit_ratio=TAKE_PROFIT_RATIO,
        )

        # --- 策略狀態（v2.7：per-symbol） ---
        self._pos = {}
        for _s in TRADE_SYMBOLS:
            self._pos[_s] = {
                "has": False,     # 持倉標記
                "qty": 0.0,       # 追蹤股數（get_positions 兜底）
                "stage": 0,       # 0=空倉 / 1=全倉 / 2=底倉(已平50%)
                "entry": 0.0,     # 進場價
            }
        self._smh_available = False  # SMH 數據是否可用（不可用時降級模式）
        self._daily_pnl = 0.0
        self._indicators_cache: dict | None = None
        self._last_indicator_idx: int = -1

        # --- 標記是否已做首次計算 ---
        self._indicators_ready = False

        # --- 記錄每日起始組合淨值（用於計算當日盈虧） ---
        self._day_start_value = self.get_portfolio_value()

        self.log_message(
            f"[MultiSymbol Strategy] 初始化完成 | 模式={'intraday' if self.intraday_mode else 'daily'} | "
            f"sleeptime={self.sleeptime} | 標的={TRADE_SYMBOLS} | 初始資金=${INITIAL_CAPITAL:,.0f}"
        )

    # ------------------------------------------------------------------
    # Lifecycle: per-step decision making
    # ------------------------------------------------------------------
    def on_trading_iteration(self):
        """
        每次 sleeptime 觸發時執行（v2.7：多標的）。

        對 TRADE_SYMBOLS 中的每個標的獨立執行：
          1. 獲取市場數據（個股, SMH）
          2. 計算技術指標
          3. 檢查持倉 → 出場邏輯
          4. 檢查風控 → 入場邏輯
          5. 執行訂單
        """
        now = self.get_datetime()
        portfolio_value = self.get_portfolio_value()

        # --- 每日重置檢查 ---
        self.risk_mgr.check_new_day(now)
        if self.risk_mgr.is_halted():
            return  # 熔斷中，不做任何操作

        # --- SMH 板塊數據（各標的共用，一次獲取） ---
        smh_price = self.get_last_price(SECTOR_ETF)
        self._smh_available = smh_price is not None

        if not self._smh_available and REQUIRE_SMH:
            self.log_message(f"[{now}] SMH 數據缺失且 REQUIRE_SMH=True，停止交易")
            return

        lookback = self._get_lookback()
        if self.intraday_mode:
            smh_bars = self.get_historical_prices(SECTOR_ETF, length=lookback, timestep="minute") if self._smh_available else None
        else:
            smh_bars = self.get_historical_prices(SECTOR_ETF, length=lookback, timestep="day") if self._smh_available else None

        smh_df = self._prepare_bars_df(smh_bars.df) if (smh_bars is not None and smh_bars.df is not None) else pd.DataFrame()
        if smh_df.empty:
            self._smh_available = False

        # --- 對每個交易標的獨立決策 ---
        for symbol in TRADE_SYMBOLS:
            self._process_symbol(symbol, now, portfolio_value, smh_df)

    # ------------------------------------------------------------------
    # 單標的處理流程（多標的時逐一呼叫）
    # ------------------------------------------------------------------
    def _process_symbol(self, symbol: str, now: datetime, portfolio_value: float, smh_df: pd.DataFrame):
        """對單一 symbol 執行出場/入場決策（持倉狀態獨立管理）。"""
        price = self.get_last_price(symbol)
        if price is None:
            return

        lookback = self._get_lookback()
        if self.intraday_mode:
            bars = self.get_historical_prices(symbol, length=lookback, timestep="minute")
        else:
            bars = self.get_historical_prices(symbol, length=lookback, timestep="day")
        if bars is None or bars.df is None or bars.df.empty:
            return

        df = self._prepare_bars_df(bars.df)
        if df.empty:
            return

        indicators = self._compute_indicators(symbol, df, smh_df)
        self._indicators_cache = indicators
        self._indicators_ready = True
        idx = -1  # 最新一根 bar
        sym = symbol.lower()

        # --- Step 3: 檢查現有持倉的出場訊號 ---
        positions = self.get_positions()
        sym_position = next((p for p in positions if p.symbol == symbol), None)
        pos = self._pos[symbol]

        # 有倉位：以 get_positions 為主，pos["has"] 兜底（訂單結算延遲時仍能出場）
        has_position = (sym_position is not None and float(sym_position.quantity) > 0) or pos["has"]

        if has_position:
            exit_signals = check_exit_signals(
                price=indicators[f"{sym}_close"].iloc[idx],
                bb_upper=indicators[f"{sym}_bb_upper"].iloc[idx],
                rsi=indicators[f"{sym}_rsi"].iloc[idx],
                ema_slow=indicators[f"{sym}_ema_slow"].iloc[idx],
                position_stage=pos["stage"],
            )
            for sig in exit_signals:
                if sig.action == "SELL_STOP":
                    self._sell_all(symbol, now, sig.reason)
                    self.risk_mgr.increment_trade_count()
                elif sig.action == "SELL_TAKE_PROFIT":
                    if sym_position is not None:
                        qty = self.risk_mgr.compute_take_profit_quantity(int(float(sym_position.quantity)))
                        self._sell_partial(symbol, qty, now, sig.reason)
                        self.risk_mgr.increment_trade_count()
                        pos["stage"] = 2  # 平掉 50% 後進入底倉階段

            # 檢查止損（ATR 追蹤止損）
            if pos["has"] and pos["entry"] > 0:
                atr = indicators[f"{sym}_atr"].iloc[idx]
                stop_price = pos["entry"] - ATR_STOP_MULTIPLIER * atr
                if price <= stop_price:
                    self._sell_all(symbol, now, f"ATR 追蹤止損: 價格 {price:.2f} ≤ 止損價 {stop_price:.2f}")
                    self.risk_mgr.increment_trade_count()

            # 檢查強制清倉時間（僅分鐘模式，美東時區）
            if self.intraday_mode:
                now_ny = now.astimezone(self._ny_tz)
                if self.risk_mgr.is_force_close_time(now_ny.time()):
                    self._sell_all(symbol, now, "15:55 強制清倉")
                    self.risk_mgr.increment_trade_count()

            # 檢查日內熔斷
            daily_pnl = portfolio_value - self._day_start_value
            if self.risk_mgr.should_halt(daily_pnl):
                self._sell_all(symbol, now, f"日內熔斷: 虧損 ${abs(daily_pnl):,.0f}")
                self.log_message(f"[HALT] 日內虧損已達上限，停止交易")
                return

            # v2.4：底倉階段（stage=2，已平 50%）允許再開新倉
            if pos["stage"] != 2:
                return  # 全倉階段，不再開新倉

        # --- Step 4: 檢查入場條件 ---
        if sym_position is not None and float(sym_position.quantity) > 0 and pos["stage"] != 2:
            return

        # 交易許可檢查
        daily_pnl = portfolio_value - self._day_start_value
        can_trade, reason = self.risk_mgr.can_trade(daily_pnl, self.risk_mgr.daily_trade_count)
        if not can_trade:
            return

        # 時間窗口檢查（僅分鐘模式，美東時區：開盤 30 分鐘僅監控）
        if self.intraday_mode:
            now_ny = now.astimezone(self._ny_tz)
            if self.risk_mgr.is_no_trade_window(now_ny.time()):
                return

            # 15:50 後禁止開新倉
            last_entry = time(LAST_ENTRY_TIME_HOUR, LAST_ENTRY_TIME_MINUTE)
            if now_ny.time() >= last_entry:
                self.log_message(f"[{now}] {symbol} 已過最後開倉時間 {last_entry}，跳過")
                return

        # 三層決策
        trade_signal = self._run_three_layer_decision(symbol, indicators, idx)
        if trade_signal.action != "BUY":
            return

        # 計算倉位
        atr = indicators[f"{sym}_atr"].iloc[idx]
        capital = self.get_cash()
        shares = self.risk_mgr.compute_position_size(capital, atr, price)

        if shares <= 0:
            self.log_message(f"[{now}] {symbol} 倉位計算為 0，跳過")
            return

        # --- Step 5: 執行買入 ---
        order = self.create_order(symbol, shares, "buy")
        self.submit_order(order)
        self.risk_mgr.increment_trade_count()
        pos["has"] = True
        pos["qty"] += float(shares)
        # 底倉（stage=2）再加倉 → 回到全倉階段（stage=1）
        pos["stage"] = 1  # 全倉
        pos["entry"] = price

        self.log_message(
            f"[BUY] {now} | {symbol} | {trade_signal.reason} | "
            f"shares={shares} @ ${price:.2f} | "
            f"ATR={atr:.2f} | 資金=${capital:,.0f}"
        )

        # --- Step 6: 日誌 ---
        self.log_message(
            f"[Status] portfolio=${portfolio_value:,.2f} | "
            f"cash=${self.get_cash():,.2f} | "
            f"daily_trades={self.risk_mgr.daily_trade_count} | "
            f"daily_pnl=${daily_pnl:,.2f}"
        )

    # ------------------------------------------------------------------
    # 輔助：lookback 計算 & 數據整理
    # ------------------------------------------------------------------
    def _get_lookback(self) -> int:
        """
        計算歷史數據請求長度（bar 數）。

        日線模式：只需覆蓋最長指標週期。
        分鐘模式：指標在重取樣後的 K 線上計算，因此需保證
        重取樣後的 bar 數 ≥ 最長指標週期，並預留安全邊際。
        """
        if not self.intraday_mode:
            return max(
                RSI_PERIOD + 1,
                ATR_PERIOD + 1,
                BB_PERIOD + 1,
                VOLUME_MA_PERIOD + 1,
                RS_EMA_PERIOD + 1,
                EMA_SLOW + 1,
                30,  # 安全邊際
            )

        # 重取樣後需要的 K 線數（最長指標週期 × RESAMPLE_MINUTES + 安全邊際）
        need_resampled_bars = (EMA_SLOW + BB_PERIOD) * RESAMPLE_MINUTES + 100
        return need_resampled_bars * RESAMPLE_MINUTES

    def _prepare_bars_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        整理 Lumibot 返回的歷史 bar DataFrame。

        - 處理 MultiIndex 欄位
        - 確保 DatetimeIndex 且排序去重
        - 分鐘模式：重取樣為 RESAMPLE_MINUTES 分鐘 K 線
        """
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()
        # 處理 MultiIndex 欄位（例如 ('close', 'MU')）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        # 只保留 OHLCV
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if len(cols) < 5:
            return pd.DataFrame()
        df = df[cols].astype(float)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        df = df[~df.index.duplicated(keep="last")].sort_index()

        # 分鐘模式：重取樣為 K 線
        if self.intraday_mode and RESAMPLE_MINUTES > 1:
            df = (
                df.resample(f"{RESAMPLE_MINUTES}min", label="right", closed="right")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna(subset=["open", "close"])
            )

        return df

    # ------------------------------------------------------------------
    # 三層決策整合
    # ------------------------------------------------------------------
    def _run_three_layer_decision(self, symbol: str, indicators: dict, idx: int) -> Signal:
        """
        執行三層決策流程（v2 — 相對強弱制，v2.7 多標的）。

        三層架構：
          Layer 1 母系統 — RS 相對強弱（RS = 個股/SMH > RS_EMA）
          Layer 2 核心系統 — 價格突破 VWAP
          Layer 3 子系統 — RSI 動能 + 放量確認

        SMH 數據缺失時進入降級模式：
          - Layer 1 改用個股自身動能（價格 > 自身 VWAP）
          - RS_Ratio 無法計算時視為通過

        Args:
            symbol: 交易標的（如 "MU"、"AMD"）。
            indicators: 指標 dict（鍵前綴 = symbol.lower()）。
            idx: 當前 bar 索引。

        Returns:
            Signal: 最終交易訊號。
        """
        sym = symbol.lower()
        # ------------------------------------------------------------------
        # 大趨勢錨點：Daily VWAP（價格 > Daily VWAP = 今日多頭控盤）
        # ------------------------------------------------------------------
        close_here = indicators[f"{sym}_close"].iloc[idx]
        daily_vwap = indicators[f"{sym}_daily_vwap"].iloc[idx]
        if pd.isna(daily_vwap):
            return HOLD  # Daily VWAP 數據不足，保守不進場
        if close_here <= daily_vwap:
            return HOLD  # 價格在 Daily VWAP 之下 → 今日空頭控盤，買點作廢
        trend_reason = f"DailyVWAP多頭: {close_here:.2f} > 今日VWAP {daily_vwap:.2f}"

        # ------------------------------------------------------------------
        # Layer 1: 母系統 — 相對強弱（SMH 缺失 → 個股自我動能降級）
        # ------------------------------------------------------------------
        if self._smh_available:
            rs_ratio = indicators["rs_ratio"].iloc[idx]
            rs_ratio_ema = indicators["rs_ratio_ema"].iloc[idx]
            rs_ok, rs_reason = check_relative_strength(rs_ratio, rs_ratio_ema)
        else:
            # 降級：以個股自身價格 vs VWAP 作為替代動能檢查
            close_l1 = indicators[f"{sym}_close"].iloc[idx]
            vwap_l1 = indicators[f"{sym}_vwap"].iloc[idx]
            rs_ok, rs_reason = check_vwap_break(close_l1, vwap_l1)
            rs_reason += " [SMH 缺失，降級為個股自我動能]"

        if not rs_ok:
            return HOLD

        # ------------------------------------------------------------------
        # Layer 2: 核心系統 — 價格突破 VWAP
        # ------------------------------------------------------------------
        close_v = indicators[f"{sym}_close"].iloc[idx]
        vwap = indicators[f"{sym}_vwap"].iloc[idx]
        vwap_ok, vwap_reason = check_vwap_break(close_v, vwap)
        if not vwap_ok:
            return HOLD

        # ------------------------------------------------------------------
        # Layer 3: 子系統 — VWAP 回踩 + 動能 + 量能確認
        # ------------------------------------------------------------------
        rsi = indicators[f"{sym}_rsi"].iloc[idx]
        low = indicators[f"{sym}_low"].iloc[idx]
        volume_surge = bool(indicators[f"{sym}_volume_surge"].iloc[idx])
        prev_close = indicators[f"{sym}_close"].iloc[idx - 1] if idx > 0 else close_v

        pullback_ok, pullback_reason = check_pullback_entry(
            close_v, vwap, low, prev_close, rsi, volume_surge
        )
        if not pullback_ok:
            return HOLD

        return Signal(
            "BUY",
            f"三層全過: {trend_reason} | {rs_reason} | {vwap_reason} | {pullback_reason}",
            confidence=0.85,
        )

    # ------------------------------------------------------------------
    # 指標計算
    # ------------------------------------------------------------------
    def _compute_indicators(self, symbol: str, mu_df: pd.DataFrame, smh_df: pd.DataFrame) -> dict:
        """
        計算所有技術指標，返回 dict（鍵前綴 = symbol.lower()）。

        Args:
            symbol: 交易標的（如 "MU"、"AMD"）。
            mu_df: 該標的的 OHLCV DataFrame，index 為 datetime。
            smh_df: SMH 的 OHLCV DataFrame（可空 → 降級模式）。
        """
        indicators = {}
        sym = symbol.lower()

        close = mu_df["close"]
        high = mu_df["high"]
        low = mu_df["low"]
        volume = mu_df["volume"]

        # 原始數據
        indicators[f"{sym}_close"] = close
        indicators[f"{sym}_high"] = high
        indicators[f"{sym}_low"] = low
        indicators[f"{sym}_volume"] = volume

        # 技術指標
        indicators[f"{sym}_rsi"] = compute_rsi(close, period=RSI_PERIOD)
        indicators[f"{sym}_rsi_prev"] = indicators[f"{sym}_rsi"].shift(1)
        indicators[f"{sym}_atr"] = compute_atr(high, low, close, period=ATR_PERIOD)
        indicators[f"{sym}_vwap"] = compute_rolling_vwap(close, period=VWAP_LOOKBACK)
        indicators[f"{sym}_ema_fast"] = compute_ema(close, period=5)
        indicators[f"{sym}_ema_slow"] = compute_ema(close, period=EMA_SLOW)
        indicators[f"{sym}_volume_ma"] = compute_volume_ma(volume, period=VOLUME_MA_PERIOD)
        indicators[f"{sym}_volume_surge"] = volume > (indicators[f"{sym}_volume_ma"] * VOLUME_SURGE_MULTIPLIER)

        bb = compute_bollinger_bands(close, period=BB_PERIOD, num_std=BB_STD_MULTIPLIER)
        indicators[f"{sym}_bb_upper"] = bb["bb_upper"]
        indicators[f"{sym}_bb_lower"] = bb["bb_lower"]
        indicators[f"{sym}_bb_middle"] = bb["bb_middle"]

        # ---- 大趨勢錨點：Daily VWAP（每日開盤重置） ----
        if self.intraday_mode:
            idx = mu_df.index
            if idx.tz is None:
                local_idx = idx.tz_localize("UTC").tz_convert(self._ny_tz)
            else:
                local_idx = idx.tz_convert(self._ny_tz)

            typical_price = (mu_df["high"] + mu_df["low"] + mu_df["close"]) / 3.0
            pv = typical_price * mu_df["volume"]

            cum = pd.DataFrame(
                {"pv": pv.values, "v": mu_df["volume"].values},
                index=mu_df.index,
            )
            cum["day"] = local_idx.date  # 美東交易日

            cum_pv = cum.groupby("day")["pv"].cumsum()
            cum_v = cum.groupby("day")["v"].cumsum()
            daily_vwap = cum_pv / cum_v.replace(0, np.nan)

            indicators[f"{sym}_daily_vwap"] = daily_vwap.reindex(mu_df.index)
        else:
            indicators[f"{sym}_daily_vwap"] = None

        # SMH 原始數據與指標（SMH 缺失時進入降級模式，rs_ratio 設為 None）
        if smh_df is not None and not smh_df.empty:
            smh_close = smh_df["close"]
            indicators["smh_close"] = smh_close
            indicators["smh_vwap"] = compute_rolling_vwap(smh_close, period=VWAP_LOOKBACK)

            # RS Ratio（個股 / SMH）
            indicators["rs_ratio"] = compute_rs_ratio(close, smh_close)
            indicators["rs_ratio_ema"] = compute_rs_ratio_ema(indicators["rs_ratio"], period=RS_EMA_PERIOD)
        else:
            indicators["smh_close"] = None
            indicators["smh_vwap"] = None
            indicators["rs_ratio"] = None
            indicators["rs_ratio_ema"] = None

        return indicators

    # ------------------------------------------------------------------
    # 訂單輔助方法
    # ------------------------------------------------------------------
    def _sell_all(self, symbol: str, now, reason: str) -> None:
        """
        賣出指定 symbol 的全部持倉，並重置持倉狀態機。

        優先使用 get_positions() 的實際數量；若拿不到（訂單結算延遲等），
        以 self._pos[symbol]["qty"] 追蹤值兜底，確保 15:55 強制清倉萬無一失。
        """
        pos = self._pos[symbol]
        # 以追蹤值 pos["qty"] 為準（即時扣減，不受訂單結算延遲影響）；
        # 避免 SELL 50% 後 get_positions() 尚未更新而重複賣出（賣超）。
        qty = pos["qty"]

        if qty <= 0:
            # 追蹤值缺失時，從 get_positions 取得實際數量兜底
            positions = self.get_positions()
            for p in positions:
                if p.symbol == symbol and float(p.quantity) > 0:
                    qty = float(p.quantity)
                    break

        if qty <= 0:
            # 無倉位可賣，僅重置狀態
            pos["has"] = False
            pos["stage"] = 0
            return

        order = self.create_order(symbol, qty, "sell")
        self.submit_order(order)
        pos["has"] = False
        pos["qty"] = 0.0
        pos["stage"] = 0  # 空倉
        self.log_message(f"[SELL ALL] {now} | {symbol} | {reason} | qty={qty}")

    def _sell_partial(self, symbol: str, quantity: int, now, reason: str) -> None:
        """賣出指定 symbol 的部分持倉。"""
        if quantity <= 0:
            return
        order = self.create_order(symbol, quantity, "sell")
        self.submit_order(order)
        self._pos[symbol]["qty"] = max(0.0, self._pos[symbol]["qty"] - float(quantity))
        self.log_message(f"[SELL 50%] {now} | {symbol} | {reason} | qty={quantity}")
