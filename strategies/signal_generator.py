"""
訊號生成模組 — MU 日內 AI 交易策略的「三層決策架構」

三層架構（v2 — 相對強弱制）：
  1. 母系統 — 個股相對強弱 (RS Ratio)：RS = MU / SMH
     只要 MU 比半導體板塊更強（RS > RS_EMA），即使大盤盤整也可做多。
     摒棄「SMH > SMH VWAP」式的硬性開關（大盤不敏感且過度保守）。
  2. 核心系統 — 價格突破：MU 價格 > MU VWAP
  3. 子系統 — 微觀執行觸發：RSI 動能 + 放量確認

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
# Layer 1: 母系統 — 相對強弱 (RS Ratio)
# ============================================================================

def check_relative_strength(
    rs_ratio: float,
    rs_ratio_ema: float,
) -> tuple[bool, str]:
    """
    母系統過濾器：MU 是否跑贏半導體板塊（相對強弱，非硬性開關）。

    RS_Ratio = MU_price / SMH_price
    只要 RS_Ratio > RS_EMA → MU 比板塊強，允許做多；
    即使大盤/SMH 盤整，只要 MU 相對更強即可入場。

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

    return True, f"RS_Ratio {rs_ratio:.4f} > EMA {rs_ratio_ema:.4f} — MU 強於板塊"


# ============================================================================
# Layer 2: 核心系統 — 價格突破 VWAP
# ============================================================================

def check_vwap_break(
    price: float,
    vwap: float,
) -> tuple[bool, str]:
    """
    核心系統：價格向上突破 VWAP。

    Args:
        price: MU 當前價格。
        vwap: MU VWAP。

    Returns:
        (triggered: bool, reason: str)
    """
    if pd.isna(price) or pd.isna(vwap):
        return False, "價格或 VWAP 數據缺失"

    if price <= vwap:
        return False, f"價格 {price:.2f} ≤ VWAP {vwap:.2f}"

    return True, f"價格 {price:.2f} > VWAP {vwap:.2f}"


# ============================================================================
# Layer 3: 子系統 — 動能 + 量能確認
# ============================================================================

def check_momentum_confirm(
    rsi: float,
    volume_surge: bool,
) -> tuple[bool, str]:
    """
    子系統：RSI 動能確認 + 成交量放大。

    Args:
        rsi: 當前 RSI(14)。
        volume_surge: 是否放量（> 10 期均量 × 1.5）。

    Returns:
        (triggered: bool, reason: str)
    """
    if pd.isna(rsi):
        return False, "RSI 數據缺失"

    if rsi <= 40:
        return False, f"RSI {rsi:.1f} ≤ 40"

    if not volume_surge:
        return False, "成交量未放大"

    return True, f"RSI={rsi:.1f} 動能確認, 放量確認"


# ============================================================================
# 相容性包裝：check_entry_signal（原 Layer 2+3 合併版）
# ============================================================================

def check_entry_signal(
    price: float,
    vwap: float,
    rsi: float,
    rsi_prev: float,
    volume_surge: bool,
) -> tuple[bool, str]:
    """
    子系統買入觸發條件（相容 v1 呼叫介面）。

    條件：
      1. MU 價格 > VWAP（價格向上突破）
      2. RSI(14) > 40（動能復甦）
      3. 成交量 > 10 期均量 × 1.5（放量確認）

    Args:
        price: MU 當前價格。
        vwap: MU VWAP。
        rsi: 當前 RSI。
        rsi_prev: 前一根 K 線的 RSI（保留相容，v2 不使用）。
        volume_surge: 是否放量。

    Returns:
        (triggered: bool, reason: str)
    """
    vwap_ok, vwap_reason = check_vwap_break(price, vwap)
    if not vwap_ok:
        return False, vwap_reason

    momentum_ok, momentum_reason = check_momentum_confirm(rsi, volume_surge)
    if not momentum_ok:
        return False, momentum_reason

    return True, f"買入觸發: {vwap_reason}, {momentum_reason}"


# ============================================================================
# Exit signals — 止盈 / 止損
# ============================================================================

