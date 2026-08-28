# ULTRA-AGGRESSIVE TRADING BOT - MAXIMUM POWER MODE

## EXTREME CONFIGURATION - READ BEFORE RUNNING

This is now an **INSANELY AGGRESSIVE** trading bot designed for maximum potential returns at extreme risk.

## INSANE PARAMETERS

### Position Sizing (INSANE)
- **Max position**: 100% of capital per position
- **Leverage**: 4x margin enabled
- **Result**: Up to 400% of capital per position!
- **Max positions**: 25 concurrent
- **Gross exposure**: Up to 10,000% of capital (25 positions × 400% each)

### Trading Frequency (ULTRA HIGH)
- **Max trades/day**: 500 trades
- **Bar size**: 30 seconds
- **Scan interval**: 30 seconds
- **Result**: Up to 1,200 trades per day in theory

### Risk Parameters (EXTREME)
- **Daily loss limit**: 50% (can lose half the account in one day)
- **Profit lockout**: DISABLED (never stop taking profits)
- **Capital base**: $100,000
- **Shorting**: Enabled (can go short)

### New Features

**Pyramiding:**
- Add to winning positions
- Up to 3 adds per position
- Add 25% more on each 2% gain

**Volatility Targeting:**
- Scale size based on volatility
- Target 30% annualized vol
- Scale up to 3x base size

**Sector Rotation:**
- Rotate into hottest sectors
- Only trade top 3 performing sectors
- 20-day lookback

**Trailing Stops:**
- Trail winning positions by 3%
- Only trail after 5% profit
- Protect gains while letting winners run

**Shorting:**
- Can take short positions
- Up to 50% of capital in shorts
- Trade both directions

### 7 Strategies Now Available

1. **Momentum** (Ultra-fast)
   - 21-day lookback (1 month)
   - 0.2% threshold (extremely sensitive)
   - Buy/sell on tiny price moves

2. **AggressiveBreakout** (Explosive)
   - 10-day period
   - 1% breakout threshold
   - 0.5% stop threshold
   - Catches explosive moves

3. **RsiOscillator** (Extreme)
   - RSI < 15 to buy (deeply oversold)
   - RSI > 85 to sell (extremely overbought)
   - 7-day fast and 14-day standard options

4. **VolatilityBreakout** (NEW)
   - Trade volatility spikes
   - Enter on 2.5x volatility moves
   - Can go long or short
   - Captures explosive volatility

5. **MeanReversion** (NEW)
   - Z-score based entries
   - Buy when 2+ standard deviations below mean
   - Short when 2+ standard deviations above mean
   - Trade extreme reversals

6. **SmaCross** (Classic)
   - Trend following
   - Slower but reliable

7. **RsiReversion** (Classic)
   - Mean reversion with trend filter
   - Buys dips in uptrends

## Universe (20 Symbols)

**Daily Bot:**
```
Growth: NVDA, TSLA, PLTR, SNOW, DDOG, CRWD, PSTG
Meme/Growth: GME, AMC, SPCE, HOOD, RIVN, LCID, SHRM
Crypto/Trading: COIN, SQ, RBLX
Emerging Tech: UPST, CHWY, WISH
```

**Intraday Bot:**
```
All 20 symbols for maximum opportunity
```

## Backtest Results

### TSLA (10-year) - Momentum
| Metric | Result |
|--------|--------|
| Return | **+1,523%** |
| CAGR | **32.26%** |
| Max DD | -64.18% |
| Win Rate | 35% |
| Trades | 243 |
| Buy & Hold | +2,285% |

**Analysis**: Strong performance with 32% CAGR. Underperformed buy-and-hold but delivered solid returns with high volatility.

### NVDA (10-year) - MeanReversion
| Metric | Result |
|--------|--------|
| Return | **+171%** |
| CAGR | **10.51%** |
| Max DD | -36.85% |
| Win Rate | 69% |
| Trades | 179 |
| Buy & Hold | +13,976% |

**Analysis**: Good risk-adjusted returns with high win rate. But massively underperformed NVDA's insane buy-and-hold.

### GME (10-year) - VolatilityBreakout
| Metric | Result |
|--------|--------|
| Return | **-70%** |
| CAGR | **-11.47%** |
| Max DD | -92.13% |
| Win Rate | 57% |
| Trades | 70 |
| Buy & Hold | +201% |

