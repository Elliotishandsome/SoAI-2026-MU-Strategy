"""
訊號生成模組 — MU 日內 AI 交易策略的「三層決策架構」

三層架構：
  1. 母系統 — 大盤與板塊情緒過濾
  2. 核心系統 — 個股相對強弱 (RS Ratio)
  3. 子系統 — 微觀執行觸發 (價格突破 + RSI + 成交量)

所有函數為純函數，輸入指標 dict，輸出訊號 dict。
"""

import pandas as pd
import numpy as np


# ============================================================================
# 訊號類型定義
# ============================================================================

class Signal:
    """
    交易訊號。

    Attributes:
        action: "BUY" / "SELL_TAKE_PROFIT" / "SELL_STOP" / "HOLD"
        reason: 觸發原因的文字描述
        confidence: 訊號強度 (0.0 – 1.0)
    """
    __slots__ = ("action", "reason", "confidence")

    def __init__(self, action: str, reason: str = "", confidence: float = 0.0):
        self.action = action
        self.reason = reason
        self.confidence = confidence

    def __repr__(self) -> str:
        return f"Signal({self.action!r}, reason={self.reason!r}, confidence={self.confidence:.2f})"


HOLD = Signal("HOLD", "無交易訊號")

# ============================================================================
# Layer 1: 母系統 — 大盤與板塊情緒過濾
# ============================================================================

def check_market_sentiment(
    smh_close: float,
    smh_vwap: float,
    vix_spike: bool = False,
) -> tuple[bool, str]:
    """
    母系統過濾器。

    條件:
      1. SMH 價格 > SMH VWAP → 板塊看多，允許做多 MU。
      2. VIX ATR 急升 > 3% → 恐慌狀態，暫停開倉。

    Args:
        smh_close: SMH 當前收盤價。
        smh_vwap: SMH 滾動 VWAP。
        vix_spike: VIX 是否處於恐慌急升狀態。

    Returns:
        (approved: bool, reason: str)
    """
    if vix_spike:
        return False, "VIX 急升，市場恐慌 — 暫停開倉"

    if pd.isna(smh_close) or pd.isna(smh_vwap):
        return False, "SMH 數據缺失"

    if smh_close <= smh_vwap:
        return False, f"SMH {smh_close:.2f} 低於 VWAP {smh_vwap:.2f} — 板塊偏弱"

    return True, "板塊情緒正常"


# ============================================================================
# Layer 2: 核心系統 — 相對強弱 (RS Ratio)
# ============================================================================

def check_relative_strength(
    rs_ratio: float,
    rs_ratio_ema: float,
) -> tuple[bool, str]:
    """
    核心系統：檢查 MU 是否跑贏板塊。

    RS_Ratio = MU_price / SMH_price
    若 RS_Ratio > 10-period EMA → MU 跑贏，允許做多。

    Args:
        rs_ratio: 當前 RS Ratio。
        rs_ratio_ema: RS Ratio 的 EMA。

    Returns:
        (approved: bool, reason: str)
    """
    if pd.isna(rs_ratio) or pd.isna(rs_ratio_ema):
        return False, "RS Ratio 數據缺失"

    if rs_ratio <= rs_ratio_ema:
        return False, f"RS_Ratio {rs_ratio:.4f} ≤ EMA {rs_ratio_ema:.4f} — MU 未跑贏板塊"

    return True, f"RS_Ratio {rs_ratio:.4f} > EMA {rs_ratio_ema:.4f} — MU 跑贏板塊"


# ============================================================================
# Layer 3: 子系統 — 微觀執行觸發
# ============================================================================

def check_entry_signal(
    price: float,
    vwap: float,
    rsi: float,
    rsi_prev: float,
    volume_surge: bool,
) -> tuple[bool, str]:
    """
    子系統買入觸發條件。

    條件：
      1. MU 價格 > VWAP（價格向上突破）
      2. RSI(14) > 40（動能復甦）
      3. 成交量 > 10 期均量 × 1.5（放量確認）

    Args:
        price: MU 當前價格。
        vwap: MU VWAP。
        rsi: 當前 RSI。
        rsi_prev: 前一根 K 線的 RSI（用於判斷突破）。
        volume_surge: 是否放量。

    Returns:
        (triggered: bool, reason: str)
    """
    if pd.isna(price) or pd.isna(vwap):
        return False, "價格或 VWAP 數據缺失"

    if price <= vwap:
        return False, f"價格 {price:.2f} ≤ VWAP {vwap:.2f}"

    if rsi <= 40:
        return False, f"RSI {rsi:.1f} ≤ 40"

    if not volume_surge:
        return False, "成交量未放大"

    return True, f"買入觸發: 價格>{vwap:.2f} VWAP, RSI={rsi:.1f}, 放量確認"