def check_exit_signals(
    price: float,
    bb_upper: float,
    rsi: float,
    ema_slow: float,
    position_stage: int = 1,
) -> list[Signal]:
    """
    出場狀態機（同一根 bar 最多回傳一個訊號）。

    狀態定義:
        position_stage = 1  → 全倉
        position_stage = 2  → 底倉（已平倉 50%）

    規則:
      - 全倉 (stage=1):
          若 RSI(14) > 70 或 價格觸及布林帶上軌 → 平倉 50% 鎖利 (SELL_TAKE_PROFIT)
          若 價格跌破 20-EMA → 全數清倉 (SELL_STOP)（保命優先）
      - 底倉 (stage=2):
          若 價格跌破 20-EMA → 全數清倉 (SELL_STOP)

    兩個止盈條件以 ``or`` 合併，避免同一根 bar 觸發兩次 50% 平倉。

    Args:
        price: 當前價格。
        bb_upper: 布林帶上軌。
        rsi: 當前 RSI。
        ema_slow: 20-EMA。
        position_stage: 當前持倉階段（1=全倉, 2=底倉）。

    Returns:
        list[Signal]: 最多一個出場訊號；無觸發時返回空列表。
    """
    signals: list[Signal] = []

    # --- 跌破 20-EMA 一律全清（優先於止盈） ---
    if price <= ema_slow:
        stage_label = "底倉" if position_stage == 2 else "全倉"
        signals.append(
            Signal(
                "SELL_STOP",
                f"{stage_label}價格 {price:.2f} 跌破 20-EMA {ema_slow:.2f} — 全數清倉",
                confidence=1.0,
            )
        )
        return signals

    # --- 僅全倉階段檢查止盈（or 合併，只平 50%） ---
    if position_stage == 1:
        hit_upper = not pd.isna(bb_upper) and price >= bb_upper
        rsi_overheat = not pd.isna(rsi) and rsi > 70
        if hit_upper or rsi_overheat:
            reasons = []
            if hit_upper:
                reasons.append(f"價格 {price:.2f} 觸及布林帶上軌 {bb_upper:.2f}")
            if rsi_overheat:
                reasons.append(f"RSI {rsi:.1f} > 70 過熱")
            signals.append(
                Signal(
                    "SELL_TAKE_PROFIT",
                    f"{' 或 '.join(reasons)} — 平倉 50% 鎖利",
                    confidence=0.8,
                )
            )

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

    # --- Layer 1: 母系統 — 相對強弱 (RS = MU/SMH) ---
    rs_ratio = _get("rs_ratio", np.nan)
    rs_ratio_ema = _get("rs_ratio_ema", np.nan)

    rs_ok, rs_reason = check_relative_strength(rs_ratio, rs_ratio_ema)
    if not rs_ok:
        return [HOLD]  # MU 未跑贏板塊，否決

    # --- Layer 2: 核心系統 — 價格突破 VWAP ---
    price = _get("mu_close", np.nan)
    vwap = _get("mu_vwap", np.nan)

    vwap_ok, vwap_reason = check_vwap_break(price, vwap)
    if not vwap_ok:
        return [HOLD]

    # --- Layer 3: 子系統 — 動能 + 量能確認 ---
    rsi = _get("mu_rsi", np.nan)
    volume_surge = bool(_get("mu_volume_surge", False))

    momentum_ok, momentum_reason = check_momentum_confirm(rsi, volume_surge)
    if momentum_ok:
        signals.append(
            Signal(
                "BUY",
                f"三層全過: {rs_reason} | {vwap_reason} | {momentum_reason}",
                confidence=0.85,
            )
        )

    # --- 出場檢查（僅在有持倉時呼叫方檢查） ---
    # 出場訊號由 strategy.py 在持有倉位時獨立檢查

    if not signals:
        signals.append(HOLD)

    return signals


def check_exit_for_position(indicators: dict, idx: int, position_stage: int = 1) -> list[Signal]:
    """
    僅檢查出場條件（用於已持倉時）。

    Args:
        indicators: compute_all_indicators() 輸出。
        idx: 當前時間索引。
        position_stage: 持倉階段（1=全倉, 2=底倉）。

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

    exit_signals = check_exit_signals(price, bb_upper, rsi, ema_slow, position_stage=position_stage)
    return exit_signals if exit_signals else [HOLD]
