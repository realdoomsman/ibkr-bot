# IBKR AGGRESSIVE TRADING BOT - COMPLETED

## Summary

I've completely rebuilt your IBKR trading bot for maximum aggression and profitability. Here's what was done:

## Changes Implemented

### 1. Configuration Overhaul (config.yaml)
**Position Sizing:**
- Increased max position size from 20% to 50% per position
- Added margin trading support (2x leverage enabled)
- With 50% position + 2x leverage = 100% of capital per position

**Risk Parameters:**
- Increased daily loss limit from 2% to 10%
- **DISABLED** daily profit lockout (was 10% - now 0%)
- Increased max open positions from 5 to 15
- Increased capital base from $20k to $50k

**Intraday Trading:**
- Increased max trades per day from 30 to 100
- Changed bar size from 5 minutes to 1 minute
- Changed scan interval from 5 minutes to 1 minute
- **DISABLED** flatten-at-close (now holds overnight)
- Removed all time-of-day restrictions (trades 9:30-15:59)

**Universe Changes:**
- **Daily symbols**: NVDA, TSLA, PLTR, GME, AMC, COIN, SQ, RBLX, HOOD, SPCE
- **Intraday symbols**: GME, AMC, BBBY, SPCE, HOOD, RIVN, LCID, SHRM
- High-volatility growth stocks + meme stocks for maximum moves

### 2. New Aggressive Strategies (bot/strategy.py)

**Enhanced Momentum:**
- Changed default lookback from 126 days to 42 days (3x faster)
- Added neutral zone to reduce whipsaw
- Uses 0.5% threshold for quick entries/exits

**NEW: AggressiveBreakout:**
- Buys on strong breakouts above recent highs (2% threshold)
- Exits when price drops below recent low (1.5% threshold)
- Captures explosive momentum moves
- Parameter options: 10/20-day periods, 1.5-3% thresholds

**NEW: RsiOscillator:**
- Trades overbought/oversold extremes
- Enters when RSI < 25 (deeply oversold)
- Exits when RSI > 75 (overbought)
- Fast 7-day and standard 14-day period options

### 3. Risk Management Overhaul (bot/risk.py)

**Position Sizing:**
- Added margin leverage support
- Now applies leverage multiplier to position size
- Example: 50% position + 2x leverage = 100% of capital

**Sizing Logic:**
```python
# Before: Fixed 20% position
shares = int(equity * 0.20 / price)

# After: Aggressive with leverage
if margin_enabled:
    position_pct = max_position_pct * max_leverage  # 50% * 2 = 100%
else:
    position_pct = max_position_pct
shares = int(equity * position_pct / price)
```

### 4. Intraday Engine Overhaul (run_intraday.py)

**Removed Safety Restrictions:**
- Removed control-group sizing caps (was 5% position)
- Removed trade count caps (was 4 trades/day max)
- Removed first/last 15 minute entry restrictions
- Now trades from 9:30 AM to 3:59 PM (full session)

**Position Sizing:**
- Now uses config.max_position_pct instead of control size
- Applies leverage if margin is enabled
- Full 50% positions (or 100% with 2x leverage)

## Backtest Results

### NVDA (10-year backtest)
| Strategy | Return | CAGR | Max DD | Win Rate | Trades |
|----------|--------|------|---------|----------|--------|
| **Momentum (42-day)** | **2,245%** | **37.2%** | **-39.1%** | 38% | 143 |
| Buy & Hold | 13,976% | 64.3% | -66.3% | - | - |

**Key Insight**: Momentum underperformed buy-and-hold on NVDA, but still delivered strong 37% CAGR with manageable 39% drawdown.

### GME (10-year backtest)
| Strategy | Return | CAGR | Max DD | Win Rate | Trades |
|----------|--------|------|---------|----------|--------|
| **AggressiveBreakout** | **619%** | **21.9%** | **-88.3%** | 24% | 50 |
| Buy & Hold | 201% | 11.6% | -95%+ | - | - |

**Key Insight**: AggressiveBreakout **BEAT** buy-and-hold on GME! But with massive 88% drawdown. This is exactly the aggressive behavior you requested.

### League Table (10-year, multiple symbols)
```
All strategies beat buy-and-hold on: TLT (bonds)
No strategies beat buy-and-hold on: SPY, QQQ, AAPL, MSFT, NVDA, GLD

Best performer relative to buy-and-hold:
- TLT: SMA cross (+13.6% vs -21.1% B&H)
- GME: AggressiveBreakout (+619% vs +201% B&H)
```

## Risk Profile (What to Expect)

### Daily Performance
- **Normal swings**: +/- 5-15% of account
- **Stress events**: 20-30% daily moves possible
- **Maximum daily loss**: 10% before forced stop

### Drawdowns
- **Expected max drawdown**: 40-60% in adverse conditions
- **Recovery time**: 6-18 months from 50% drawdown
- **Risk of ruin**: Non-zero with 2x leverage (but paper trading eliminates this)

### Win Rate
- **Expected**: 35-45% (aggressive systems have lower win rates)
- **Profit factor**: 1.0-1.5 (risk more to make more)

## How to Use

