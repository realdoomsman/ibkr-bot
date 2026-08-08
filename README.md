# IBKR Trading Bot — a learn-by-doing starter project

This is an educational algorithmic trading project for Interactive Brokers (IBKR).
It trades a **paper account** (fake money, real market) using a simple, transparent
strategy, with real risk controls and an honest backtester.

## Read this first: the honest part

- **Most retail traders lose money — this is measured, not folklore.** The
  landmark study of Taiwan's complete market records (Barber, Lee, Liu & Odean)
  found fewer than 1% of day traders are predictably profitable in a typical
  year. A Brazilian study tracking everyone who started day trading futures
  (Chague & De-Losso) found 97% of those who persisted 300+ days lost money.
  EU brokers are required to disclose that typically 70–89% of retail CFD
  accounts lose money. Automation doesn't rescue you — it removes emotional
  mistakes but adds overfit backtests, optimistic paper fills, and cost drag.
- **Backtests lie by default.** Lookahead bias, ignored costs, and curve-fitting
  make almost any strategy look good on paper. This project's backtester avoids
  the classic traps and always compares you to buy-and-hold — the "do nothing"
  benchmark you must beat to justify existing.
- **Treat this as tuition.** The realistic goal is to learn market mechanics and
  trading-systems engineering, not to generate income. Paper trade for months.
  The bot has a hard guard: it refuses to connect to a live account unless you
  set an explicit environment variable. Leave that guard alone.
- Nothing in this project is financial advice.

## What's in the box

```
ibkr/
├── run_backtest.py     # backtest on demo / free real (Yahoo) / IBKR data
├── run_compare.py      # league table: all strategies x all symbols (+ HTML report)
├── run_portfolio.py    # ONE account trading the whole basket + regime table
├── run_sweep.py        # parameter sweep - see how fragile a "good" backtest is
├── run_walkforward.py  # walk-forward validation - the honest evaluation method
├── run_montecarlo.py   # bootstrap thousands of alternate years -> probabilities
├── run_analyze.py      # research note: stats + real headlines (+ AI read if enabled)
├── run_live.py         # one paper-trading cycle against IBKR (run daily)
├── config.yaml         # symbols, strategy parameters, risk limits
├── requirements.txt
└── bot/
    ├── config.py       # config loading + the live-trading guard
    ├── data.py         # demo bars, Yahoo daily bars + headlines, IBKR bars
    ├── strategy.py     # Strategy interface + 3 strategies (trend, reversion, momentum)
    ├── backtest.py     # no-lookahead backtester with commissions & slippage
    ├── portfolio.py    # portfolio-level backtester (shared cash, equal weight)
    ├── walkforward.py  # rolling optimize-then-test-out-of-sample engine
    ├── report.py       # self-contained HTML report with equity-curve charts
    ├── risk.py         # position sizing, daily-loss kill switch, order sanity checks
    ├── broker.py       # IBKR connection + risk-gated order placement
    ├── main.py         # one full trading cycle (data -> signal -> rebalance)
    └── ai_analyst.py   # optional Claude-powered second opinion (advisory only)
```

The design follows the standard bot architecture: **data → strategy → risk
manager → execution**, each in its own module. The strategy *proposes*; the
risk manager *disposes*. Strategy code contains zero broker code, which is what
lets the same strategy run in both the backtester and the live loop.

## Step 0 — Install Python

Python is not installed on this machine yet. Easiest way on Windows
(this project needs Python 3.10+; 3.12 is a safe choice):

```bash
winget install -e --id Python.Python.3.12
```

Close and reopen your terminal afterwards, then verify with `python --version`.

## Step 1 — Run your first backtest (no IBKR account needed)

```bash
cd C:\tech\ibkr
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run_backtest.py
```

This backtests the SMA crossover strategy on **synthetic random-walk data**.
Play with `--fast` and `--slow`. Two lessons hide in here:

1. On random data no strategy has a real edge — yet some parameter combos will
   *look* profitable. That's luck, and it's exactly how people fool themselves.
   (Formally: overfitting. The more variants you try, the more your best
   backtest is chance.)
2. Notice how commissions and slippage eat into every round trip.

### Then graduate to real data (still no IBKR account needed)

```bash
python run_backtest.py --source yahoo --symbol SPY
python run_backtest.py --source yahoo --symbol SPY --strategy momentum
python run_backtest.py --source yahoo --symbol SPY --stress    # costs doubled
```

Three strategies are included, one from each classic family (see
`bot/strategy.py`): `sma_cross` (trend following), `rsi_reversion` (mean
reversion with a trend filter), `momentum` (time-series momentum).

### The evaluation tools — this is where real learning happens

