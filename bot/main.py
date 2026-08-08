"""One trading cycle: fetch data, compute signals, rebalance the paper account.

Deliberately NOT a 24/7 loop. For daily-bar strategies you run this once per
day (manually, or via Windows Task Scheduler after market close). Fewer moving
parts, fewer ways to lose money while you sleep, and much easier to reason
about while learning.
"""

from __future__ import annotations

import logging

from .broker import Broker
from .config import BotConfig
from .data import fetch_daily_bars
from .risk import RiskManager
from .strategy import make_strategy
from . import ai_analyst

log = logging.getLogger("bot.main")

# User directive 2026-08-05: "close all positions before the market closes."
# Daily trend-following holds for days-to-weeks, so its ENTRIES are retired;
# the bot still manages exits for anything held. Flip only at a gate.
DAILY_ENTRIES_ENABLED = False


def run_cycle(cfg: BotConfig) -> None:
    strategy = make_strategy(cfg.strategy.name, **cfg.strategy.params)
    broker = Broker(cfg)
    broker.connect()
    try:
        equity = broker.net_liquidation()
        log.info("Account equity: $%s", f"{equity:,.2f}")
        # Baseline the daily-loss breaker on YESTERDAY's close, not on equity
        # captured seconds ago - otherwise an overnight drop is invisible to it.
        prev = broker.previous_day_equity()
        if prev and prev > 0:
            day_start = prev
            log.info("Daily-loss baseline: previous close $%s", f"{prev:,.2f}")
        else:
            day_start = equity
            log.warning(
                "Previous-day equity unavailable - using current equity as the "
                "daily-loss baseline (breaker blind to overnight moves this run)"
            )
        risk = RiskManager(cfg.risk, day_start_equity=day_start)

        for symbol in cfg.strategy.symbols:
            try:
                _process_symbol(symbol, cfg, strategy, broker, risk)
            except Exception:
                log.exception("Error processing %s - continuing with next symbol", symbol)
    finally:
        broker.disconnect()
    log.info("Cycle complete.")


def _process_symbol(symbol, cfg, strategy, broker, risk) -> None:
    if symbol in broker.pending_order_symbols(actions=("BUY",)):
        log.warning("%s: a buy order is already working at IBKR - skipping this cycle", symbol)
        return

    bars = fetch_daily_bars(broker.ib, symbol, days=cfg.strategy.history_days)
    if len(bars) < strategy.warmup:
        # A data shortfall must NEVER read as an exit signal - the strategy
        # returns 0.0 below warmup, and selling on that would liquidate
        # positions because of a data hiccup.
        log.warning(
            "%s: got %d bars but strategy needs %d - skipping, no trade",
            symbol, len(bars), strategy.warmup,
        )
        return

    current_qty = broker.position_qty(symbol)
    target = strategy.target_position(bars, current=1.0 if current_qty > 0 else 0.0)
    last_price = float(bars["close"].iloc[-1])
    log.info(
        "%s: last=%.2f target=%.0f%% current=%d shares",
        symbol, last_price, 100 * target, current_qty,
    )

    # Optional AI second opinion - logged for your review, never trades on its
    # own. Isolated so an API failure can never block the trade logic below.
    if ai_analyst.is_enabled():
        try:
            from .data import fetch_headlines

            ai_analyst.analyze_symbol(symbol, bars, headlines=fetch_headlines(symbol))
        except Exception:
            log.warning("AI analysis failed for %s - continuing without it", symbol, exc_info=True)

    if target > 0 and current_qty == 0 and not DAILY_ENTRIES_ENABLED:
        # (Note for the gate that reconsiders this: this strategy family held
        # the evidence sweep's ONLY conditional pass.)
        log.info("%s: entry signal IGNORED - daily sleeve is exit-only "
                 "(no-overnight-holds directive 2026-08-05)", symbol)
        return
    if target > 0 and current_qty == 0:
        equity = broker.net_liquidation()
        qty = risk.position_size(equity, last_price)
        if qty > 0:
            # Advisory-only second opinion from the trained filter (journaled).
            try:
                from . import filter_advisor
                from .broker import journal

                # entry_next_open: this bot decides after the close and fills
                # at TOMORROW's open, so score tomorrow's decision row (which
                # sees today's completed bar - matching training semantics).
                advice = filter_advisor.advise(symbol, entry_next_open=True)
                if advice:
                    log.info("%s: filter p_win=%.2f -> %s (advisory only)",
                             symbol, advice["p_win"], advice["verdict"])
                    journal(symbol, "BUY", "filter_advice", signal_price=last_price,
                            note=f"[daily] p={advice['p_win']:.3f} {advice['verdict']}")
            except Exception:
                log.debug("filter advice unavailable", exc_info=True)
            # Capped limit: behaves like a market order in normal conditions
            # but refuses to pay more than 0.5% above the last close. The
            # order RESTS (no cancel) because daily runs happen after the
            # close - it fills at the next open, or expires end of that day.
            broker.submit_entry_limit(
                symbol, qty, risk, last_price, wait_s=3, cancel_unfilled=False
            )
    elif target == 0 and current_qty > 0:
        # No after-hours orders (user directive 2026-08-05). This bot runs
        # after the close, so it may only LOG exit intent; actual selling
        # happens via the manual flatten or the intraday engines in RTH.
        import datetime as _dt
        from zoneinfo import ZoneInfo

        now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5 or not (_dt.time(9, 30) <= now_et.time() < _dt.time(16, 0)):
            log.warning("%s: exit signal but market is CLOSED - no after-hours "
                        "orders (user directive). Position held; sell it in "
                        "RTH (flatten_now.py) if still signaled.", symbol)
            return
        broker.submit_market_order(symbol, "SELL", current_qty, risk, last_price)
    else:
        log.info("%s: no action needed", symbol)
