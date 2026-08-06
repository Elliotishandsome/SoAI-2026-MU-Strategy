"""
策略參數配置 — MU 日內 AI 交易策略（SoAI 2026）

此檔案集中管理所有可調參數，方便回測調參與後續修改。
分為以下區塊：
  1. 交易標的 & 數據源
  2. 資金 & 費率
  3. 技術指標參數
  4. 風控參數
  5. 交易時段

使用方式：在其他模組中 `from strategies.params import *` 或按區塊導入。
"""

# ============================================================================
# 1. 交易標的 & 數據源
# ============================================================================

# 主交易標的
TRADE_SYMBOL = "MU"

# 輔助標的（用於相對強弱 RS = MU/SMH）
SECTOR_ETF = "SMH"       # 半導體板塊 ETF（替代 SPY 作為板塊參照）
VOLATILITY_INDEX = "^VIX"  # 恐慌指數（Yahoo 代碼為 ^VIX；v2 暫不強制使用）

# 回測對標基準 — 解綁 SPY，改用半導體板塊指數 SMH
BENCHMARK = "SMH"

# 策略喚醒頻率（Lumibot sleeptime）：
#   "5M" = 5 分鐘級（分鐘回測，配合 data/ 下的 1m CSV 使用）
#   "1M" = 1 分鐘級
#   "1D" = 日線級（Yahoo backtest）
SLEEPTIME = "5M"

# 分鐘級回測時，將 1 分鐘 bar 重取樣為幾分鐘的 K 線（5 = 5 分鐘 K 線）
RESAMPLE_MINUTES = 5

# ============================================================================
# 1.5 大趨勢錨點 — Daily VWAP（每日開盤重置）
# ============================================================================

# 判斷標準：MU 當前價格 > Daily VWAP → 今日多頭控盤，5 分鐘買點才有效；
#           價格 < Daily VWAP → 今日主力出貨，5 分鐘買點一律作廢。
# Daily VWAP 每天開盤重置，不受隔夜跳空影響，反映今天機構資金的總體態度。
# （v2.1 曾用 1H EMA20，v2.2 改為 Daily VWAP）

# 最後允許開倉時間（美東）：Lumibot 在 15:55 後不再迭代，
# 15:55 買入將無法當日清倉 → 15:50 後禁止開新倉
LAST_ENTRY_TIME_HOUR = 15
LAST_ENTRY_TIME_MINUTE = 50

# CSV 數據模式（Pandas backtest）使用的標的清單
# MU 主標的 + SMH 板塊 ETF（Layer1 RS 相對強弱需要）—— 已解綁 SPY
STOCK_SLEEVE_SYMBOLS = [TRADE_SYMBOL, SECTOR_ETF]
CRYPTO_SLEEVE_SYMBOLS: list[str] = []
STOCK_BENCH = BENCHMARK
CRYPTO_BENCH = BENCHMARK
CRYPTO_SYMBOLS: set[str] = set(CRYPTO_SLEEVE_SYMBOLS)

# 是否強制要求 SMH 數據（True = SMH 缺失時停止交易；False = 降級為只用 MU 自身動能）
# 目前 data/ 有完整 SMH 數據，保持 True 使用完整 RS 相對強弱架構
REQUIRE_SMH = True

# ============================================================================
# 2. 資金 & 費率
# ============================================================================

INITIAL_CAPITAL = 1_000_000       # 初始總資金 (USD)
BUY_COMMISSION_BPS = 2.0          # 買入手續費 (bps, 0.02%)
SELL_COMMISSION_BPS = 2.0         # 賣出手續費 (bps, 0.02%)
SLIPPAGE_BPS = 1.0                # 滑點假設 (bps)

# ============================================================================
# 3. 技術指標參數
# ============================================================================

# RSI
RSI_PERIOD = 14
RSI_OVERSOLD_THRESHOLD = 40       # 買入觸發：RSI 突破此值
RSI_OVERBOUGHT_THRESHOLD = 70     # 止盈觸發：RSI 超過此值

# 移動平均 / EMA
VWAP_LOOKBACK = 5                 # VWAP（日線模式即收盤價均線）回看週期
EMA_FAST = 5                      # 快線
EMA_SLOW = 20                     # 慢線（止盈參考）

# RS_Ratio（相對強弱）
RS_EMA_PERIOD = 10                # RS_Ratio EMA 週期

# 布林帶
BB_PERIOD = 20
BB_STD_MULTIPLIER = 2.0

# ATR
ATR_PERIOD = 14

# 成交量
VOLUME_MA_PERIOD = 10             # 成交量均線週期
VOLUME_SURGE_MULTIPLIER = 1.5     # 放量倍數門檻

# ============================================================================
# 4. 風控參數
# ============================================================================

MAX_RISK_RATIO = 0.02             # 單筆最大風險比例 (2%)
ATR_STOP_MULTIPLIER = 1.5         # 止損 ATR 倍數
MAX_POSITION_RATIO = 0.25         # 單筆頭寸上限 (25% of capital)
MAX_POSITION_VALUE = 250_000      # 單筆建倉市值上限 (USD)
DAILY_LOSS_LIMIT = 15_000         # 日內虧損熔斷 (USD)
DAILY_LOSS_LIMIT_RATIO = 0.015    # 日內虧損熔斷比例 (1.5%)
MAX_DAILY_TRADES = 20             # 單日交易次數上限
TAKE_PROFIT_RATIO = 0.50          # 首段止盈平倉比例 (50%)

# ============================================================================
# 5. 交易時段（美東時間 EST）
# ============================================================================

MARKET_OPEN = "09:30"
NO_TRADE_UNTIL = "10:00"          # 開盤前 30 分鐘僅監控不下單
FORCE_CLOSE_TIME = "15:55"        # 收盤前 5 分鐘強制清倉