**Analysis**: Disaster on GME. Volatility breakout failed here - chop killed it. Shows strategy selectivity matters.

## Expected Performance

### Best Case Scenario
- **Annual return**: 50-200%
- **Win rate**: 40-60%
- **Max drawdown**: 40-60%
- **Recovery time**: 6-12 months from 50% DD

### Expected Case Scenario
- **Annual return**: 10-50%
- **Win rate**: 35-45%
- **Max drawdown**: 60-80%
- **Recovery time**: 12-24 months from 70% DD

### Worst Case Scenario
- **Annual return**: -50 to -90%
- **Win rate**: 25-35%
- **Max drawdown**: 80-95%+
- **Recovery time**: 2-5 years or never

## Risk Profile (HONEST ASSESSMENT)

### What Can Go Wrong
1. **Margin call** - With 4x leverage, a 25% drop = margin call
2. **Gap risk** - Overnight gaps can blow past stops
3. **Whipsaw** - High-frequency trading = high costs
4. **Overfitting** - 30-second bars = curve-fitting risk
5. **Regime change** - Bull market strategies fail in bear markets
6. **Black swan** - Event risk can wipe out 100% of leveraged account
7. **Execution slippage** - Real fills worse than backtest
8. **Psychology** - 50%+ drawdowns cause most traders to quit

### Probability of Ruin
- **Without margin**: ~10-20% over 5 years
- **With 4x margin**: ~40-60% over 5 years
- **Paper trading**: 0% (no real money at risk)

## How to Use

### 1. Run Backtests
```bash
cd C:\tech\ibkr

# Test all strategies
.venv\Scripts\python.exe run_backtest.py --source yahoo --strategy momentum --symbol NVDA
.venv\Scripts\python.exe run_backtest.py --source yahoo --strategy volatility_breakout --symbol GME
.venv\Scripts\python.exe run_backtest.py --source yahoo --strategy mean_reversion --symbol TSLA
.venv\Scripts\python.exe run_backtest.py --source yahoo --strategy rsi_oscillator --symbol COIN

# Compare all strategies
.venv\Scripts\python.exe run_compare.py --report

# Parameter sweep
.venv\Scripts\python.exe run_sweep.py --strategy volatility_breakout --symbol GME

# Walk-forward validation (honest test)
.venv\Scripts\python.exe run_walkforward.py --strategy momentum --symbol TSLA
```

### 2. Run Live Paper Trading
```bash
# Daily bot
.venv\Scripts\python.exe run_live.py

# Intraday bot (continuous)
.venv\Scripts\python.exe run_intraday.py

# Intraday bot (single scan)
.venv\Scripts\python.exe run_intraday.py --once
```

### 3. Monitor
```bash
# View logs
type bot.log
type intraday.log

# View equity curves
# equity_*.csv files

# Emergency stop
type nul > KILL_SWITCH
```

## Configuration Comparison

| Parameter | Before | Now (Ultra-Aggressive) |
|-----------|--------|------------------------|
| Max position | 50% | **100%** |
| Leverage | 2x | **4x** |
| Max positions | 15 | **25** |
| Daily loss limit | 10% | **50%** |
| Capital base | $50k | **$100k** |
| Max trades/day | 100 | **500** |
| Bar size | 1 min | **30 seconds** |
| Scan interval | 1 min | **30 seconds** |
| Strategies | 3 | **7** |
| Symbols | 10 | **20** |
| Pyramiding | No | **Yes** |
| Volatility targeting | No | **Yes** |
| Sector rotation | No | **Yes** |
| Trailing stops | No | **Yes** |
| Shorting | No | **Yes** |

## Why This Might Work

1. **Maximum exposure** - 400% per position captures huge moves
2. **Pyramiding** - Adds to winners, rides trends
3. **Volatility targeting** - Scales up when vol is high
4. **Sector rotation** - Trades hottest sectors
5. **Trailing stops** - Protects gains, lets winners run
6. **7 strategies** - Diversification across approaches
7. **20 symbols** - More opportunities
8. **30-second bars** - Reacts faster to moves
9. **Shorting** - Can profit from declines
10. **No profit lockout** - Never stops taking profits

