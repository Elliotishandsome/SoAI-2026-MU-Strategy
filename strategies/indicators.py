"""
技術指標計算模組 — MU 日內 AI 交易策略

所有函數均以純 pandas Series/DataFrame 進行計算，無副作用、無狀態。
設計為可獨立測試、可替換的純函數集合。

支援的指標：
  - RSI (Relative Strength Index)
  - ATR (Average True Range)
  - VWAP / Rolling VWAP (日內用標準 VWAP；日線模式用收盤價均線近似)
  - Bollinger Bands
  - EMA (Exponential Moving Average)
  - RS Ratio (相對強弱比)
  - Volume MA (成交量移動平均)
"""

import pandas as pd
import numpy as np


# ============================================================================
# RSI — Relative Strength Index
# ============================================================================

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    計算 RSI（Wilder's smoothing）。

    Args:
        close: 收盤價序列。
        period: RSI 週期，預設 14。

    Returns:
        pd.Series: RSI 值 (0–100)，前 period 個值為 NaN。
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


# ============================================================================
# ATR — Average True Range
# ============================================================================

def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """
    計算 ATR（Average True Range）。

    Args:
        high: 最高價序列。
        low: 最低價序列。
        close: 收盤價序列。
        period: ATR 週期，預設 14。

    Returns:
        pd.Series: ATR 值。
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = true_range.ewm(alpha=1 / period, adjust=False).mean()
    return atr


# ============================================================================
# VWAP — Volume-Weighted Average Price
# ============================================================================

def compute_vwap(df: pd.DataFrame, price_col: str = "close", volume_col: str = "volume") -> pd.Series:
    """
    計算累積 VWAP（日內模式使用）。

    從每個交易日的開盤開始重新累積。

    Args:
        df: 含 price 與 volume 的 DataFrame，index 為 datetime。
        price_col: 價格欄位名。
        volume_col: 成交量欄位名。

    Returns:
        pd.Series: 累積 VWAP 值。
    """
    typical_price = df[price_col]
    cum_pv = (typical_price * df[volume_col]).groupby(df.index.date).cumsum()
    cum_vol = df[volume_col].groupby(df.index.date).cumsum()
    vwap = cum_pv / cum_vol.replace(0, np.nan)
    return vwap


def compute_rolling_vwap(close: pd.Series, period: int = 5) -> pd.Series:
    """
    滾動近似 VWAP — 用於日線模式。

    日線級別無法取得真正的 VWAP，以收盤價的簡單移動平均作為近似。

    Args:
        close: 收盤價序列。
        period: 回看週期。

    Returns:
        pd.Series: 滾動均價。
    """
    return close.rolling(window=period, min_periods=1).mean()


# ============================================================================
# Bollinger Bands
# ============================================================================

def compute_bollinger_bands(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    """
    計算布林帶。

    Args:
        close: 收盤價序列。
        period: 移動平均週期。
        num_std: 標準差倍數。

    Returns:
        pd.DataFrame: 包含 middle / upper / lower 三欄。
    """
    middle = close.rolling(window=period, min_periods=1).mean()
    std = close.rolling(window=period, min_periods=1).std()
    upper = middle + num_std * std
    lower = middle - num_std * std

    return pd.DataFrame({"bb_middle": middle, "bb_upper": upper, "bb_lower": lower}, index=close.index)


# ============================================================================
# EMA — Exponential Moving Average
# ============================================================================

def compute_ema(close: pd.Series, period: int) -> pd.Series:
    """
    計算指數移動平均。

    Args:
        close: 收盤價序列。
        period: EMA 週期。

    Returns:
        pd.Series: EMA 值。
    """
    return close.ewm(span=period, adjust=False).mean()


# ============================================================================
# RS Ratio — 相對強弱比
# ============================================================================

def compute_rs_ratio(
    mu_close: pd.Series, smh_close: pd.Series
) -> pd.Series:
    """
    計算 MU / SMH 相對強弱比。

    RS_Ratio > 其 EMA → MU 跑贏板塊。

    Args:
        mu_close: MU 收盤價序列。
        smh_close: SMH 收盤價序列。

    Returns:
        pd.Series: RS Ratio 值。
    """
    # 對齊兩個序列的 index
    aligned = pd.concat([mu_close.rename("mu"), smh_close.rename("smh")], axis=1).dropna()
    return aligned["mu"] / aligned["smh"]


def compute_rs_ratio_ema(rs_ratio: pd.Series, period: int = 10) -> pd.Series:
    """計算 RS Ratio 的 EMA。"""
    return rs_ratio.ewm(span=period, adjust=False).mean()


# ============================================================================
# Volume MA — 成交量移動平均
# ============================================================================

def compute_volume_ma(volume: pd.Series, period: int = 10) -> pd.Series:
    """
    計算成交量移動平均。

    Args:
        volume: 成交量序列。
        period: 均線週期。

    Returns:
        pd.Series: 成交量 MA。
    """
    return volume.rolling(window=period, min_periods=1).mean()


# ============================================================================
# VXN/VIX ATR 波動率檢測
# ============================================================================

def compute_vix_atr_spike(
    vix_close: pd.Series, period: int = 5, spike_threshold: float = 0.03
) -> pd.Series:
    """
    檢測 VIX 是否出現 ATR 急升。

    使用簡化的 True Range（當日高低差 + 跳空），計算 5 期 ATR，
    若 ATR 相較前值升幅超過門檻則標記。

    Args:
        vix_close: VIX 收盤價序列。
        period: ATR 週期。
        spike_threshold: 判定急升的門檻比例。

    Returns:
        pd.Series: True = 恐慌狀態。
    """
    tr = vix_close.diff().abs()
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    atr_pct_change = atr.pct_change()
    spike = atr_pct_change > spike_threshold
    return spike


# ============================================================================
# 批量計算 — 一次產出所有指標
# ============================================================================

def compute_all_indicators(
    mu_df: pd.DataFrame,
    smh_df: pd.DataFrame,
    vix_series: pd.Series | None = None,
) -> dict:
    """
    一次性計算所有技術指標。

    Args:
        mu_df: MU 的 OHLCV DataFrame，index 為 datetime。
        smh_df: SMH 的 OHLCV DataFrame，index 為 datetime。
        vix_series: VIX 收盤價序列（可選）。

    Returns:
        dict: 所有指標的字典。
    """
    indicators = {}

    # --- MU 指標 ---
    mu_close = mu_df["close"]
    indicators["mu_close"] = mu_close  # 原始價格供信號模組使用
    mu_high = mu_df["high"]
    mu_low = mu_df["low"]
    mu_volume = mu_df["volume"]

    indicators["mu_rsi"] = compute_rsi(mu_close, period=14)
    indicators["mu_rsi_prev"] = indicators["mu_rsi"].shift(1)  # 前一根 RSI，用於判斷突破
    indicators["mu_atr"] = compute_atr(mu_high, mu_low, mu_close, period=14)
    indicators["mu_vwap"] = compute_rolling_vwap(mu_close, period=5)
    indicators["mu_ema_fast"] = compute_ema(mu_close, period=5)
    indicators["mu_ema_slow"] = compute_ema(mu_close, period=20)
    indicators["mu_volume_ma"] = compute_volume_ma(mu_volume, period=10)
    indicators["mu_volume_surge"] = mu_volume > (indicators["mu_volume_ma"] * 1.5)

    bb = compute_bollinger_bands(mu_close, period=20, num_std=2.0)
    indicators["mu_bb_upper"] = bb["bb_upper"]
    indicators["mu_bb_lower"] = bb["bb_lower"]
    indicators["mu_bb_middle"] = bb["bb_middle"]

    # --- SMH 指標 ---
    smh_close = smh_df["close"]
    indicators["smh_close"] = smh_close  # 原始價格供信號模組使用
    indicators["smh_vwap"] = compute_rolling_vwap(smh_close, period=5)

    # --- RS Ratio ---
    indicators["rs_ratio"] = compute_rs_ratio(mu_close, smh_close)
    indicators["rs_ratio_ema"] = compute_rs_ratio_ema(indicators["rs_ratio"], period=10)

    # --- VIX ---
    if vix_series is not None:
        indicators["vix_spike"] = compute_vix_atr_spike(vix_series, period=5)

    return indicators
