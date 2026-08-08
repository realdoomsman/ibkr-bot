"""Overnight premium sleeve (Cooper, Cliff & Gulen 2008 and successors).

The published effect: essentially the entire US equity premium has accrued
OVERNIGHT (close -> open); intraday (open -> close) returns average ~zero.
This sleeve holds QQQ overnight only:

    buy leg  (~15:57 ET): buy QQQ with this sleeve's budget, capped limit
    sell leg (~09:31 ET): sell at the next open

It is deliberately the mirror image of the intraday engine (which is flat
overnight) - between them, the daily reviews will show which side of the
close the money is actually on, which is the cleanest experiment this
account can run.

Honesty notes: the overnight effect is documented but debated (crowding,
decay, and it eats gap risk - the position holds through the night with NO
stop, which is why the budget is small). QQQ spreads ~1-2bps keep the
round trip cheap.

Scheduled weekdays: buy leg 15:57, sell leg 09:31.
Run manually:  python run_overnight.py --leg buy|sell
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from bot.broker import Broker
from bot.config import load_config
from bot.data import fetch_intraday_bars
from bot.risk import RiskManager

log = logging.getLogger("bot.overnight")

NY = ZoneInfo("America/New_York")
SYMBOL = "QQQ"
BUDGET_PCT = 0.15       # aggressive profile (within the 20% sanity cap)
CLIENT_ID = 47


def main() -> None:
    parser = argparse.ArgumentParser(description="Overnight premium sleeve")
    parser.add_argument("--leg", choices=["buy", "sell"], required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("sleeves.log", encoding="utf-8")],
    )
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    # SLEEVE RETIRED per user directives 2026-08-05 ("no index funds or
    # anything like QQQ" + "close all positions before the market closes" -
    # this sleeve exists to hold QQQ overnight, so both rules kill it).
    # The SELL leg still runs so any leftover position gets liquidated.
    if args.leg == "buy":
        log.info("Overnight sleeve DISABLED (user directive 2026-08-05: no "
                 "index ETFs, no overnight holds). Buy leg does nothing.")
        return

    now = dt.datetime.now(NY)
    if now.weekday() >= 5:
        log.info("Weekend - nothing to do")
        return
    # The sell leg is the ONLY exit for a stop-less overnight position, so its
    # window spans the session: a missed 09:31 run must not extend the hold by
    # another full day and night. (Late sells are safe - guards are idempotent.)
    # Sell window starts 9:31 - never pre-market (user directive 2026-08-05).
    windows = {"buy": (dt.time(15, 45), dt.time(16, 0)), "sell": (dt.time(9, 31), dt.time(15, 30))}
    lo, hi = windows[args.leg]
    if not (lo <= now.time() <= hi):
        log.info("Outside the %s-leg window %s-%s ET (now %s) - nothing to do",
                 args.leg, lo, hi, now.time())
        return

    cfg = load_config()
    cfg.ib.client_id = CLIENT_ID
    broker = Broker(cfg, sleeve="overnight")
    broker.connect()
    try:
        equity = broker.net_liquidation()
        risk = RiskManager(cfg.risk, day_start_equity=equity)
        held = broker.position_qty(SYMBOL)

        if args.leg == "buy":
            if held != 0 or SYMBOL in broker.pending_order_symbols():
                log.warning("%s already has a position/order - skipping buy leg", SYMBOL)
                return
            bars = fetch_intraday_bars(broker.ib, SYMBOL, min_bars=30)
            last = float(bars["close"].iloc[-1])
            budget = risk.effective_capital(equity) * BUDGET_PCT
            qty = int(budget // last)
            if qty < 1:
                log.info("Budget too small for one share of %s - no trade", SYMBOL)
                return
            # 0.5% cap: `last` is ~15-min delayed; a tighter cap would only
            # fill on days QQQ sagged into the close, biasing the experiment.
            broker.submit_entry_limit(SYMBOL, qty, risk, last, cap_pct=0.005)
            log.info("Holding %d %s overnight (no stop by design - gap risk is the bet)",
                     broker.position_qty(SYMBOL), SYMBOL)
        else:  # sell leg
            if held <= 0:
                log.info("No %s position to sell at the open - nothing to do", SYMBOL)
                return
            bars = fetch_intraday_bars(broker.ib, SYMBOL, min_bars=10)
            last = float(bars["close"].iloc[-1])
            broker.submit_market_order(SYMBOL, "SELL", held, risk, last)
            log.info("Overnight round trip closed: sold %d %s at the open", held, SYMBOL)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