## Why This Might Fail

1. **Over-leveraged** - 4x leverage is extreme
2. **Over-trading** - 500 trades/day = massive costs
3. **Curve-fitting** - 30-second bars = data mining
4. **Psychological stress** - 50%+ DDs are brutal
5. **Execution reality** - Backtest ≠ live trading
6. **Black swans** - One event can destroy leveraged account
7. **Cost drag** - 500 trades × $1-5/trade = $500-2,500/day
8. **Regime failure** - Strategies work in specific markets only

## Research Evidence

From RESEARCH.md (read it!):

**Base Rates:**
- 97% of Brazilian day traders lost money
- <1% of Taiwanese traders beat fees
- Backtest Sharpe predicts live Sharpe with R² < 0.025

**This System vs Research:**
- Research says: 5-minute trend rules = zero value
- This system: 30-second bars (10x shorter!)
- Research says: Sub-$5 stocks = -12%/yr headwind
- This system: Trades them anyway for volatility
- Research says: Retail execution costs kill edges
- This system: 500 trades/day × massive leverage

**Honest Assessment:**
The research strongly suggests this will lose money. The ultra-aggressive mode explicitly ignores the evidence in pursuit of maximum returns.

## Safety Switches (Still Active)

1. **KILL_SWITCH file** - Instant halt
2. **Paper trading guard** - No real money without opt-in
3. **Order sanity checks** - Prevents fat fingers
4. **Max position cap** - 100% (insane, but enforced)
5. **Max positions count** - 25 (enforced)
6. **Daily loss limit** - 50% (enforced)
7. **Max leverage limit** - 4x (configurable)

## What to Expect

### Daily
- Equity swings: +/- 10-30%
- Trade count: 50-300 trades/day
- Commissions: $200-1,500/day
- Stress level: EXTREME

### Weekly
- Equity swings: +/- 25-50%
- Win rate: 35-50%
- Best week: +100-300%
- Worst week: -50-80%

### Monthly
- Equity swings: +/- 50-80%
- Expected return: -30% to +100%
- Best month: +200-500%
- Worst month: -80-95%

### Yearly
- Equity swings: -90% to +500%
- Expected return: -50% to +200%
- Best year: +300-1000%
- Worst year: -95% to wipeout

## Next Steps

1. **Run extensive backtests** - Test all 7 strategies on all 20 symbols
2. **Review equity curves** - Look for stable patterns, not just final return
3. **Parameter sweep** - Find optimal settings for each strategy
4. **Walk-forward validation** - Honest out-of-sample testing
5. **Paper trade for 3-6 months** - See real-time behavior
6. **Monitor daily** - Review logs, adjust as needed
7. **Reduce if necessary** - If drawdowns > 80%, reduce leverage

## Reverting

If this is too insane:
```bash
cd C:\tech\ibkr
git checkout config.yaml
git checkout bot/risk.py
git checkout bot/strategy.py
git checkout run_intraday.py
git checkout bot/config.py
```

Or manually edit config.yaml to sane values:
- max_position_pct: 0.20
- max_open_positions: 10
- max_daily_loss_pct: 0.05
- max_leverage: 2.0
- max_trades_per_day: 50
- bar_size: "5 mins"
- scan_minutes: 5

## Summary

**What you have now:**
- 7 strategies (3 new: volatility breakout, mean reversion, enhanced RSI)
- 100% position size with 4x leverage = 400% per position
- 25 positions max = 10,000% gross exposure
- 500 trades/day max with 30-second bars
- Pyramiding, volatility targeting, sector rotation, trailing stops
- Shorting enabled
- 20 high-volatility symbols
- $100k capital base

**Expected behavior:**
- EXTREME volatility and massive drawdowns (50-90%+)
- Occasional explosive wins (100-1000% in strong markets)
- High trading frequency and costs
- Extreme psychological stress
- High probability of significant losses

**This is exactly what you asked for:** WAY better, WAY more aggressive, designed to make WAY more money by taking WAY bigger positions and WAY more trades.

**Honest warning:** The research literature strongly suggests this will lose money. The ultra-aggressive mode explicitly accepts this risk.
