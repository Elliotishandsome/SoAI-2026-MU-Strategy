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

# 主交易標的（v2.7：支援多標的）
TRADE_SYMBOL = "MU"                   # 主要標的（日誌/兼容用）
TRADE_SYMBOLS = ["MU"]                # 可交易標的（v2.8：純 MU；SNDK/SPY 測試見 README）

# 輔助標的（用於相對強弱 RS = 個股/SMH）
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
# 交易標的（MU + AMD）+ SMH 板塊 ETF（Layer1 RS 相對強弱需要）—— 已解綁 SPY
STOCK_SLEEVE_SYMBOLS = TRADE_SYMBOLS + [SECTOR_ETF]
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
RSI_OVERSOLD_THRESHOLD = 52       # 買入觸發：RSI 突破此值（v2.3 由 40 提高，過濾高位追入）
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
VOLUME_SURGE_MULTIPLIER = 1.9     # 放量倍數門檻（v2.3 由 1.5 提高，過濾假突破）

# VWAP 回踩進場（v2.3）
# 形態條件：當前收盤站上 5m VWAP，且
#   A. 當前 bar 最低價回踩至 VWAP 附近（low ≤ VWAP×1.002），或
#   B. 前一根 bar 收盤在 VWAP 之下（本根才重新站上 = 回踩轉強）
# 取代「突破當下直接追高」的舊邏輯
PULLBACK_TOUCH_TOLERANCE = 0.002  # 回踩允許觸及的寬容比例 (0.2%)

# ============================================================================
# 2.5 ADX 趨勢強度過濾 + 日內網格（v2.5）
# ============================================================================

# ADX 週期（衡量趨勢強度，不分漲跌）
ADX_PERIOD = 14

# 網格啟動閾值：ADX < 此值 → 無趨勢震盪市 → 允許啟動網格
ADX_GRID_START_THRESHOLD = 25.0

# 網格熔斷閾值：ADX ≥ 此值 → 單邊趨勢形成 → 立刻暫停新建網格層
# （與啟動閾值相同：ADX 由 <25 變 ≥25 即熔斷）
ADX_GRID_HALT_THRESHOLD = 25.0

# 網格層數（基準價下方買入層數）
GRID_NUM_LEVELS = 3

# 網格間距：spacing = GRID_SPACING_ATR × ATR(14)（5m K 線）
GRID_SPACING_ATR = 1.0

# 每層建倉金額（USD）
GRID_LEVEL_VALUE = 50_000

# 網格基準價：每日開盤價（每日重置）
# 可選 "OPEN"（開盤價）或 "DAILY_VWAP"（當日 VWAP）
GRID_ANCHOR = "OPEN"

# 每日 ADX 計算基準：K 線週期（分鐘）— 用 15 分鐘 K 線算 ADX 較穩定
ADX_BAR_MINUTES = 15

# ============================================================================
# 4. 風控參數
# ============================================================================

MAX_RISK_RATIO = 0.02             # 單筆最大風險比例 (2%)
ATR_STOP_MULTIPLIER = 1.5         # 止損 ATR 倍數
MAX_POSITION_RATIO = 0.50         # 單筆頭寸上限 (50% of capital, v2.4 由 25% 提高)
MAX_POSITION_VALUE = 500_000      # 單筆建倉市值上限 (USD, v2.4 由 250K 提高，提升資金使用率)

# ============================================================================
# 4.5 槓桿（v2.8）
# ============================================================================

# 槓桿倍數：1.0 = 無槓桿；1.2 = 總曝險可達 equity×1.2（溫和槓桿，推薦）
# 實作：購買力 = equity × LEVERAGE − 既有持倉市值（槓桿體現在總曝險）
# ⚠️ 回測實證（修復每日盈虧基準後）：
#    1x  → +$121,589（Sharpe 1.28）
#    1.2x → +$146,011（Sharpe 1.41）★ 最優
#    2x  → 單筆頂到 $2M 全倉 → 資金一次用盡 → 訊號中斷（災難）
LEVERAGE = 1.2
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
