# AGGRESSIVE TRADING MODE - Configuration Summary

## Overview
The IBKR trading bot has been reconfigured for **maximum aggression** to increase potential profits. All safety rails have been significantly loosened or removed entirely.

## Key Changes

### 1. Position Sizing (CRAZY AGGRESSIVE)
- **Before**: 20% max position size (conservative)
- **After**: 50% max position size per position
- **Leverage**: Enabled 2x margin leverage
- **Result**: Up to 100% of capital per position with leverage

### 2. Position Count
- **Before**: 5 concurrent positions max
- **After**: 15 concurrent positions max
- **Result**: Can have 15 positions at 50% each = 750% gross exposure with leverage

### 3. Trading Frequency
- **Before**: 30 trades/day max, 5-minute scans
- **After**: 100 trades/day max, 1-minute scans
- **Result**: High-frequency trading capability

### 4. Strategy Changes
- **Before**: Slow SMA crossovers (20/50 day)
- **After**: Fast momentum (42-day) + 2 new aggressive strategies
  - `aggressive_breakout`: Buys on strong breaks above recent highs
  - `rsi_oscillator`: Trades overbought/oversold extremes

### 5. Daily Loss Limit
- **Before**: 2% daily loss limit (stops trading for the day)
- **After**: 10% daily loss limit
- **Result**: Can lose up to 10% before stopping

### 6. Profit Lockout (REMOVED)
- **Before**: Stop taking new trades after 10% daily gain
- **After**: **DISABLED** - Ride winners all day

### 7. Time Restrictions (REMOVED)
- **Before**: No entries first/last 15 minutes of day
- **After**: Trade from 9:30 AM to 3:59 PM (full session)

### 8. Overnight Holding (ENABLED)
- **Before**: Flatten all positions before close
- **After**: Hold overnight to capture gaps

### 9. Universe Change
- **Before**: Low-volatility small caps (TE, EOSE, ONDS)
- **After**: High-volatility growth + meme stocks
  - Daily: NVDA, TSLA, PLTR, GME, AMC, COIN, SQ, RBLX, HOOD, SPCE
  - Intraday: GME, AMC, BBBY, SPCE, HOOD, RIVN, LCID, SHRM

### 10. Bar Size (ULTRA FAST)
- **Before**: 5-minute bars
- **After**: 1-minute bars for intraday
- **Result**: React to price moves 5x faster

## Risk Profile

### Expected Drawdowns
- **Daily**: Up to 10% common
- **Weekly**: 20-30% in volatile weeks
- **Monthly**: 40%+ possible in adverse conditions

### Win Rate
- **Expected**: 35-45% (typical for aggressive systems)
- **Profit Factor**: 1.0-1.5 (risking more to make more)

### Volatility
- **Daily Equity Swings**: +/- 5-15% normal
- **Stress Events**: 25%+ daily moves possible in meme stocks

## How to Test

### Backtest
```bash
cd C:\tech\ibkr
python run_backtest.py --strategy aggressive_breakout
python run_backtest.py --strategy rsi_oscillator
python run_backtest.py --strategy momentum
```

### Compare All Strategies
```bash
python run_compare.py --report
```

### Parameter Sweep
```bash
python run_sweep.py --strategy aggressive_breakout
python run_sweep.py --strategy rsi_oscillator
```

### Walk-Forward Validation (Honest Test)
```bash
python run_walkforward.py --strategy momentum
```

### Run Live (Paper Trading)
```bash
# Daily bot
python run_live.py

# Intraday bot
python run_intraday.py
```

## Warning

**THIS IS EXTREMELY AGGRESSIVE** - Expect:
- Large drawdowns
- High volatility
- Frequent stop-outs
- Occasional gap losses
- Stressful equity curve swings

## Evidence from Research

The RESEARCH.md file (read it!) documents that:

1. **97% of Brazilian day traders lost money** persisting 300+ days
2. **<1% of Taiwanese traders reliably beat fees**
3. **5-minute trend rules showed zero value** in the most direct published test
4. **Sub-$5 stocks have documented -12%/yr headwind** from adverse drift
5. **Backtest Sharpe predicts live Sharpe with R-squared < 0.025** (essentially no relationship)

The aggressive mode explicitly accepts these risks in pursuit of higher returns.

## Safety Switches Still Active

1. **KILL_SWITCH file** - Create empty `KILL_SWITCH` file to halt all trading instantly
2. **Paper trading guard** - Requires explicit environment variable to trade real money
3. **Order sanity checks** - Still prevents fat-finger orders
4. **Max position cap** - Still enforced (just at 50% instead of 20%)
5. **Max positions count** - Still enforced (just at 15 instead of 5)

## Monitoring

Watch these files:
- `bot.log` - Daily bot activity
- `intraday.log` - Intraday bot activity
- `equity_*.csv` - Equity curves for backtests
- `trades_history.csv` - All trades executed

## If It Fails

Revert to conservative config:
```bash
git checkout config.yaml
git checkout bot/risk.py
git checkout bot/strategy.py
git checkout run_intraday.py
git checkout bot/config.py
```

Or manually edit `config.yaml` to restore conservative values.

## Next Steps

1. Run backtests to see what the aggressive config produces
2. Review equity curves and drawdowns
3. If acceptable, run paper trading for at least 1 month
4. Monitor daily logs and adjust parameters based on results
5. Consider position size adjustments if drawdowns exceed tolerance
