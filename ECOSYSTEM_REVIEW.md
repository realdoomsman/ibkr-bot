# Ecosystem Review — our bot vs. the open-source trading world (2026-08-07)

Six research agents surveyed the major frameworks (freqtrade, NautilusTrader,
QuantConnect/Lean, hummingbot, mlfinlab, qlib, FinRL), mined IBKR/ib_insync
issue trackers, and read the post-mortem graveyard. Verdict in four buckets.
Full cited findings: workflow archive; distilled here for the repo.

## VALIDATED — the world confirms these choices (keep them)

1. **Fill-reports-over-position-cache + quiet-period clamps** — Nautilus and
   Lean converge on exactly the design our double-sell incident forced on us.
   We re-derived the reference architecture from one cheap accident.
2. **Software-side exit ladder** — broker-side TRAIL/STP orders on pennies
   mis-trigger off single bad prints and behave differently on paper vs live.
   Engine-computed exits sidestep it (cost: process liveness matters — Gap 3).
3. **Paper guard, real-API paper trading, alert dedupe hygiene** — stronger
   than freqtrade/hummingbot equivalents.
4. **EDGAR dilution/China blocklists** — a whole paid-SaaS category ($74+/mo)
   sells this exact signal; almost no retail bot checks filings at all.
5. **Polling architecture, REDUCING-style kill switch, full automation, daily
   TWS restart** — the documented survivor profile. Event-driven rewrite: NOT
   needed at 5-min cadence.
6. **ML stack is literature-grade** (triple-barrier, purged CV, uniqueness
   weights, advisory-first) — refine gates, not the stack.

## WARNINGS — documented failure modes we're still exposed to

1. **Daily-loss breaker is OFF** (user choice). Peer practice is unanimous
   the other way. Proposal on file: account-level ~5% breaker that blocks NEW
   entries only (never exits/flatten) + per-symbol loss memory + re-entry
   cooldown — log would-have-fired days for a month, decide at the gate.
2. **Ladder protection dies with the process** for up to 30 min (watchdog
   cadence). Mitigations: 1-2 min paging when positions are open (Gap 3);
   optionally rest RUNG SELLS as real GTC limit orders (profit-taking limits,
   not stops — directive-compatible).
3. **Paper fills are fantasy in thin books** — log NBBO at every order/fill
   (L1 is live now); friction-haircut shadow ledger becomes the headline P&L.
4. **No published edge exists below $5**; +12% sits in the documented
   exhaustion band. Pre-registered lab studies queued: tier-split, threshold
   sweep with RVOL confirm, flatten-value attribution. The one verified
   intraday edge (ORB on RVOL-ranked "stocks in play", Sharpe 2.4, >$5) is a
   third-engine candidate on paper.
5. **Point-in-time replay discipline** — research must consume archived daily
   universe/blocklist snapshots (we archive screens; extend to blocklists).

## GAPS WORTH CLOSING (ops-critical, in reviewed batches)

1. **Startup/reconnect reconciliation gate**: replay reqExecutions since last
   journal timestamp before ANY order; refuse to trade until the diff is clean.
2. **Cheap hard backstops (~25 lines)**: healthchecks.io dead-man's ping
   (alerts on watchdog SILENCE); session order-rate ceiling -> KILL_SWITCH;
   price plausibility check (reject limits far from last close).
3. **Seconds-cadence liveness while positions open**: reqCurrentTime probe,
   per-symbol tick-age gate, hard-assert marketDataType==1, 10197 handler.
4. **Order-lifecycle taxonomy**: in-flight-unknown state; cancel-then-place
   over modify; minTick rounding across the $1 boundary; stale-order reaper;
   errors 110/103 page instead of silent-retry.
5. **Exit escalation**: unfilled exit -> cancel(clamped) -> reprice -> after K
   tries, market. Pre-register N, K.
6. **Time discipline**: exchange calendar (early closes!), flatten hard-gate
   by 15:59:30, clock-sync monitoring, no API jobs 23:30-01:00 ET reset window.
7. **Halt awareness**: NASDAQ halts RSS + tick 49; block entries on halted/
   news-pending; halted-near-close = queued exit + page.
8. **ClientId architecture**: fixed id per engine; watchdog/flatten as master
   id 0 (can cancel anything); symbol-claim registry; 326/103 handling.
9. **Deploy verification**: compileall + import smoke + pytest after pull,
   auto-rollback on failure, SHA handshake, TWS precautions checkbox in runbook.

## GAPS (gate-eligible — evidence machinery, not live-loop)

10. **Scanner vocabulary**: time-of-day RVOL (the LEADING signal; ours is the
    lagging one), float/float-rotation, spread gate, IPO age, catalyst tags —
    enter as pre-registered filters/features through the promotion ledger.
11. **Statistical hardening**: Deflated Sharpe (we have honest trial counts —
    collect the benefit), CPCV with PBO, isotonic calibration + Brier in the
    ledger, lookahead-invariance test, logistic-regression benchmark floor.

## IGNORE (fashionable, not for us)

- Event-driven rearchitecture / adopting Nautilus wholesale (import patterns,
  not frameworks)
- Reinforcement learning in the live loop (FinRL's own docs concede the
  overfitting culture)
- Pump-detection classifiers, paid short-interest feeds, pre-market scraping