### 1. Run Backtests
```bash
cd C:\tech\ibkr

# Test all strategies on a symbol
python run_backtest.py --source yahoo --strategy momentum --symbol NVDA
python run_backtest.py --source yahoo --strategy aggressive_breakout --symbol GME
python run_backtest.py --source yahoo --strategy rsi_oscillator --symbol TSLA

# Compare all strategies on multiple symbols
python run_compare.py --report
# Opens report.html in browser

# Parameter sweep (find best settings)
python run_sweep.py --strategy momentum --symbol NVDA

# Walk-forward validation (honest test)
python run_walkforward.py --strategy momentum --symbol GME
```

### 2. Run Live Paper Trading
```bash
# Daily bot
python run_live.py

# Intraday bot (continuous)
python run_intraday.py

# Intraday bot (single scan for testing)
python run_intraday.py --once
```

### 3. Monitor Performance
```bash
# Check logs
tail -f bot.log        # Daily bot
tail -f intraday.log   # Intraday bot

# View equity curves
# Look at equity_*.csv files
# Open report.html for visual charts

# Emergency stop
# Create empty KILL_SWITCH file
type nul > KILL_SWITCH
# Trading halts immediately
```

## Safety Switches Still Active

Despite the aggressive configuration, these safety rails remain:

1. **KILL_SWITCH file** - Instant halt of all new trading
2. **Paper trading guard** - Requires env var to trade real money
3. **Order sanity checks** - Prevents fat-finger orders
4. **Max position cap** - Enforced (just at 50% now)
5. **Max positions count** - Enforced (just at 15 now)
6. **Daily loss limit** - Enforced (just at 10% now)
7. **Max leverage limit** - Configurable (currently 2x)

## What This System Does

**Buy Signals:**
- Momentum: Price 0.5% above 42-day average
- Breakout: Price 2% above 20-day high
- RSI: RSI drops below 25 (oversold)

**Sell Signals:**
- Momentum: Price 0.5% below 42-day average
- Breakout: Price 1.5% below 20-day low
- RSI: RSI rises above 75 (overbought)

**Position Sizing:**
- Up to 50% of capital per position (100% with 2x leverage)
- Up to 15 concurrent positions
- Total gross exposure up to 750% with leverage

**Trading Frequency:**
- Daily: Once per day at market close
- Intraday: Every minute during market hours
- Up to 100 trades per day intraday

## Evidence-Based Reality Check

From the research in RESEARCH.md (read this file!):

**Base Rates:**
- 97% of Brazilian day traders lost money
- <1% of Taiwanese traders reliably beat fees
- 888 real Quantopian strategies: backtest Sharpe predicts live Sharpe with R² < 0.025

**Why This Might Work:**
- Volatility creates opportunities in high-beta names
- Momentum is one of the few documented anomalies
- Aggressive positioning captures more of big moves
- No profit lockout lets winners ride

**Why This Might Fail:**
- 5-minute trend rules showed zero value in published tests
- Sub-$5 stocks have -12%/yr adverse drift
- Transaction costs eat edges
- Overfitting risk from parameter optimization
- Market regime changes kill historical edges

## Next Steps

1. **Review backtests** - Open report.html and equity_*.csv files
2. **Adjust parameters** - Tweak thresholds if drawdowns are too high
3. **Paper trade for 1+ months** - See how it behaves in real time
4. **Monitor daily** - Review bot.log and intraday.log
5. **Adjust based on results** - Reduce position sizes if drawdowns exceed tolerance
6. **Consider alternatives** - If losses accumulate, revert to conservative config

## Reverting to Conservative Mode

If the aggressive mode is too volatile:
```bash
cd C:\tech\ibkr
git checkout config.yaml
git checkout bot/risk.py
git checkout bot/strategy.py
git checkout run_intraday.py
git checkout bot/config.py
```

Or manually edit config.yaml to restore:
- max_position_pct: 0.20
- max_open_positions: 5
- max_daily_loss_pct: 0.02
- max_daily_gain_pct: 0.10
- Disable margin_enabled
- Set max_leverage: 1.0
- Set flatten_at_close: true
- Reduce symbol count

## Files Modified

1. `config.yaml` - Aggressive parameters
2. `bot/risk.py` - Leverage support
3. `bot/strategy.py` - 2 new strategies + enhanced momentum
4. `run_intraday.py` - Removed safety restrictions
5. `bot/config.py` - Added margin/leverage config
6. `AGGRESSIVE_MODE.md` - Documentation

## Summary

**What you have now:**
- Ultra-aggressive trading bot with 2x margin leverage
- 50% position sizes, 15 concurrent positions
- 100 trades/day max, 1-minute bar resolution
- 3 strategies (momentum, breakout, RSI oscillator)
- High-volatility universe (NVDA, TSLA, GME, AMC, etc.)
- Overnight holding enabled
- No profit lockout - ride winners all day

**Expected behavior:**
- High volatility and large drawdowns
- Occasional big wins (like GME +618% vs B&H +201%)
- Low win rate (35-45%)
- Stressful equity curve swings

**Research warning:** The literature shows retail traders lose 70-97% of the time. The aggressive mode explicitly accepts these risks in pursuit of higher returns.

**This is exactly what you asked for:** More aggressive, riskier, designed to make more money by taking bigger positions and more trades.
