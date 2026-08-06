# MU Intraday AI Trading Strategy — SoAI 2026

> ## 🏁 FINAL SUBMISSION v2.8 (2026-08-07)
>
> **Symbol: MU (Micron Technology) | Leverage: 1.2x | Cadence: 5-min bars | Pure intraday (forced flat at 15:55 ET)**
>
> **14-month backtest (Jun 2025 → Aug 2026): +$146,011 (14.6%) | Sharpe 1.41 |
> Sortino 4.52 | Win rate 69.4% | Profit factor 2.82 | Max drawdown -1.7% | 0 overnight positions**

## Strategy Overview

This strategy is purpose-built for **Micron Technology (MU)** using a
**three-layer decision framework** that trades purely on price action and
sector sentiment — avoiding news and fundamental noise.

| Item | Value |
|------|-------|
| Traded symbol | MU (Micron Technology) |
| Bar cadence | 5-minute OHLCV |
| Initial capital | $1,000,000 USD |
| Fees | 2 bps (0.02%) per side |
| Holding period | Intraday only; forced liquidation 15:55 ET |
| Leverage | 1.2x (total exposure = equity × 1.2 − existing position value) |

## Three-Layer Decision Framework

### Macro Trend Anchor: Daily VWAP
- Daily VWAP resets every open and accumulates Σ(typical price × volume)/Σ(volume)
  from the first bar of the session.
- **A 5-minute buy is only valid when MU price > Daily VWAP** (bulls in control today).
- Price below Daily VWAP = institutional distribution day → all 5-minute entries void.

### Layer 1: Master System — Relative Strength (RS Ratio)
- `RS_Ratio = MU Price / SMH Price`
- **RS_Ratio above its 10-period EMA → MU is stronger than the semiconductor
  sector, longs allowed.**
- If SMH data is unavailable, falls back to "MU price above its own VWAP".

### Layer 2: Core System — Price Above VWAP
- MU price must be above the 5-minute VWAP (upward breakout).

### Layer 3: Sub-System — VWAP Pullback Entry
- Enter only after price pulls back toward the 5-min VWAP and reclaims it
  (current-bar low touches within ≤×1.002 of VWAP, or prior bar closed below VWAP).
- **RSI(14) > 52** (filter out late-chasing entries).
- **Volume surge ≥ 1.9× its moving average** (filter out false breakouts).
- Exits: Bollinger upper band / RSI > 70 → sell 50%; close below 20-EMA → sell all.

## Risk Controls

| Constraint | Value |
|------------|-------|
| Position model | Fixed-risk (equity × 2%) + ATR-adaptive |
| Stop distance | 1.5 × ATR |
| Single position cap | initial capital × leverage ($1.2M) |
| Intraday circuit breaker | -$15,000 (1.5%) → halt for the day |
| Daily trade limit | 20 trades max |
| Opening protection | 9:30–10:00 ET monitor only, no orders |
| Forced liquidation | no new entries after 15:50; full liquidation 15:55 ET |

## Backtest Results (14 months, Jun 2025 → Aug 2026)

| Metric | Value |
|--------|-------|
| Intraday net P&L | **+$146,011 (14.6%)** |
| Sharpe | 1.41 |
| Sortino | 4.52 |
| Win rate | 69.4% |
| Profit factor | 2.82 |
| Max drawdown | -1.7% |
| Overnight positions | 0 |

Recent 3-month validation (2026-05-05 → 08-05): **+$50,912**, Sharpe 1.81,
win rate 75%, profit factor 5.07, 0 overnight — consistent with the long
history, no overfitting concerns.

## Module Structure

```text
strategies/
├── __init__.py            # package init
├── params.py              # all tunable parameters in one place
├── indicators.py          # pure indicator functions (RSI/ATR/VWAP/BB/EMA/RS_Ratio)
├── risk_manager.py        # risk management (position sizing / circuit breaker / time windows)
├── signal_generator.py    # three-layer signal generation (pure functions)
└── strategy.py            # main strategy class (Lumibot entrypoint, official execution)
```

## Quick Backtest

```bash
pip install -r requirements.txt
# Provide data/MU_1m_spot.csv and data/SMH_1m_spot.csv (1-min OHLCV, UTC)
python backtest.py   # 5-min bar backtest
```

The official score is generated **only** by the IntelligenceX technical team
running `strategies/strategy.py` in a standardized environment; the local
backtest is for development validation only.

---

*Built on the [SoAI 2026 AI Algorithmic Trading Competition](https://www.soc-ai.org/events/intelligencex-2026) official template.*
