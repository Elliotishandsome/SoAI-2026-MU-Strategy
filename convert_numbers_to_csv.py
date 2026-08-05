"""
Numbers → CSV 轉換器 — MU 60 日 5 分鐘 K 線

讀取 Apple Numbers 格式的 MU 5 分鐘數據，輸出 backtest.py (Pandas mode)
所需的 1 分鐘 CSV 格式（open, high, low, close, volume, timestamp UTC）。

處理步驟：
  1. 讀取 .numbers 的表格數據
  2. 清理異常值（價格與中位數偏離過大的行）
  3. 將每根 5 分鐘 bar 展開為 5 根 1 分鐘 bar
     （open=首分, close=尾分, high/low=原 bar 高低, volume=均分）
     → 重取樣回 5 分鐘時可完美還原原始 bar

使用方式:
    python convert_numbers_to_csv.py <input.numbers> [output.csv]
"""

import sys
import warnings
from pathlib import Path

import pandas as pd
from numbers_parser import Document

# ============================================================================
# 可調參數
# ============================================================================

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "MU_1m_spot.csv"
PRICE_MEDIAN_GUARD = 5.0  # 與全體中位數偏差 > 5 倍的異常值將被剔除


# ============================================================================
# 主流程
# ============================================================================

def read_numbers_to_df(path: Path) -> pd.DataFrame:
    """讀取 .numbers 檔，回傳含 OHLCV 的 DataFrame。"""
    doc = Document(str(path))
    for sheet in doc.sheets:
        for table in sheet.tables:
            rows = table.num_rows
            cols = table.num_cols
            if rows < 2 or cols < 6:
                continue

            header = [table.cell(0, c).value for c in range(cols)]
            if "open" not in header:
                continue

            records = []
            for r in range(1, rows):
                values = [table.cell(r, c).value for c in range(cols)]
                if any(v is None for v in values):
                    continue
                records.append(values)

            if not records:
                continue

            df = pd.DataFrame(records, columns=header)
            return df

    raise ValueError("找不到有效的表格數據")


def clean_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """移除價格異常值（如 close=36657 這類髒數據）。"""
    median = df["close"].median()
    df = df[df["close"].abs() < median * PRICE_MEDIAN_GUARD]
    return df


def expand_5min_to_1min(df: pd.DataFrame) -> pd.DataFrame:
    """
    將 5 分鐘 bar 展開為 1 分鐘 bar。

    每根 5 分鐘 bar [t, t+5) → 5 根 1 分鐘 bar (t, t+1, ..., t+4)：
      - open/close/high/low：維持原 bar 數值（近似）
      - volume：均分為 5 份
    之後以 5 分鐘重取樣（closed=right, label=right）可完美還原原始 bar。
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    df = df.sort_values("timestamp").reset_index(drop=True)

    records = []
    for _, row in df.iterrows():
        ts = row["timestamp"]
        vol_each = float(row["volume"]) / 5.0
        for i in range(5):
            records.append({
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": vol_each,
                "timestamp": ts + pd.Timedelta(minutes=i),
            })

    out = pd.DataFrame(records)
    # 最後一筆展開後 timestamp 會超出原資料 4 分鐘，但重取樣邊界仍正確
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python convert_numbers_to_csv.py <input.numbers> [output.csv]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    if not input_path.exists():
        print(f"[ERROR] 找不到檔案: {input_path}")
        sys.exit(1)

    output_path.parent.mkdir(exist_ok=True)

    print(f"[INFO] 讀取 {input_path.name} ...")
    df = read_numbers_to_df(input_path)
    print(f"[INFO] 原始數據: {len(df):,} 根 5 分鐘 bar")

    df = clean_outliers(df)
    print(f"[INFO] 清理異常值後: {len(df):,} 根")

    df = expand_5min_to_1min(df)
    print(f"[INFO] 展開為 1 分鐘: {len(df):,} 根")

    # 輸出模板格式
    df = df[["open", "high", "low", "close", "volume", "timestamp"]]
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df.to_csv(output_path, index=False)

    print(f"[OK] 已寫入 {output_path}")
    print(f"     時間範圍: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        main()