```bash
python run_sweep.py --symbol SPY           # every parameter combo, ranked
python run_walkforward.py --symbol SPY     # optimize on 2y, test on next 6m, roll
```

**The sweep** shows the gap between the *best* parameter combo and the
*median* one — that gap is roughly how much an over-fitter fools themself.
Look for plateaus of similar-performing neighbors, not lone spikes.

**Walk-forward** is the honest test: parameters are chosen on one window and
evaluated on data they never saw, repeatedly, rolling forward. Only the
stitched out-of-sample result counts. Walk-forward efficiency below ~0.5
means the tuning gains were mostly curve-fitting. Watch how the "best"
parameters jump around between windows — that instability is what a weak
edge looks like.

### The research desk

```bash
python run_compare.py --report          # league table + visual HTML report
python run_portfolio.py --strategy momentum   # one account, whole basket, regime table
python run_montecarlo.py --symbol SPY --strategy momentum   # outcome probabilities
python run_analyze.py --symbol NVDA     # stats + real headlines (+ AI read if enabled)
```

**Portfolio** shows the one real free lunch — diversification: uncorrelated
positions smooth the equity curve more than any indicator tweak. But beware
**basket-selection bias**: picking AAPL/MSFT/NVDA *today* to backtest the past
decade means picking known winners — hindsight in disguise.

