"""Close EVERY open position in the paper account, immediately.

Cancels all working orders account-wide, then market-sells every long and
buys back any short, verifies the account is flat, and reports. Books
whatever P&L exists - winners and losers alike. Use during market hours.

This is the manual "get me out of everything" button (user directive
2026-08-05: close all open positions). The paper-account guard still
applies - it refuses to run against a live account.

Run (on whichever machine hosts TWS):
    .venv\\Scripts\\python.exe flatten_now.py
"""

from __future__ import annotations

import logging

from bot.broker import Broker, journal
from bot.config import load_config
from bot.risk import RiskManager

log = logging.getLogger("bot.flatten")
CLIENT_ID = 99


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    # User directive 2026-08-05: NO trading outside regular market hours.
    # No resting orders queued overnight either - orders exist only while
    # the market can fill them.
    import datetime as _dt
    from zoneinfo import ZoneInfo

    now = _dt.datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5 or not (_dt.time(9, 30) <= now.time() < _dt.time(15, 58)):
        raise SystemExit(
            "REFUSING: market is closed and after-hours/pre-market orders are "
            "banned (user directive). Run between 9:30 and 15:58 ET."
        )

    cfg = load_config()  # enforces the paper guard
    cfg.ib.client_id = CLIENT_ID
    broker = Broker(cfg, sleeve="manual")
    broker.connect()
    try:
        equity = broker.net_liquidation()
        risk = RiskManager(cfg.risk, day_start_equity=equity)

        # 1. Kill every working order, from every bot, account-wide.
        log.info("Cancelling ALL working orders (global cancel)...")
        broker.ib.reqGlobalCancel()
        broker.ib.sleep(3)

        # 2. Flatten, verify, retry - up to 3 passes.
        for attempt in range(1, 4):
            open_pos = [(p.contract.symbol, int(p.position))
                        for p in broker.ib.positions() if p.position != 0]
            if not open_pos:
                break
            log.info("Pass %d: %d open position(s): %s", attempt, len(open_pos),
                     ", ".join(f"{s} {q:+d}" for s, q in open_pos))
            for symbol, qty in open_pos:
                try:
                    if qty > 0:
                        broker.submit_market_order(symbol, "SELL", qty, risk,
                                                   last_price=0.0)
                        journal(symbol, "SELL", "manual_flatten", qty=qty,
                                note="[manual] close-all directive 2026-08-05")
                    elif symbol in broker.pending_order_symbols(actions=("BUY",)):
                        # After hours, a cover placed on an earlier pass RESTS
                        # until the next open - covering again would stack
                        # orders that ALL fill at 9:30 and flip us long.
                        log.info("%s: cover already resting - not duplicating", symbol)
                    else:
                        broker.buy_to_cover(symbol, -qty)
                except Exception:
                    log.exception("Failed to flatten %s - will retry", symbol)
            broker.ib.sleep(5)

        remaining = [(p.contract.symbol, int(p.position))
                     for p in broker.ib.positions() if p.position != 0]
        if remaining:
            resting = broker.pending_order_symbols()
            if all(s in resting for s, _ in remaining):
                log.info("MARKET CLOSED: flatten orders for %s are RESTING and "
                         "will fill at the next open (~9:30 ET). Do not re-run "
                         "before then.", [s for s, _ in remaining])
            else:
                log.error("STILL OPEN after 3 passes: %s - check TWS (halted "
                          "stock? market closed?)", remaining)
        else:
            log.info("ACCOUNT FLAT. Equity $%s. All positions closed, all "
                     "orders cancelled.", f"{broker.net_liquidation():,.0f}")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
