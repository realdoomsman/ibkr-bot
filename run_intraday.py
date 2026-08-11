"""Continuous intraday paper-trading engine.

Runs all session: scans every `scan_minutes` during US market hours, computes
the strategy on 5-minute bars, and rebalances its OWN symbol basket through
the same risk manager as the daily bot. Flattens everything before the close
(no overnight risk), then exits.

Intraday-specific rails on top of the usual ones:
  - max_trades_per_day hard cap (a runaway signal can't churn)
  - the daily-loss circuit breaker is checked EVERY scan, not once
  - flatten-at-close, so each day starts clean
  - KILL_SWITCH file blocks new entries within one scan interval

Honest limits, in plain terms:
  - Without a real-time data subscription, IBKR serves ~15-min DELAYED data.
    The mechanics work; the signals are stale. Fine for paper learning.
  - Intraday trading multiplies costs (more trades = more spread/slippage)
    and is the arena where retail loss statistics are measured. This engine
    exists to let you EXPERIENCE that on fake money, with rails.

Run:  python run_intraday.py           # loop until the close
      python run_intraday.py --once    # single scan pass (testing)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.broker import Broker
from bot.config import load_config
from bot.data import archive_intraday_bars, fetch_intraday_bars
from bot.risk import RiskManager
from bot.strategy import make_strategy

log = logging.getLogger("bot.intraday")

NY = ZoneInfo("America/New_York")
MARKET_OPEN = dt.time(9, 30)
MARKET_CLOSE = dt.time(16, 0)
FLATTEN_AT = dt.time(15, 55)  # start flattening 5 minutes before the bell


def market_phase(now: dt.datetime | None = None) -> str:
    """'premarket', 'open', 'flatten', or 'closed' (weekends yes; US holidays
    are not tracked - on a holiday every scan simply finds no fresh bars).

    'premarket' exists because the engines START at 9:28, two minutes before
    the bell. Treating pre-open as 'closed' made every scheduled start exit
    immediately - the fleet's oldest bug, masked for a week by louder
    failures (TWS down, connections refused, manual mid-day starts) and
    finally expressed on 2026-08-11, the first morning everything else
    worked. Pre-open means WAIT, never exit."""
    now = now or dt.datetime.now(NY)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if t < MARKET_OPEN:
        return "premarket"
    if MARKET_OPEN <= t < FLATTEN_AT:
        return "open"
    if FLATTEN_AT <= t < MARKET_CLOSE:
        return "flatten"
    return "closed"


def own_working_sells(broker, symbol) -> bool:
    """True if THIS clientId has active SELL orders resting for `symbol`."""
    my_id = broker.ib.client.clientId
    return any(
        t.contract.symbol == symbol and t.order.action == "SELL"
        and t.order.clientId == my_id
        and t.orderStatus.status in broker.ACTIVE_STATUSES
        for t in broker.ib.reqAllOpenOrders()
    )


def reconcile_symbol(symbol, broker) -> int:
    """Repair inconsistent states BEFORE strategy logic runs each scan.

    Returns the (possibly repaired) current position quantity.
    - Orphaned own SELL with no position (e.g. user sold manually in TWS):
      cancel it, or it will eventually fire into a naked short.
    - Own SELL resting against a HELD position: exits here are immediate
      market orders, so a resting own SELL can only be a leftover protective
      stop from before the no-stop directive - cancel it every scan until
      gone (self-healing; a one-shot cancel can miss its 5s confirmation).
    - Negative position: buy to cover - this bot must never carry a short.
      Skipped while a cover BUY is already working, or unfilled covers
      would stack across scans and flip the short into an unintended long.
    """
    qty = broker.position_qty(symbol)
    if qty == 0 and symbol in broker.pending_order_symbols(actions=("SELL",)):
        log.warning("%s: working SELL order(s) with no position - cancelling orphans", symbol)
        broker.cancel_symbol_orders(symbol, actions=("SELL",))
        qty = broker.position_qty(symbol)
    if qty > 0 and own_working_sells(broker, symbol):
        log.info("%s: cancelling stale resting SELL on held position (no-stop policy)", symbol)
        broker.cancel_symbol_orders(symbol, actions=("SELL",))
        qty = broker.position_qty(symbol)
    if qty < 0:
        if symbol in broker.pending_order_symbols(actions=("BUY",)):
            log.warning("%s: short, but a cover BUY is already working - waiting", symbol)
        else:
            broker.cancel_symbol_orders(symbol, actions=("SELL",))
            qty = broker.position_qty(symbol)  # fresh read: SELLs can fill during cancel
            if qty < 0:
                broker.buy_to_cover(symbol, -qty)
                qty = broker.position_qty(symbol)
    return qty


def scan_symbol(symbol, icfg, strategy, broker, risk, entry_allowed: bool) -> int:
    """One evaluation of one symbol. Returns trades executed (0 or 1)."""
    current_qty = reconcile_symbol(symbol, broker)

    if symbol in broker.pending_order_symbols(actions=("BUY",)):
        log.info("%s: buy order already working - skip", symbol)
        return 0

    bars = fetch_intraday_bars(broker.ib, symbol, strategy.warmup + 10, icfg.bar_size)
    archive_intraday_bars(symbol, bars)  # build the dataset - waste nothing
    if len(bars) < strategy.warmup:
        log.warning("%s: %d bars < warmup %d - skip", symbol, len(bars), strategy.warmup)
        return 0

    target = strategy.target_position(bars, current=1.0 if current_qty > 0 else 0.0)
    last = float(bars["close"].iloc[-1])
    log.info("%s: last=%.2f target=%.0f%% held=%d", symbol, last, 100 * target, current_qty)

    if target > 0 and current_qty == 0:
        if not entry_allowed:
            log.info("%s: entry signal but trade cap reached - watching only", symbol)
            return 0
        equity = broker.net_liquidation()
        qty = risk.position_size(equity, last)
        if qty > 0:
            # Second opinion from the trained meta-labeling filter - JOURNALED
            # ONLY, never gates. The nightly review keeps its scoreboard.
            try:
                from bot import filter_advisor
                from bot.broker import journal

                advice = filter_advisor.advise(symbol, no_network=True)
                if advice:
                    log.info("%s: filter p_win=%.2f -> %s (advisory only)",
                             symbol, advice["p_win"], advice["verdict"])
                    journal(symbol, "BUY", "filter_advice", signal_price=last,
                            note=f"[intraday] p={advice['p_win']:.3f} {advice['verdict']}")
            except Exception:
                log.debug("filter advice unavailable", exc_info=True)

            # Capped limit entry: fills like market normally, never chases a
            # spike more than 0.5%. NO protective stop - removed 2026-08-04 by
            # user directive ("no stop losses"). Exits come only from the
            # strategy signal or the end-of-day flatten.
            broker.submit_entry_limit(symbol, qty, risk, last)
            if broker.position_qty(symbol) > 0:
                return 1
    elif target == 0 and current_qty > 0:
        # Exit sequence hardened against the fills-during-cancel race:
        # 1. cancel any working SELLs (e.g. a manual TWS order) and WAIT for
        #    confirmation; abort if unconfirmed (double-sell risk)
        # 2. re-read the live position (a SELL may have filled meanwhile)
        # 3. sell only what is actually still held
        all_done, filled_during = broker.cancel_symbol_orders(symbol, actions=("SELL",))
        if not all_done:
            log.error("%s: cancel unconfirmed - aborting exit this scan (a working "
                      "SELL may still execute)", symbol)
            return 0
        if filled_during:
            log.info("%s: %d shares sold during cancel - selling remainder only",
                     symbol, filled_during)
        # Arithmetic bound beats the lagging cache (2026-08-04 double-sell).
        qty_now = broker.settled_position_qty(
            symbol, expected_max=current_qty - filled_during)
        if qty_now < 0:
            broker.buy_to_cover(symbol, -qty_now)
            return 1
        if qty_now == 0:
            log.info("%s: position already closed (stop did the exit)", symbol)
            return 0
        trade = broker.submit_market_order(symbol, "SELL", qty_now, risk, last)
        return 1 if trade is not None else 0
    return 0


def flatten(icfg, broker, risk) -> bool:
    """Close every intraday position before the bell (working SELLs cancelled
    first).

    Returns True only if every symbol VERIFIED flat - a submitted sell is not
    a filled sell (rejections and unfilled orders are routine in this tier),
    and with no stops, an unnoticed non-fill would ride overnight unprotected.
    The caller retries shortly otherwise.
    """
    all_clean = True
    for symbol in icfg.symbols:
        try:
            qty_pre = broker.position_qty(symbol)  # read BEFORE the cancel
            all_done, filled_during = broker.cancel_symbol_orders(symbol, actions=("SELL",))
            if not all_done:
                log.critical("FLATTEN %s: cancel unconfirmed - will retry", symbol)
                all_clean = False
                continue
            # Never size a sell off the cache right after a cancel raced fills.
            qty = broker.settled_position_qty(symbol, expected_max=qty_pre - filled_during) \
                if qty_pre > 0 else broker.position_qty(symbol)
            if qty < 0 and symbol not in broker.pending_order_symbols(actions=("BUY",)):
                broker.buy_to_cover(symbol, -qty)
                if broker.position_qty(symbol) != 0:
                    all_clean = False
            elif qty > 0:
                log.info("FLATTEN %s: selling %d before the close", symbol, qty)
                trade = broker.submit_market_order(symbol, "SELL", qty, risk, last_price=0.0)
                # Judge by the ORDER's fill report, never the position cache:
                # the cache lags fills, and re-selling on its stale word is
                # the exact double-sell that went short on 2026-08-04.
                filled = broker.wait_order_done(trade)
                if trade is None or filled < qty:
                    log.warning("FLATTEN %s: %d/%d filled - will retry remainder",
                                symbol, filled, qty)
                    all_clean = False
        except Exception:
            log.exception("FLATTEN %s failed - will retry", symbol)
            all_clean = False
    return all_clean


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous intraday paper engine")
    parser.add_argument("--once", action="store_true", help="Single scan pass, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("intraday.log", encoding="utf-8")],
    )
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    cfg = load_config()  # enforces the paper guard
    icfg = cfg.intraday
    if not icfg.enabled:
        raise SystemExit("intraday.enabled is false in config.yaml")

    strategy = make_strategy(icfg.strategy, **icfg.params)
    cfg.ib.client_id = icfg.client_id  # don't collide with the daily bot
    broker = Broker(cfg)

    # TWS may not be up yet (or mid-restart) - keep retrying instead of dying
    # for the day. A missed connection should cost minutes, not a session.
    while True:
        try:
            broker.connect()
            break
        except SystemExit:
            raise  # the paper-account guard - never retry past that
        except Exception as e:
            if args.once or market_phase() == "closed":
                raise
            log.warning("Can't reach TWS (%s) - retrying in 2 minutes", e)
            import time as _time

            _time.sleep(120)

    try:
        equity = broker.net_liquidation()
        prev = broker.previous_day_equity()
        risk = RiskManager(cfg.risk, day_start_equity=prev if prev and prev > 0 else equity)
        log.info(
            "Intraday engine up. Equity $%s | basket %s | strategy %s %s | scan every %dmin",
            f"{equity:,.0f}", icfg.symbols, icfg.strategy, icfg.params, icfg.scan_minutes,
        )

        # Pre-warm the trade filter and its data caches OUTSIDE the order path.
        try:
            from bot import filter_advisor

            filter_advisor.prewarm(icfg.symbols)
        except Exception:
            log.debug("filter prewarm failed", exc_info=True)

        # Legacy protective stops (pre-no-stop-directive) need no one-shot
        # sweep here: reconcile_symbol cancels any own resting SELL on a held
        # position EVERY scan, which also self-heals a cancel that missed its
        # confirmation window.

        trades_today = 0
        flattened = False
        while True:
            phase = market_phase()
            try:  # heartbeat for the watchdog: proof this loop is alive
                Path("heartbeat_intraday.txt").write_text(
                    dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            except Exception:
                pass

            # Mid-session disconnect (TWS restart, network blip): reconnect
            # instead of burning scans against a dead socket.
            if phase != "closed" and not broker.ib.isConnected():
                log.warning("Lost TWS connection - attempting reconnect")
                try:
                    broker.connect()
                except SystemExit:
                    raise
                except Exception:
                    log.warning("Reconnect failed - will retry next scan")
                    broker.ib.sleep(icfg.scan_minutes * 60)
                    continue

            if phase == "premarket":
                broker.ib.sleep(20)  # the bell is minutes away - wait for it
                continue

            if phase == "open":
                # The cap gates ENTRIES only - exits, stops, and repairs must
                # always run, or a capped day strands positions unmanaged.
                for symbol in icfg.symbols:
                    try:
                        entry_allowed = trades_today < icfg.max_trades_per_day
                        trades_today += scan_symbol(
                            symbol, icfg, strategy, broker, risk, entry_allowed
                        )
                    except Exception:
                        log.exception("Scan error on %s - continuing", symbol)

            elif phase == "flatten":
                if icfg.flatten_at_close and not flattened:
                    flattened = flatten(icfg, broker, risk)
                    if flattened:
                        log.info("Day done: %d trades. Waiting for the close.", trades_today)
                    else:
                        log.warning("Flatten incomplete - retrying in 45s")
                        broker.ib.sleep(45)
                        continue

            else:  # closed
                if args.once:
                    log.info("Market closed - nothing to scan.")
                    break
                log.info("Market closed. Exiting (Task Scheduler restarts me tomorrow).")
                break

            if args.once:
                log.info("--once: single pass complete (%d trades)", trades_today)
                break
            broker.ib.sleep(icfg.scan_minutes * 60)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
