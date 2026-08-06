"""
Massive 數據下載器 — MU 日內 AI 交易策略

從 Massive REST API 抓取美股 1 分鐘 OHLCV 數據（官方比賽數據源），
輸出為 backtest.py (Pandas mode) 所需的 CSV 格式。

使用方式:
    python download_massive_data.py

API Key 提供方式（擇一，切勿直接貼在聊天中）:
    1. 環境變數:  export MASSIVE_API_KEY=你的key
    2. .env 檔案: MASSIVE_API_KEY=你的key   （.env 已被 .gitignore 排除）

可調參數:
    SYMBOLS          要下載的標的（stocks 直接給代號）
    INDEX_SYMBOLS    指數標的（如 VIX）
    START_DATE       起始日 (YYYY-MM-DD)
    END_DATE         結束日 (YYYY-MM-DD)
    INTERVAL_MIN     分鐘 bar 大小（預設 1，策略內部再重取樣為 5 分鐘）
    REGULAR_SESSION  是否只保留美東 9:30–16:00 正規交易時段
"""

from pathlib import Path
import os
import sys
import time

import pandas as pd
import requests
from dotenv import load_dotenv

# ============================================================================
# 可調參數
# ============================================================================

# 股票標的（v2.7：交易標的 MU + AMD + 板塊 SMH）
SYMBOLS = ["MU", "AMD", "SMH"]

# 指數標的（VIX 為恐慌指數，用於 Layer 1 的恐慌過濾；需 Indices 方案權限）
# 若方案無 Indices 權限（403），策略會自動跳過 VIX 過濾，不影響主流程。
INDEX_SYMBOLS: list[str] = []

# 數據範圍（分段下載：免費方案有請求上限，一次拉太長會被 429 截斷）
START_DATE = "2025-06-01"
END_DATE = "2026-02-09"

# 分鐘 bar 大小（策略會在內部重取樣為 RESAMPLE_MINUTES=5）
INTERVAL_MIN = 1

# 只保留正規交易時段 9:30–16:00 (ET)，排除盤前盤後
REGULAR_SESSION_ONLY = True

# 輸出目錄
DATA_DIR = Path(__file__).resolve().parent / "data"

# API 設定
API_BASE = "https://api.massive.com"
API_KEY_ENV = "MASSIVE_API_KEY"
REQUEST_DELAY = 1.2  # 秒，避免觸發 rate limit（免費方案 429）


# ============================================================================
# API 輔助
# ============================================================================

def get_api_key() -> str:
    """從環境變數或 .env 讀取 API key。"""
    load_dotenv(Path(__file__).resolve().parent / ".env")
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        print("[ERROR] 找不到 MASSIVE_API_KEY")
        print("  請用以下任一方式提供：")
        print("    1. export MASSIVE_API_KEY=<你的key>")
        print("    2. 在 repo 根目錄建立 .env 檔（已被 gitignore）：")
        print("       MASSIVE_API_KEY=<你的key>")
        sys.exit(1)
    return key


