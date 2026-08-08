"""Last-half-hour momentum sleeve (Gao, Han, Li & Zhou, JFE 2018).

The published effect: on liquid ETFs, the FIRST half-hour return (measured
from the prior close, so it includes the overnight move) positively predicts
the LAST half-hour return. This sleeve trades the long-only version on SPY:

    at ~15:30 ET:  r_first = (price at 10:00) / (prior session close) - 1
                   if r_first > threshold: buy SPY with this sleeve's budget
    at the close:  exit via Market-on-Close (the closing auction - no spread)

Honesty notes: the effect's R-squared was ~1.6% in-sample and post-publication
evidence is mixed (decay/crowding) - this sleeve is a small, cheap, documented
experiment, not a profit engine. SPY spreads ~1 cent make it one of the few
intraday trades a retail account can afford to test. Runs on delayed data,
which lags the signal ~15 minutes; the review measures what that costs.

Scheduled 15:28 ET weekdays; exits by 16:01.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from bot.broker import Broker
from bot.config import load_config
from bot.data import fetch_intraday_bars

log = logging.getLogger("bot.lasthour")

NY = ZoneInfo("America/New_York")
SYMBOL = "SPY"
BUDGET_PCT = 0.15       # aggressive profile (within the 20% sanity cap)
THRESHOLD = 0.0005       # first-half-hour return must exceed +5bps to trade
CLIENT_ID = 46


def first_half_hour_return(bars) -> float | None:
    """(price at ~10:00 today) / (prior session close) - 1, from 5-min bars."""
    today = dt.datetime.now(NY).date()
    dates = bars.index.date
    today_bars = bars[dates == today]
    prior_bars = bars[dates < today]
    if today_bars.empty or prior_bars.empty:
        return None
    prior_close = float(prior_bars["close"].iloc[-1])
    # STRICT '<': IBKR labels bars by START time, so the 10:00 bar closes at
    # ~10:05. The price AT 10:00 is the close of the 09:55 bar - anything
    # later contaminates the published 9:30-10:00 window.
    ten_am = dt.time(10, 0)
    upto_ten = today_bars[today_bars.index.time < ten_am]
    if upto_ten.empty:
        return None
    return float(upto_ten["close"].iloc[-1]) / prior_close - 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("sleeves.log", encoding="utf-8")],
    )
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    # SLEEVE RETIRED per user directive 2026-08-05: "no index funds or
    # anything like QQQ" - SPY is an index ETF. (For the record: this and the
    # overnight sleeve were the two RESEARCH-BACKED strategies - Gao 2018.
    # Re-enable only at an evaluation gate if the directive changes.)
    log.info("Last-hour sleeve DISABLED (user directive 2026-08-05: no index ETFs).")
    return

    now = dt.datetime.now(NY)
    if now.weekday() >= 5 or not (dt.time(15, 25) <= now.time() <= dt.time(15, 55)):
        log.info("Outside the 15:25-15:55 ET window (now %s) - nothing to do", now.time())
        return

    cfg = load_config()
    cfg.ib.client_id = CLIENT_ID
    broker = Broker(cfg, sleeve="lasthour")
    broker.connect()
    try:
        if broker.position_qty(SYMBOL) != 0 or SYMBOL in broker.pending_order_symbols():
            log.warning("%s already has a position/order - skipping today", SYMBOL)
            return

        bars = fetch_intraday_bars(broker.ib, SYMBOL, min_bars=90)
        r_first = first_half_hour_return(bars)
        if r_first is None:
            log.warning("Could not compute first-half-hour return - no trade")
            return
        log.info("First-half-hour return (incl overnight): %+.3f%%", 100 * r_first)

        if r_first <= THRESHOLD:
            log.info("Signal not positive enough - staying flat (that IS the strategy)")
            return

        from bot.risk import RiskManager

        equity = broker.net_liquidation()
        risk = RiskManager(cfg.risk, day_start_equity=equity)
        budget = risk.effective_capital(equity) * BUDGET_PCT
        last = float(bars["close"].iloc[-1])
        qty = int(budget // last)
        if qty < 1:
            log.info("Budget too small for one share of %s - no trade", SYMBOL)
            return

        # cap 0.5%, not 0.1%: `last` is a ~15-min-delayed price, and a tight
        # cap would only fill on days the market DIPPED - inverting the
        # momentum signal this sleeve is testing.
        broker.submit_entry_limit(SYMBOL, qty, risk, last, cap_pct=0.005)

        # True-up: wait until no BUY is working before sizing the exit, so a
        # late fill can never leave shares without an MOC.
        for _ in range(12):
            if SYMBOL not in broker.pending_order_symbols(actions=("BUY",)):
                break
            broker.ib.sleep(1)
        pos = broker.position_qty(SYMBOL)
        if pos > 0:
            broker.place_moc_sell(SYMBOL, pos, risk=risk, last_price=last)
            log.info("In for the last half hour: %d %s, exit at the closing auction", pos, SYMBOL)
            # Final safety net: if extra shares landed after the MOC was sized,
            # cover them with a second MOC rather than stranding them overnight.
            broker.ib.sleep(2)
            extra = broker.position_qty(SYMBOL) - pos
            if extra > 0:
                log.warning("%s: %d late-fill shares - adding a second MOC", SYMBOL, extra)
                broker.place_moc_sell(SYMBOL, extra, risk=risk, last_price=last)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
