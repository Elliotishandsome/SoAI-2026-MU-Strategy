"""
yfinance 分鐘數據下載器 — MU 日內 AI 交易策略

使用 yfinance 抓取 1 分鐘 OHLCV 數據，輸出為 backtest.py (Pandas mode)
所需的 CSV 格式（open, high, low, close, volume, timestamp UTC）。

注意：
  - Yahoo Finance 免費 1 分鐘數據僅提供「最近 7 天」。
  - 正式比賽期間官方會提供分鐘數據；此腳本僅供本地開發。

使用方式:
    python download_minute_data.py
"""

from pathlib import Path
import sys

import pandas as pd
import yfinance as yf

# ============================================================================
# 可調參數
# ============================================================================

# 要下載的標的（MU 主標的 + SMH 板塊 ETF + SPY 基準）
SYMBOLS = ["MU", "SMH", "SPY"]

# Yahoo 拉取區間（1m 數據最多約 7 天）
PERIOD = "7d"

# K 線週期：1m = 1 分鐘
INTERVAL = "1m"

# 輸出目錄（與 backtest.py 的 DATA_DIR 一致）
DATA_DIR = Path(__file__).resolve().parent / "data"


# ============================================================================
# 主流程
# ============================================================================

def fetch_minute_data(symbol: str) -> pd.DataFrame:
    """
    下載單一標的的 1 分鐘數據，轉為模板 CSV 格式。

    Returns:
        DataFrame: 欄位為 open, high, low, close, volume, timestamp (ISO-8601 UTC)。
    """
    print(f"[INFO] 下載 {symbol} 1m 數據 (period={PERIOD}) ...")
    df = yf.download(
        symbol,
        period=PERIOD,
        interval=INTERVAL,
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if df is None or df.empty:
        print(f"[WARN] {symbol} 沒有取得任何數據")
        return pd.DataFrame()

    # 扁平化 MultiIndex 欄位（yfinance 新版會回傳 ('Open', 'MU') 這種結構）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 統一為小寫欄位名
    df = df.rename(columns=str.lower)

    # 只保留 OHLCV，丟棄 Adj Close 等
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    if len(keep) < 5:
        print(f"[WARN] {symbol} 欄位不完整: {list(df.columns)}")
        return pd.DataFrame()
    df = df[keep]

    # 移除 NaN 行（盤前/盤後的空 bar）
    df = df.dropna()

    # 統一轉為 UTC，並輸出 ISO-8601 字串
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.sort_index()
    df["timestamp"] = df.index.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 輸出欄位順序與模板一致
    return df[["open", "high", "low", "close", "volume", "timestamp"]]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    downloaded: list[str] = []

    for symbol in SYMBOLS:
        df = fetch_minute_data(symbol)
        if df.empty:
            continue

        out_path = DATA_DIR / f"{symbol}_1m_spot.csv"
        df.to_csv(out_path, index=False)
        print(
            f"[OK] {symbol}: {len(df):,} bars "
            f"({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}) → {out_path.name}"
        )
        downloaded.append(symbol)

    print("=" * 50)
    if downloaded:
        print(f"完成！下載 {len(downloaded)} 個標的: {', '.join(downloaded)}")
        print("執行回測: python backtest.py")
    else:
        print("沒有成功下載任何數據。請檢查網路或稍後重試。")
        sys.exit(1)


if __name__ == "__main__":
    main()