**Monte Carlo** turns one backtest into thousands of resampled alternate
years, so you get probabilities instead of a single number ("20% chance of a
losing year; bad-case drawdown −20%"). If you can't stomach the 5th
percentile, you can't run the strategy — you'll abandon it mid-drawdown.

### The ML lab (signals → XGBoost trade filter → leak tests)

```bash
python run_ml_filter.py      # triple-barrier labels + walk-forward XGBoost
python run_ml_backtest.py    # cost-charged simulation of the filter's picks
pytest tests/ -q             # the leak-hunting test suite (50 tests)
```

- `bot/signals.py` — 5 classic strategies (MACD, RSI, Bollinger, golden
  cross, Donchian) as vectorized, mechanically lag-guaranteed functions.
  These are ML *features*, not trade recommendations.
- `run_ml_filter.py` — the realistic use of ML in trading: not predicting
  prices, but learning **which signal-days to skip**. Labels use López de
  Prado's Triple Barrier method; evaluation is walk-forward with an embargo
  gap (never shuffled K-fold, which leaks the future).
- `tests/` — perturbation tests that *prove* no feature sees the future:
  shock day k's prices, assert nothing before k+1 changes. Run these after
  any change to signals or features. A green suite is the only license to
  trust a backtest.

## Step 2 — Get an IBKR account + paper trading

1. Apply at [interactivebrokers.com](https://www.interactivebrokers.com)
   (Open Account → Start Application). Budget 15–60 minutes; approval typically
   takes 1–3 business days. Choose a **cash** account to start — simpler, no
   borrowed money, no auto-liquidations. You do **not** need to fund the
   account to use paper trading.
2. Every individual account comes with a free **paper trading account** with
   $1,000,000 in simulated money. In Client Portal: **User menu (head icon) →
   Settings → Account Settings → Paper Trading Account** — this shows your
   separate paper username (it's usually ready ~24h after account approval).
   You can reset the paper balance from the same page.
3. Optional but useful: on that same page set **"Share real-time market data" =
   Yes**. Without it the paper account runs on free *delayed* (~15 min) data —
   fine for this project's daily-bar strategy, distorting for anything intraday.
4. Download and install **Trader Workstation (TWS)**. (IB Gateway is the
   lighter headless alternative for later; TWS is better while learning because
   you can *see* your orders and positions. Same API — only the port differs.)
5. On the TWS login screen, flip the **Live/Paper toggle to Paper** and log in.
   TWS shows a "Simulated Trading" banner when you're on paper.

## Step 3 — Enable the API in TWS

In TWS: **File → Global Configuration → API → Settings**

- ✅ Check **Enable ActiveX and Socket Clients** — the master switch
- ❌ Uncheck **Read-Only API** — it's ON by default and silently blocks all
  order placement until you untick it
- Socket port: **7497** (TWS paper default — matches `config.yaml`)
- ✅ Keep **Allow connections from localhost only** checked (default, safest)
- ✅ Check **Download open orders on connection** — so the bot can see orders
  that already exist after a restart

Also worth doing: **Global Configuration → Lock and Exit → set Auto restart**
(instead of auto logoff), so TWS restarts itself daily rather than logging you
out. Note TWS still requires a manual 2FA re-authentication about once a week.

TWS must stay running while the bot works — your bot talks to TWS, and TWS
talks to IBKR.

## Step 4 — Run a paper-trading cycle

```bash
python run_live.py
```

This connects to TWS, reads your paper account equity, fetches daily bars for
each symbol in `config.yaml`, computes the strategy signal, and rebalances —
buying or selling through the risk manager. Run it once a day (manually, or via
Windows Task Scheduler after market close). Everything is logged to `bot.log`.

**Emergency stop:** create an empty file named `KILL_SWITCH` in this folder and
the bot will refuse to open any new position.

### Troubleshooting the connection

| Symptom | Cause / fix |
|---|---|
| Error 502 "Couldn't connect" | TWS isn't running, API not enabled, or wrong port (7497 = TWS paper, 4002 = Gateway paper) |
| Orders rejected / nothing happens | "Read-Only API" is still checked in TWS |
| Error 326 (client id in use) | Another script is using the same `client_id` — change it in `config.yaml` |
| Error 200 "No security definition" | Bad/ambiguous symbol — the bot uses SMART/USD stock contracts |
| Messages 2104/2106/2158 at startup | Normal! "Market data farm connection is OK" is informational, not an error |
| Brief disconnect near 23:45 ET | IBKR's nightly server reset — harmless for a once-a-day bot |
| Kicked out of TWS when bot logs in elsewhere | IBKR allows one login per username at a time — use the paper user for the bot |

## The safety rails (read `bot/risk.py`)

| Control | Default | What it prevents |
|---|---|---|
| Live-trading guard | on | Connecting to a real-money port without an explicit env-var opt-in |
| Max position size | 10% of equity | One bad trade sinking the account |
| Max open positions | 5 | Overconcentration |
| Daily loss kill switch | 2% | A bad day becoming a terrible day |
| Order sanity check | on | Fat-finger / buggy order sizes |
| `KILL_SWITCH` file | manual | Everything, instantly, no code needed |

One more that's on you: **paper fills are optimistic.** IBKR simulates fills
from the top of the book only, so paper results overstate live results. A
strategy that's marginally profitable on paper usually loses live.

## The optional AI module

`bot/ai_analyst.py` can ask Claude for a structured second opinion on each
symbol (bullish/bearish/neutral + confidence + key risks). It is **advisory
only**: the output is logged for your review and never places trades. To enable
it, get an API key from [platform.claude.com](https://platform.claude.com/) and set
`ANTHROPIC_API_KEY` in your environment. Each call costs a small amount of real
money in API tokens.

Why doesn't the AI just pick the trades? Because a language model has no
special knowledge of future prices, and "the AI said so" is not a testable,
backtestable rule. Use it the way you'd use a colleague's opinion: as one
input, logged and reviewed.

## Rules worth knowing (US markets, as of mid-2026)

- **The Pattern Day Trader rule is gone.** The old rule (4+ day trades in 5
  days in a margin account under $25,000 → restrictions) was eliminated
  effective June 4, 2026 (FINRA Rule 4210 amendments; IBKR adopted the change
  the same day). It's replaced by broker-set, risk-based intraday margin
  standards — so limits now vary by broker rather than by a fixed $25k line.
- **Cash account violations still exist.** In a cash account, selling something
  you bought with unsettled funds is a Good Faith Violation (3 in 12 months =
  90-day restriction). Stock trades settle **T+1** (next business day).
- **Margin means auto-liquidation at IBKR.** IBKR doesn't call you about margin
  problems — it liquidates positions automatically, in real time. Another
  reason to start with a cash account.
- **Taxes:** short-term gains are taxed as ordinary income, and the wash-sale
  rule disallows losses when you rebuy within 30 days — a real trap for
  frequent-trading strategies.
- **Market data:** the bot requests free delayed data (`reqMarketDataType(3)`),
  which is plenty for daily bars. Real-time subscriptions are cheap (a few
  $/month) but unnecessary for now.

## Where to go next

1. **Understand every line** of `strategy.py` and `backtest.py` before changing
   anything. If you can't explain why a trade happened, stop.
2. **Backtest on real data**: `python run_backtest.py --symbol SPY --ibkr`
   (with TWS running). Compare against the demo-data results.
3. **Write your own strategy**: subclass `Strategy`, implement
   `target_position()`, register it in `make_strategy()`. Keep it to 2–4
   parameters — more parameters = more ways to fool yourself.
4. **Learn the pitfalls by name**: lookahead bias, survivorship bias,
   overfitting, regime blindness, transaction-cost underestimation. Then learn
   **walk-forward validation** — optimize on one window, test on the next,
   roll forward — which is the standard honest way to evaluate a strategy.
5. **Paper trade one strategy for 3+ months** and keep a journal of every
   surprise. Only after live-vs-paper behavior is boring and well understood
   should real money even enter the conversation — and then at minimum size.
