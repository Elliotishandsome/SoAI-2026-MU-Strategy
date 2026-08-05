"""
Yahoo Finance 日線回測執行器 — MU 日內 AI 交易策略

使用 Lumibot 內建的 YahooDataBacktesting，自動從 Yahoo Finance 拉取
日線 OHLCV 數據，無需本地下載 CSV。

此模式為日線級別回測（sleeptime = "1D"），用於快速驗證策略邏輯。
正式比賽使用分鐘級別（需 Pandas backtest + CCXT/Massive 數據源）。

使用方式:
    python backtest_yahoo.py

調整說明:
  - 修改 BACKTEST_START / BACKTEST_END 控制回測區間
  - 修改 BUDGET 控制初始資金
  - 修改 BENCHMARK 控制對標指數
"""

from datetime import datetime

from lumibot.backtesting import YahooDataBacktesting

# 注意：此導入必須是 strategies.strategy 中的 Strategy 類
# Lumibot 會通過此路徑動態載入策略
from strategies.strategy import Strategy


# ============================================================================
# 可調參數
# ============================================================================

# 回測區間 — 使用 Yahoo Finance 有數據的範圍
# 注意：未來日期 Yahoo 不會有數據；請設定為過往日期
BACKTEST_START = datetime(2025, 1, 1)
BACKTEST_END = datetime(2025, 8, 5)

# 初始資金（與 params.py 中保持一致）
BUDGET = 1_000_000

# 對標基準
BENCHMARK = "SPY"

# 是否顯示詳細日誌
VERBOSE = True


# ============================================================================
# 執行
# ============================================================================

def run_yahoo_backtest():
    """
    使用 Yahoo Finance 日線數據執行策略回測。

    YahooDataBacktesting 自動處理數據拉取與日期對齊。
    策略內的 sleeptime 必須設為 "1D"（日線模式）。
    """
    print("=" * 60)
    print(" MU AI 交易策略 — Yahoo 日線回測")
    print("=" * 60)
    print(f" 回測區間: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(f" 初始資金: ${BUDGET:,.0f}")
    print(f" 對標基準: {BENCHMARK}")
    print(f" 策略模式: 日線 (sleeptime='1D')")
    print("=" * 60)

    result = Strategy.run_backtest(
        YahooDataBacktesting,
        BACKTEST_START,
        BACKTEST_END,
        budget=BUDGET,
        benchmark_asset=BENCHMARK,
        show_tearsheet=True,
        save_tearsheet=True,
        show_plot=VERBOSE,
    )

    print("\n" + "=" * 60)
    print(" 回測完成！")
    print("=" * 60)
    return result


if __name__ == "__main__":
    run_yahoo_backtest()
