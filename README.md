# MU AI 日內交易策略 — SoAI 2026

## 策略概述

本策略專為 **美光科技 (MU)** 設計，採用 **三層決策架構**，通過規避新聞及基本面噪音，利用純價格行為 (Price Action) 及指數情緒共振進行交易。

| 項目 | 值 |
|------|-----|
| 交易標的 | MU (Micron Technology) |
| 運行週期 | 3–5 分鐘 K 線（日線回測模式亦支援） |
| 初始資金 | $1,000,000 USD |
| 手續費 | 雙邊各 2 bps (0.02%) |
| 持倉時間 | 純日內，美東 15:55 強制清倉 |

## 三層決策架構

### 第 1 層：母系統 — 大盤與板塊情緒過濾
- **SMH VWAP**：SMH 價格必須位於 5 分鐘 VWAP 之上，否則禁止做多
- **VIX 急升檢測**：VIX 5 分鐘 ATR 飆升超過 3% → 暫停開倉

### 第 2 層：核心系統 — 個股相對強弱 (RS Ratio)
- `RS_Ratio = MU Price / SMH Price`
- 若 RS_Ratio > 10 週期 EMA → MU 跑贏板塊，允許做多

### 第 3 層：子系統 — 微觀執行觸發
- 價格突破 VWAP + RSI(14) > 40 + 成交量放大至 10 週期均值 × 1.5
- 止盈：布林帶上軌 / RSI > 70 → 平倉 50%；跌破 20-EMA → 全數清倉

## 風控約束

| 約束 | 值 |
|------|-----|
| 倉位模型 | 固定風險 + ATR 自適應 |
| 單筆最大風險 | 總資金 2%（$20,000） |
| 止損距離 | 1.5 × ATR |
| 單筆頭寸上限 | $250,000（總資金 25%） |
| 日內熔斷 | 虧損 $15,000（1.5%）→ 立即停機 |
| 交易頻率限制 | 單日最多 20 筆 |
| 開盤保護 | 9:30–10:00 僅監控不下單 |

## 模組結構

```text
strategies/
├── __init__.py            # 套件初始化
├── params.py              # 所有可調參數集中管理
├── indicators.py          # 技術指標純函數（RSI/ATR/VWAP/BB/EMA/RS_Ratio）
├── risk_manager.py        # 風控管理（倉位計算/熔斷/時間約束）
├── signal_generator.py    # 三層訊號生成（純函數）
├── strategy.py            # 主策略類（Lumibot 入口）
├── example_strategy_1.py  # 模板範例（保留）
└── example_strategy_2.py  # 模板範例（保留）
```

## 快速回測

```bash
# 分鐘級回測（Pandas CSV 模式，建議先下載數據）
python download_minute_data.py    # yfinance 抓最近 7 天 1 分鐘 OHLCV
python backtest.py                # 5 分鐘 K 線回測

# 日線回測（Yahoo Finance，免 API Key）
python backtest_yahoo.py
```

**模式切換**：在 `strategies/params.py` 修改 `SLEEPTIME`（`"5M"` / `"1M"` / `"1D"`）與 `RESAMPLE_MINUTES`（分鐘級 K 線重取樣週期）。分鐘級回測時交易時段（9:30–10:00 監控期、15:55 強制清倉）自動以美東時間生效。

---

*基於 [SoAI 2026 AI Algorithmic Trading Competition](https://www.soc-ai.org/events/intelligencex-2026) 官方模板構建。*