def fetch_custom_bars(api_key: str, ticker: str, is_index: bool = False) -> list[dict]:
    """
    抓取單一標的的歷史 K 線（處理分頁）。

    Args:
        api_key: Massive API key。
        ticker: 標的代號。
        is_index: 是否為指數（走 indices endpoint）。

    Returns:
        list[dict]: results 陣列（含 o/h/l/c/v/t/vw 欄位）。
    """
    prefix = "indices" if is_index else "stocks"
    url = (
        f"{API_BASE}/v2/aggs/ticker/{ticker}/range/{INTERVAL_MIN}/minute/"
        f"{START_DATE}/{END_DATE}?apiKey={api_key}&sort=asc&limit=50000"
    )
    # 注意：stocks 用 /v2/aggs/...；indices 需確認 endpoint 路徑，
    # 此處先統一試 stocks 路徑，失敗再提示調整。

    all_results: list[dict] = []
    page = 0

    while url:
        page += 1
        try:
            resp = requests.get(url, timeout=30)
        except requests.RequestException as e:
            print(f"[WARN] {ticker} 第 {page} 頁請求失敗: {e}")
            time.sleep(2)
            continue

        if resp.status_code == 429:
            # rate limit：等待 60 秒重試（最多 3 次）
            for attempt in range(3):
                print(f"[WARN] {ticker} rate limit (429)，等待 60 秒重試 ({attempt+1}/3)...")
                time.sleep(60)
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    break
            else:
                print(f"[ERROR] {ticker} 持續 rate limit，放棄本頁")
                break
        if resp.status_code == 401:
            print("[ERROR] API key 無效或無權限 (401)")
            print("  請確認 key 正確，且方案涵蓋所需數據")
            sys.exit(1)
        if resp.status_code == 403:
            print(f"[ERROR] 無權限存取 {ticker} (403) — 可能超出方案範圍")
            break
        if resp.status_code != 200:
            print(f"[WARN] {ticker} 第 {page} 頁 HTTP {resp.status_code}: {resp.text[:150]}")
            break

        data = resp.json()
        results = data.get("results", []) or []
        all_results.extend(results)

        next_url = data.get("next_url")
        if next_url and next_url != url:
            url = next_url + (f"&apiKey={api_key}" if "apiKey=" not in next_url else "")
        else:
            url = None

        if page > 1 and page % 10 == 0:
            print(f"  {ticker}: 已抓取 {len(all_results):,} bars (第 {page} 頁)")
        time.sleep(REQUEST_DELAY)

    print(f"[OK] {ticker}: 共 {len(all_results):,} bars ({page} 頁)")
    return all_results


def results_to_df(results: list[dict], ticker: str, is_index: bool = False) -> pd.DataFrame:
    """
    將 Massive results 轉為模板 DataFrame。

    Args:
        results: API results 陣列。
        ticker: 標的代號（僅用於日誌）。
        is_index: 是否為指數（指數可能無成交量欄位）。

    Returns:
        DataFrame: open, high, low, close, volume, timestamp (UTC ISO-8601)。
    """
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)

    # 成交量：指數（如 VIX）可能沒有 volume，補 0
    df["volume"] = df.get("v", pd.Series(0, index=df.index)).fillna(0)

    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    df = df[["open", "high", "low", "close", "volume", "timestamp"]].sort_values("timestamp")

    # 只保留正規交易時段（美東 9:30–16:00）
    if REGULAR_SESSION_ONLY:
        et = df["timestamp"].dt.tz_convert("America/New_York")
        mask = (et.dt.time >= pd.Timestamp("09:30").time()) & (et.dt.time <= pd.Timestamp("16:00").time())
        df = df[mask]

    df = df.dropna(subset=["open", "close"]).reset_index(drop=True)
    return df


def save_csv(df: pd.DataFrame, symbol: str) -> Path:
    """輸出模板格式 CSV。"""
    out = DATA_DIR / f"{symbol}_1m_spot.csv"
    df = df.copy()
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df.to_csv(out, index=False)
    return out


# ============================================================================
# 主流程
# ============================================================================

def main() -> None:
    api_key = get_api_key()
    DATA_DIR.mkdir(exist_ok=True)

    print("=" * 60)
    print(f" Massive 數據下載 | {START_DATE} → {END_DATE} | {INTERVAL_MIN}min bars")
    print(f" 股票: {SYMBOLS} | 指數: {INDEX_SYMBOLS}")
    print("=" * 60)

    all_symbols = [(s, False) for s in SYMBOLS] + [(s, True) for s in INDEX_SYMBOLS]

    for symbol, is_index in all_symbols:
        print(f"\n[INFO] 下載 {symbol} ...")
        results = fetch_custom_bars(api_key, symbol, is_index=is_index)
        df = results_to_df(results, symbol, is_index=is_index)
        if df.empty:
            print(f"[WARN] {symbol} 沒有數據，跳過")
            continue
        out = save_csv(df, symbol)
        print(
            f"[OK] {symbol}: {len(df):,} bars "
            f"({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}) → {out.name}"
        )

    print("\n" + "=" * 60)
    print("完成！執行回測: python backtest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