# ============================================================================
# Exit signals — 止盈 / 止損
# ============================================================================

def check_exit_signals(
    price: float,
    bb_upper: float,
    rsi: float,
    ema_slow: float,
) -> list[Signal]:
    """
    檢查所有出場條件。

    出場規則:
      1. RSI(14) > 70 → 動能過熱，平倉 50% 鎖利。
      2. 價格觸及布林帶上軌 → 平倉 50%。
      3. 價格跌破 20-EMA → 清倉剩餘頭寸。

    Args:
        price: 當前價格。
        bb_upper: 布林帶上軌。
        rsi: 當前 RSI。
        ema_slow: 20-EMA。

    Returns:
        list[Signal]: 出場訊號列表（可能為空）。
    """
    signals = []

    if price <= ema_slow:
        signals.append(Signal("SELL_STOP", f"價格 {price:.2f} 跌破 20-EMA {ema_slow:.2f} — 全數清倉", confidence=1.0))

    if rsi > 70:
        signals.append(Signal("SELL_TAKE_PROFIT", f"RSI {rsi:.1f} > 70 過熱 — 平倉 50% 鎖利", confidence=0.8))

    if price >= bb_upper:
        signals.append(Signal("SELL_TAKE_PROFIT", f"價格 {price:.2f} 觸及布林帶上軌 {bb_upper:.2f} — 平倉 50%", confidence=0.7))

    return signals


# ============================================================================
# 三層整合 — 一次產出最終交易決策
# ============================================================================

def generate_signals(indicators: dict, idx: int) -> list[Signal]:
    """
    執行完整三層決策流程，產出交易訊號。

    Args:
        indicators: compute_all_indicators() 的輸出 dict。
        idx: 當前時間點的整數索引。

    Returns:
        list[Signal]: 交易訊號列表。
    """
    signals: list[Signal] = []

    # 安全取值，避免 KeyError / IndexError
    def _get(key: str, default=np.nan):
        series = indicators.get(key)
        if series is None or idx >= len(series):
            return default
        val = series.iloc[idx]
        return val if not pd.isna(val) else default

    # --- Layer 1: 母系統 ---
    smh_close = _get("smh_close", np.nan)
    smh_vwap = _get("smh_vwap", np.nan)
    vix_spike = bool(_get("vix_spike", False))

    sentiment_ok, sentiment_reason = check_market_sentiment(smh_close, smh_vwap, vix_spike)
    if not sentiment_ok:
        return [HOLD]  # 母系統否決，不進行後續判斷

    # --- Layer 2: 核心系統 ---
    rs_ratio = _get("rs_ratio", np.nan)
    rs_ratio_ema = _get("rs_ratio_ema", np.nan)

    rs_ok, rs_reason = check_relative_strength(rs_ratio, rs_ratio_ema)
    if not rs_ok:
        return [HOLD]  # RS 否決

    # --- Layer 3: 子系統 ---
    price = _get("mu_close", np.nan)
    vwap = _get("mu_vwap", np.nan)
    rsi = _get("mu_rsi", np.nan)
    rsi_prev = _get("mu_rsi_prev", np.nan)
    volume_surge = bool(_get("mu_volume_surge", False))

    entry_ok, entry_reason = check_entry_signal(price, vwap, rsi, rsi_prev, volume_surge)
    if entry_ok:
        signals.append(Signal("BUY", f"三層全過: {sentiment_reason} | {rs_reason} | {entry_reason}", confidence=0.85))

    # --- 出場檢查（僅在有持倉時呼叫方檢查） ---
    # 出場訊號由 strategy.py 在持有倉位時獨立檢查

    if not signals:
        signals.append(HOLD)

    return signals


def check_exit_for_position(indicators: dict, idx: int) -> list[Signal]:
    """
    僅檢查出場條件（用於已持倉時）。

    Args:
        indicators: compute_all_indicators() 輸出。
        idx: 當前時間索引。

    Returns:
        list[Signal]: 出場訊號。
    """
    def _get(key: str, default=np.nan):
        series = indicators.get(key)
        if series is None or idx >= len(series):
            return default
        val = series.iloc[idx]
        return val if not pd.isna(val) else default

    price = _get("mu_close", np.nan)
    bb_upper = _get("mu_bb_upper", np.nan)
    rsi = _get("mu_rsi", np.nan)
    ema_slow = _get("mu_ema_slow", np.nan)

    exit_signals = check_exit_signals(price, bb_upper, rsi, ema_slow)
    return exit_signals if exit_signals else [HOLD]
