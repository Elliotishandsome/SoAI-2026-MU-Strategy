"""
風控管理模組 — MU 日內 AI 交易策略

集中管理所有風險約束，包括：
  - 倉位計算（固定風險 + ATR 自適應）
  - 單筆頭寸上限
  - 日內虧損熔斷
  - 單日交易次數限制
  - 交易時段管控
  - 強制清倉

設計為狀態機：每筆交易 / 每日開盤時更新內部狀態。
"""

from datetime import datetime, time
import math

# 數值常量從 params 導入，避免魔術數字
DEFAULT_MAX_RISK_RATIO = 0.02
DEFAULT_ATR_MULTIPLIER = 1.5
DEFAULT_MAX_POSITION_VALUE = 250_000
DEFAULT_DAILY_LOSS_LIMIT = 15_000
DEFAULT_MAX_DAILY_TRADES = 20
DEFAULT_TAKE_PROFIT_RATIO = 0.50


class RiskManager:
    """
    風險管理器。

    追蹤每日損益、交易次數，並根據 ATR 波動率動態計算倉位。

    Usage::

        rm = RiskManager(initial_capital=1_000_000)
        shares = rm.compute_position_size(capital, atr, price)
        if rm.can_trade(daily_pnl, trade_count):
            # proceed
        if rm.should_halt(daily_pnl):
            # stop trading for the day
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        max_risk_ratio: float = DEFAULT_MAX_RISK_RATIO,
        atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
        max_position_value: float = DEFAULT_MAX_POSITION_VALUE,
        daily_loss_limit: float = DEFAULT_DAILY_LOSS_LIMIT,
        max_daily_trades: int = DEFAULT_MAX_DAILY_TRADES,
        take_profit_ratio: float = DEFAULT_TAKE_PROFIT_RATIO,
    ):
        self.initial_capital = initial_capital
        self.max_risk_ratio = max_risk_ratio
        self.atr_multiplier = atr_multiplier
        self.max_position_value = max_position_value
        self.daily_loss_limit = daily_loss_limit
        self.max_daily_trades = max_daily_trades
        self.take_profit_ratio = take_profit_ratio

        # --- 每日重置狀態 ---
        self.reset_daily()

    def reset_daily(self) -> None:
        """每個交易日開始時呼叫，重置計數器。"""
        self._daily_pnl = 0.0
        self._daily_trade_count = 0
        self._day_start_value: float | None = None
        self._halted = False
        self._last_date: object = None  # date object 用於檢測換日

    def check_new_day(self, current_dt: datetime) -> None:
        """
        檢測是否進入新交易日，若是則自動重置。

        Args:
            current_dt: 當前 datetime（應帶時區資訊）。
        """
        current_date = current_dt.date()
        if self._last_date is not None and current_date != self._last_date:
            self.reset_daily()
        self._last_date = current_date

    # ------------------------------------------------------------------
    # 倉位計算
    # ------------------------------------------------------------------

    def compute_position_size(
        self, capital: float, atr: float, price: float
    ) -> int:
        """
        根據「固定風險 + ATR 自適應」模型計算買入股數。

        公式: shares = floor[(capital × max_risk_ratio) / (atr_multiplier × ATR)]

        Args:
            capital: 當前可用資金。
            atr: 當前 ATR(14) 值。
            price: 當前價格。

        Returns:
            int: 建議買入股數（已考慮頭寸上限）。
        """
        if atr <= 0 or price <= 0:
            return 0

        risk_amount = capital * self.max_risk_ratio          # e.g. $20,000
        stop_distance = self.atr_multiplier * atr             # e.g. 1.5 × ATR
        if stop_distance <= 0:
            return 0

        shares = risk_amount / stop_distance
        shares = math.floor(shares)

        # 頭寸市值上限
        max_shares_by_value = math.floor(self.max_position_value / price)
        shares = min(shares, max_shares_by_value)

        return max(0, shares)

    # ------------------------------------------------------------------
    # 交易許可檢查
    # ------------------------------------------------------------------

    def can_trade(self, daily_pnl: float, trade_count: int) -> tuple[bool, str]:
        """
        檢查當前是否允許開新倉。

        Args:
            daily_pnl: 當日累計盈虧 (USD)。
            trade_count: 當日已交易次數。

        Returns:
            (allowed: bool, reason: str)
        """
        if self._halted:
            return False, "已觸發熔斷，當日停止交易"

        if abs(daily_pnl) >= self.daily_loss_limit and daily_pnl < 0:
            self._halted = True
            return False, f"日內虧損已達上限 ${self.daily_loss_limit:,.0f}"

        if trade_count >= self.max_daily_trades:
            return False, f"已達單日交易次數上限 {self.max_daily_trades}"

        return True, ""

    def should_halt(self, daily_pnl: float) -> bool:
        """檢查是否應觸發熔斷。"""
        if daily_pnl <= -self.daily_loss_limit:
            self._halted = True
            return True
        return False

    def is_halted(self) -> bool:
        return self._halted

    # ------------------------------------------------------------------
    # 時間約束
    # ------------------------------------------------------------------

    @staticmethod
    def is_no_trade_window(current_time: time) -> bool:
        """
        判斷是否在「僅監控不下單」時段（美東 9:30–10:00）。

        在日線級別回測中，此函數始終返回 False。
        """
        no_trade_start = time(9, 30)
        no_trade_end = time(10, 0)
        return no_trade_start <= current_time < no_trade_end

    @staticmethod
    def is_force_close_time(current_time: time) -> bool:
        """
        判斷是否到達強制清倉時間（美東 15:55）。

        在日線級別回測中，此函數始終返回 False。
        """
        force_close = time(15, 55)
        return current_time >= force_close

    # ------------------------------------------------------------------
    # 止盈計算
    # ------------------------------------------------------------------

    def compute_take_profit_quantity(self, current_quantity: int) -> int:
        """
        計算首段止盈應平倉數量（預設平 50%）。

        Args:
            current_quantity: 當前持倉股數。

        Returns:
            int: 應賣出股數。
        """
        return max(1, math.floor(current_quantity * self.take_profit_ratio))

    # ------------------------------------------------------------------
    # 狀態查詢
    # ------------------------------------------------------------------

    @property
    def daily_trade_count(self) -> int:
        return self._daily_trade_count

    def increment_trade_count(self) -> None:
        self._daily_trade_count += 1
