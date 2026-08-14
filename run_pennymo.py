"""Penny momentum sleeve: catch the mover, DCA out into strength, keep a moon bag.

The user's playbook, encoded:
  1. FIND    - watch the Penny Radar names (high-volume, low-priced movers);
               rescan the market every ~30 min for fresh ones.
  2. ENTER   - when one is genuinely moving (>=12% above today's open AND still
               within 15% of the day's high), buy ~$2,000 with a capped limit.
  3. LADDER  - sell 1/3 at +8%, another 1/3 at +15% from entry (user
               directives 2026-08-05: "take profits more aggressively",
               "sells when it's up" - bank fast, everything dies at the
               bell anyway).
  4. MOON BAG- the last third trails 15% below its high-water mark after both
               rungs, and a BREAKEVEN FLOOR arms after the first rung: once
               +15% has banked, the rest exits if price falls back to entry -
               a banked winner is never allowed to become a bag (exit-lab
               tournament winner, 2026-08-05).
  5. EOD FLATTEN - user directive 2026-08-05: "close all positions before the
     market closes." Everything is sold in the 15:55 window, INCLUDING
     underwater positions - the end-of-day flatten supersedes the no-stop
     rule and books whatever the day produced. Intraday, there are still no
     stop-losses: below entry we hold until the closing bell decides.

Honest limits, stated once and loudly:
  - Delayed data (~15 min) means every entry here is LATE by design. This
    sleeve is the single best argument for the real-time data subscription.
  - IBKR's paper simulator fills these thin names generously. Treat this
    sleeve's P&L as an UPPER BOUND, not a promise - the nightly review
    tags it [penny] and scores it separately.
  - Underwater positions are held (no intraday stop) but only until the
    bell: the 15:55 flatten books every result, so bags can no longer live
    past a single session. penny_radar.csv keeps scoring what the spikes do
    afterward - measuring what the flatten rule costs or saves.

Rails: max 2 new entries/day, max 3 concurrent penny positions, one attempt
per symbol per day (no re-chasing a faded pump), no entries after 15:30,
never touches another sleeve's symbols (baskets + SPY/QQQ excluded).

Run:  python run_pennymo.py           # loop until the close
      python run_pennymo.py --once    # single pass (testing)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

from bot.broker import Broker, journal
from bot.config import load_config
from bot.data import archive_intraday_bars, fetch_intraday_bars
from bot.risk import RiskManager
from run_intraday import market_phase

log = logging.getLogger("bot.pennymo")

CLIENT_ID = 49
# User directive 2026-08-12: entries sized at 30% OF THE ACCOUNT (was a
# fixed $2,000 ~= 10%). With MAX_OPEN=3 that is up to ~90% of equity
# deployed at once in no-stop positions - consequence stated, user owns it.
# Sized off LIVE equity at entry time; FALLBACK_BUDGET if the read fails.
ENTRY_FRACTION = 0.30
FALLBACK_BUDGET = 6000.0

# Dip-buy SHADOW LOGGER (2026-08-14): the user's SCKT hypothesis cleared the
# causal replay bar (diplab v2: first -15% retrace on an established big
# runner = +6.2%/64% win over 337 events, avg further drawdown -22%). NO
# ORDERS - would-be entries are journaled (kind=shadow_dip_entry) so the
# Sept-4 gate judges replay + live shadow evidence together before wiring.
SHADOW_DIP_ARM = 1.50       # running high >= +50% over today's open arms it
SHADOW_DIP_RETRACE = 0.15   # first -15% pullback off the running high
SHADOW_DIP_LOGGED: dict = {}  # symbol -> date logged (once per day)

# User directive 2026-08-14: no NEW trades 9:30-9:45 (the opening range
# "typically makes the day's highs and lows") UNLESS the premarket looks
# good AND there is solid demand on the buy orders. Mechanized: the sleeve
# already requires an early-premarket-high BREAK (the premarket-quality
# half); the demand half = NBBO bid size >= DEMAND_RATIO x ask size
# (Polygon real-time book). FAIL-CLOSED inside the window: can't read the
# book -> wait for 9:45. Entries after 15:45 were already blocked
# (ENTRY_CUTOFF); EXITS always run - the 15:55 flatten is its own directive.
ENTRY_START = dt.time(9, 45)
DEMAND_RATIO = 1.5
MAX_NEW_PER_DAY = 2
MAX_OPEN = 3
SCAN_MINUTES = 5
REFRESH_EVERY = 6          # re-run the mover screen every 6 scans (~30 min)
ENTRY_GAIN = 0.12          # today's move from the open that counts as "moving"
NEAR_HIGH = 0.85           # must still be within 15% of the day's high
# Band history (every change measured before/at wiring):
#   PMH-break cohort by price tier (net/trade, win, n):
#     sub-$1   +5.36%  66%  n=713
#     $1-3     +2.82%  52%  n=1,188
#     $3-7     -0.16%  44%  n=1,470
#     $2-300   -1.15%  39%  n=4,293
#     $5-100   -1.52%  37%  n=3,225  (break+early -1.31%, n=2,083)
# 2026-08-11: evidence set 0.20-3.00 (user delegated).
# 2026-08-13: USER DIRECTIVE overrides - "pick between $5-$100" then
# "$5-$1000" top, confirmed via question, WITH the -1.3%/trade measurement
# stated to them first. Their account, their call, ON RECORD; the Sept-4
# gate judges live results vs the band evidence. ORB harness for this
# territory remains the queued lab build.
MIN_PRICE, MAX_PRICE = 5.00, 1000.0
MIN_DVOL_M = 3.0           # $3M/day min - "high volume" for the penny tier
RUNGS = (1.08, 1.15)       # DCA-out levels: +8% and +15% FROM ENTRY. Entry is
                           # already ~13% above the open, so these = roughly
                           # +22% and +30% days from the open - reachable on
                           # real movers, unlike the old +47% ask (user:
                           # "most stocks dont go up 30% in a day" - correct).
RUNG_FRAC = 1 / 3
TRAIL = 0.10               # last third trails 10% below its high-water mark
BREAKEVEN_FLOOR = True     # after rung 1 banks, exit the rest if price
                           # round-trips to entry (never give a winner back)
ENTRY_CUTOFF = dt.time(15, 45)  # user directive 2026-08-10: trade the WHOLE
# session. Only the last 10 min stay closed - that window belongs to the
# 15:55 EOD flatten (an entry racing the flatten's cancel->sell sequence is
# the stale-cache double-sell class of bug again). Late entries have less
# rung runway before the forced flatten; the review buckets entries by time
# so the Sept gate can judge late vs early on evidence.
STATE_FILE = Path("pennymo_state.json")

# Symbols this sleeve must NEVER trade: the other sleeves' instruments.
# Two engines managing one account-wide position would sell each other's
# shares (e.g. the intraday 15:55 flatten force-selling a penny moon bag
# below entry - a stop-loss by accident). Populated from config in main().
EXCLUDE: set[str] = {"SPY", "QQQ"}

# Refuse entries in names with ACTIVE DILUTION FILINGS on record (424B
# takedowns/ATMs, fresh S-1/S-3/EFFECT - see bot/edgar.py). This is not a
# risk-appetite setting: a spike the company itself is selling into is the
# documented collapse mechanism in this tier (FINRA 22-25), and with no
# stop losses this sleeve cannot exit the aftermath. Flip to False to trade
# them anyway - the radar keeps measuring flagged names either way.
SKIP_DILUTION_FLAGS = True
#: symbol -> sec_flags string, refreshed from penny_radar.csv each scan
DILUTION: dict[str, str] = {}
#: symbol -> (price, dollar_vol_m) from the radar - feeds the RVOL estimate
RADAR_STATS: dict[str, tuple] = {}
#: CIK -> symbol map for the EDGAR tripwire (built lazily as symbols appear)
CIKS: dict[str, str] = {}
#: symbol -> (date, pm_high, pm_high_time) - one extended-hours fetch per day
PM_HIGHS: dict[str, tuple] = {}
#: symbols already logged as late-PMH-skipped today (log once, not per scan)
LATE_PMH_LOGGED: set = set()


def pm_high_today(broker: Broker, symbol: str):
    today = dt.date.today().isoformat()
    cached = PM_HIGHS.get(symbol)
    if cached and cached[0] == today:
        return cached[1], cached[2]
    from bot.scanner import premarket_high

    v, t = premarket_high(broker.ib, symbol)
    PM_HIGHS[symbol] = (today, v, t)
    return v, t


def notify(title: str, msg: str, priority: str = "high",
           tags: str = "rotating_light") -> None:
    """Push to the user's phone via the watchdog's ntfy topic. Best effort.
    Default priority/tags = alarm style; trade notifications pass
    priority='default' so they inform without ringing like a fire drill."""
    try:
        import urllib.request

        from run_watchdog import _ntfy_topic

        topic = _ntfy_topic()
        if topic:
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"),
                headers={"Title": title, "Priority": priority, "Tags": tags})
            urllib.request.urlopen(req, timeout=10)
    except Exception:
        log.debug("ntfy push failed", exc_info=True)


def shadow_dip_check(symbol: str, bars) -> None:
    """Journal the FIRST would-be dip-buy of the day (no order, ever): once a
    scanned name's running high reaches +50% over its open, the first bar
    whose low pierces 15% off that high is the shadow entry. Fail-open."""
    try:
        today = dt.date.today().isoformat()
        if SHADOW_DIP_LOGGED.get(symbol) == today:
            return
        tb = todays_bars(bars)
        if not len(tb):
            return
        day_open = float(tb["open"].iloc[0])
        if day_open <= 0:
            return
        run_hi = 0.0
        for _, b in tb.iterrows():
            run_hi = max(run_hi, float(b["high"]))
            if run_hi < day_open * SHADOW_DIP_ARM:
                continue
            level = run_hi * (1 - SHADOW_DIP_RETRACE)
            if float(b["low"]) <= level:
                SHADOW_DIP_LOGGED[symbol] = today
                log.info("%s: SHADOW dip-buy would trigger at %.2f (run high "
                         "%.2f, -%d%%) - journal only, NO order", symbol,
                         level, run_hi, int(SHADOW_DIP_RETRACE * 100))
                journal(symbol, "BUY", "shadow_dip_entry",
                        signal_price=round(level, 4),
                        note=f"[penny] SHADOW dip-buy: run_hi={run_hi:.2f} "
                             f"(+{100 * (run_hi / day_open - 1):.0f}% over open), "
                             f"no order placed")
                return
    except Exception:
        log.debug("shadow dip check failed for %s", symbol, exc_info=True)


PENNY_SEEN = "tripwire_seen_penny.json"


def tripwire_exit(state: dict, broker: Broker, risk: RiskManager,
                  symbol: str, reason: str) -> bool:
    """Information-based emergency exit: the held company just filed dilution
    paperwork (424B/8-K 3.02/5.03). Sell now, at whatever the price is - the
    filing, not a price level, is the trigger, so this respects the
    no-stop-loss directive. Returns True only when the position is CONFIRMED
    flat; a False return keeps the symbol in the pending-flatten queue to
    retry next slice (a failed exit must NEVER be silently dropped)."""
    log.critical("TRIPWIRE EXIT %s: %s - selling ALL now", symbol, reason)
    qty_pre = broker.position_qty(symbol)
    if qty_pre <= 0:
        state["positions"].pop(symbol, None)
        return True  # already flat / manual close
    all_done, fd = broker.cancel_symbol_orders(symbol, actions=("SELL",))
    if not all_done:
        log.error("%s: tripwire cancel unconfirmed - RETRYING next slice", symbol)
        return False
    qty = broker.settled_position_qty(symbol, expected_max=qty_pre - fd)
    if qty <= 0:
        state["positions"].pop(symbol, None)
        return True
    trade = broker.submit_market_order(symbol, "SELL", qty, risk, last_price=0.0)
    journal(symbol, "SELL", "penny_tripwire_exit", qty=qty,
            note=f"[penny] TRIPWIRE: {reason[:90]}")
    if broker.wait_order_done(trade) >= qty and broker.position_qty(symbol) <= 0:
        state["positions"].pop(symbol, None)
        return True
    log.error("%s: tripwire sell not fully confirmed - RETRYING next slice", symbol)
    return False


def process_tripwire(state: dict, broker: Broker, risk: RiskManager) -> None:
    """Retry every pending dilution-exit until flat, THEN poll the feed for
    new filings on held names. The pending queue persists in state, so a
    failed sell, exception, or crash-restart all re-attempt - decoupled from
    the feed's one-shot dedupe."""
    pending = state.setdefault("pending_flatten", {})
    # 1. retry outstanding exits first (these are life-or-death for the book)
    for sym in list(pending):
        if sym not in state["positions"] and broker.position_qty(sym) <= 0:
            from bot.tripwire import ack

            ack(pending[sym].get("id", ""), PENNY_SEEN)  # confirmed flat -> dedupe now
            del pending[sym]
            save_state(state)
            continue
        try:
            if tripwire_exit(state, broker, risk, sym, pending[sym]["reason"]):
                from bot.tripwire import ack

                ack(pending[sym].get("id", ""), PENNY_SEEN)
                del pending[sym]
                save_state(state)
        except Exception:
            log.exception("%s: tripwire retry errored - stays queued", sym)
    # 2. poll the feed for NEW filings on held names
    if not state["positions"]:
        return
    try:
        from bot.tripwire import check, cik_map

        for s in list(state["positions"]):
            if s not in CIKS.values():
                CIKS.update(cik_map([s]))
        hits = check({c: s for c, s in CIKS.items() if s in state["positions"]},
                     seen_name=PENNY_SEEN)
        for h in hits:
            if h["action"] == "flatten":
                notify(f"IBKR fleet: DILUTION TRIPWIRE {h['symbol']}",
                       f"{h['form']}: {h['detail'][:80]} - exiting now.")
                pending[h["symbol"]] = {"reason": f"{h['form']}: {h['detail'][:60]}",
                                        "id": h["id"]}
                save_state(state)
                if tripwire_exit(state, broker, risk, h["symbol"],
                                 pending[h["symbol"]]["reason"]):
                    ack(h["id"], PENNY_SEEN)
                    pending.pop(h["symbol"], None)
                    save_state(state)
            else:
                log.warning("TRIPWIRE alert %s: %s", h["symbol"], h["detail"])
                notify(f"IBKR fleet: filing alert {h['symbol']}", h["detail"][:180])
    except Exception:
        log.debug("tripwire check failed", exc_info=True)


# ---- ladder state (survives restarts and crashes) --------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            st.setdefault("positions", {})
            st.setdefault("entered", {})
            st.setdefault("pending", {})
            return st
        except Exception:
            # Preserve the evidence - a truncated file after a crash can be
            # reconstructed by hand; silently overwriting it cannot.
            log.warning("pennymo_state.json unreadable - preserved as .corrupt, starting fresh")
            try:
                STATE_FILE.replace(STATE_FILE.with_suffix(".json.corrupt"))
            except Exception:
                pass
    return {"positions": {}, "entered": {}, "pending": {}}


def save_state(state: dict) -> None:
    """Atomic write (tmp + os.replace): a crash mid-write can never leave a
    truncated state file. Called after EVERY order-related mutation, not just
    per scan pass - a hard kill between a fill and a lazy save would replay
    the order on restart (duplicate rung sell / duplicate $2k entry)."""
    state["entered"] = dict(sorted(state["entered"].items())[-5:])
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# ---- universe --------------------------------------------------------------

def watchlist(state: dict) -> list[str]:
    """Held names first (always managed), then fresh radar movers."""
    watch = []
    tox = None
    try:
        from bot.toxicity import block_reason, load as tox_load

        tox = tox_load()
    except Exception:
        pass
    for s in state["positions"]:
        if s in EXCLUDE:
            log.critical("%s: in the penny ladder AND another sleeve's basket - "
                         "two engines are managing one position; resolve manually", s)
        watch.append(s)
    try:
        import pandas as pd

        radar = pd.read_csv("penny_radar.csv")
        for _, r in radar[radar["status"] == "tracking"].tail(16).iloc[::-1].iterrows():
            s = str(r["symbol"])
            flags = str(r.get("sec_flags") or "")
            if flags and flags != "nan":
                DILUTION[s] = flags
            if s not in RADAR_STATS:  # rows iterate newest-first; keep newest
                try:
                    RADAR_STATS[s] = (float(r["price"]), float(r["dollar_vol_m"]))
                except (TypeError, ValueError, KeyError):
                    pass
            if tox is not None:
                try:
                    why = block_reason(s, tox)
                    if why:
                        continue  # exchange-flagged distressed / exact-ETF ban
                except Exception:
                    pass
            if s not in watch and s not in EXCLUDE \
                    and MIN_PRICE <= float(r["price"]) <= MAX_PRICE \
                    and float(r["dollar_vol_m"]) >= MIN_DVOL_M:
                watch.append(s)
    except Exception:
        log.debug("radar read failed", exc_info=True)
    return watch[:8]


def refresh_radar() -> None:
    """Mid-session mover rescan - feeds new names into penny_radar.csv."""
    try:
        from run_pennyradar import capture_today, load_radar, save_radar

        save_radar(capture_today(load_radar(), []))
    except Exception:
        log.debug("radar refresh failed", exc_info=True)


# ---- signals ---------------------------------------------------------------

def todays_bars(bars):
    d = bars.index[-1].date()
    return bars[[ts.date() == d for ts in bars.index]]


def entry_price(bars) -> float | None:
    """Return the last price if this name is 'moving' by the playbook's rules."""
    tb = todays_bars(bars)
    if len(tb) < 3:
        return None
    last = float(tb["close"].iloc[-1])
    day_open = float(tb["open"].iloc[0])
    day_high = float(tb["high"].max())
    if not (MIN_PRICE <= last <= MAX_PRICE) or day_open <= 0:
        return None
    if last / day_open - 1 < ENTRY_GAIN:      # not actually moving
        return None
    if last < NEAR_HIGH * day_high:            # already faded off the high
        return None
    return last


# ---- position management ---------------------------------------------------

def new_ladder(date: str, entry: float, qty: int) -> dict:
    return {"date": date, "entry": round(entry, 4), "initial": qty,
            "rungs": [False] * len(RUNGS), "hw": entry}


def adopt_pending(state: dict, broker: Broker) -> None:
    """Adopt entries whose BUY filled AFTER we stopped watching it.

    submit_entry_limit's cancel can go unconfirmed (or a fill can lag the
    position cache); without this, a late fill becomes a real position no
    scan ever manages. try_enter records the attempt in state['pending'];
    here it either becomes a ladder (filled) or expires (order dead)."""
    for symbol in list(state["pending"]):
        rec = state["pending"][symbol]
        pos = broker.position_qty(symbol)
        if pos > 0:
            # Cost basis unknown exactly - use the capped limit's worst case
            # (signal +1%) so the never-sell-below-entry floor stays honest.
            state["positions"][symbol] = new_ladder(rec["date"], rec["signal"] * 1.01, pos)
            log.info("%s: late BUY fill adopted into ladder (%d shares)", symbol, pos)
            journal(symbol, "BUY", "penny_late_adopt", qty=pos, filled=pos,
                    signal_price=rec["signal"], note="[penny] late fill adopted")
            del state["pending"][symbol]
            save_state(state)
        elif symbol not in broker.pending_order_symbols(actions=("BUY",)):
            log.info("%s: pending BUY confirmed dead, never filled", symbol)
            del state["pending"][symbol]
            save_state(state)


def manage(symbol: str, state: dict, broker: Broker, risk: RiskManager, last: float) -> None:
    """Run the DCA-out ladder for one held position."""
    positions = state["positions"]
    st = positions[symbol]
    qty = broker.position_qty(symbol)

    if qty < 0:
        # Repair a short, but never stack covers and never drop the ladder
        # while short: cover from a FRESH read after the cancel wait, and
        # keep state so the next scan retries whatever remains.
        if symbol in broker.pending_order_symbols(actions=("BUY",)):
            log.warning("%s: short, cover BUY already working - waiting", symbol)
            return
        broker.cancel_symbol_orders(symbol, actions=("SELL",))
        qty = broker.position_qty(symbol)   # SELLs can fill during the cancel
        if qty < 0:
            broker.buy_to_cover(symbol, -qty)
            qty = broker.position_qty(symbol)
        if qty < 0:
            log.error("%s: still short after cover attempt - retrying next scan", symbol)
            return

    if qty == 0:
        all_done, _ = broker.cancel_symbol_orders(symbol, actions=("SELL",))
        if not all_done:
            log.error("%s: flat but SELL cancel unconfirmed - keeping ladder "
                      "one more scan", symbol)
            return
        journal(symbol, "SELL", "penny_ladder_done", note="[penny] position closed - ladder cleared")
        log.info("%s: position gone (manual exit?) - ladder cleared", symbol)
        del positions[symbol]
        save_state(state)
        return

    st["hw"] = max(st.get("hw", last), last)
    entry, rungs = st["entry"], st["rungs"]

    # Rung sells - at most one per scan, using the hardened sequence: cancel
    # any working SELLs (e.g. an order placed manually in TWS) + CONFIRM ->
    # re-read position -> sell the tranche -> VERIFY shares actually sold
    # before marking the rung done. An unverified rung (rejection, halt)
    # would silently skip a +50%/+100% exit and later dump the whole
    # remainder as a "moon bag".
    qty_pre = qty  # manage()'s own fresh read, taken before any cancel below
    known_max = qty_pre  # arithmetic upper bound on remaining shares
    for i, mult in enumerate(RUNGS):
        if not rungs[i] and last >= entry * mult:
            all_done, filled_during = broker.cancel_symbol_orders(symbol, actions=("SELL",))
            if not all_done:
                log.error("%s: working SELLs unconfirmed - rung %d deferred", symbol, i + 1)
                return
            # Arithmetic bound, never the lagging cache (2026-08-04 incident).
            qty = broker.settled_position_qty(symbol, expected_max=qty_pre - filled_during)
            if qty <= 0:
                return  # something else sold it; next scan cleans up
            tranche = min(max(1, int(st["initial"] * RUNG_FRAC)), qty)
            log.info("%s: RUNG %d hit (last %.2f >= %.2f) - DCA out %d of %d",
                     symbol, i + 1, last, entry * mult, tranche, qty)
            trade = broker.submit_market_order(symbol, "SELL", tranche, risk, last)
            # Judge by the ORDER's fill report - the position cache lags and
            # judging by it caused the 2026-08-04 double-sell incident.
            sold = broker.wait_order_done(trade)
            if trade is None or sold <= 0:
                log.error("%s: rung %d sell NOT confirmed (sold %d) - retrying next scan",
                          symbol, i + 1, sold)
                return
            if sold < tranche:
                # Partial fill, order possibly still working: bank what sold,
                # mark the rung (the level WAS hit - the remainder just joins
                # the moon bag), and STOP this pass - continuing straight into
                # the trail/floor logic would cancel the still-working order
                # and re-enter the stale-cache race within the lag window.
                log.warning("%s: rung %d partially banked (%d/%d) - remainder "
                            "rides; deferring further action one scan",
                            symbol, i + 1, sold, tranche)
                rungs[i] = True
                save_state(state)
                journal(symbol, "SELL", "penny_rung", qty=tranche, filled=sold,
                        signal_price=last,
                        note=f"[penny] rung {i + 1} PARTIAL @ +{100 * (mult - 1):.0f}%")
                return
            rungs[i] = True
            known_max = qty - sold
            save_state(state)
            journal(symbol, "SELL", "penny_rung", qty=tranche, filled=sold, signal_price=last,
                    note=f"[penny] rung {i + 1} @ +{100 * (mult - 1):.0f}% from {entry:.2f}")
            notify(f"Penny SELL: {symbol} +{100 * (mult - 1):.0f}%",
                   f"Rung {i + 1} banked: sold {sold} @ ~${last:.2f} "
                   f"(entry ${entry:.2f}). Remainder rides.",
                   priority="default", tags="moneybag")
            break

    qty = broker.settled_position_qty(symbol, expected_max=known_max)
    if qty < 0:
        return  # rare double-fill residue; next scan's repair handles it
    if qty == 0:
        del positions[symbol]
        save_state(state)
        return

    # Breakeven floor (exit-lab winner): once the first rung has BANKED
    # profit, never let the remainder round-trip into a bag - exit at entry.
    # Books ~zero, not a loss, so the intraday no-stop rule is preserved.
    if BREAKEVEN_FLOOR and rungs[0] and last <= entry:
        all_done, fd = broker.cancel_symbol_orders(symbol, actions=("SELL",))
        if not all_done:
            return
        qty = broker.settled_position_qty(symbol, expected_max=qty - fd)
        if qty > 0:
            log.info("%s: BREAKEVEN FLOOR hit (last %.2f <= entry %.2f after "
                     "rung 1 banked) - closing remainder flat", symbol, last, entry)
            trade = broker.submit_market_order(symbol, "SELL", qty, risk, last)
            journal(symbol, "SELL", "penny_floor_exit", qty=qty, signal_price=last,
                    note=f"[penny] breakeven floor, entry={entry:.2f}")
            if broker.wait_order_done(trade) >= qty:  # order-confirmed, cache-proof
                del positions[symbol]
                notify(f"Penny SELL: {symbol} breakeven floor",
                       f"Closed remaining {qty} at ~${last:.2f} (entry "
                       f"${entry:.2f}) - rung-1 profit kept, no round-trip.",
                       priority="default", tags="shield")
        elif qty == 0:
            del positions[symbol]
        save_state(state)
        return

    # Moon bag trail exit: both rungs sold and price 15% off its high-water
    # mark. `last > entry` keeps intraday exits above entry - below entry,
    # only the breakeven floor (after rung 1) or the EOD flatten sells.
    if all(rungs) and last <= st["hw"] * (1 - TRAIL) and last > entry:
        all_done, fd = broker.cancel_symbol_orders(symbol, actions=("SELL",))
        if not all_done:
            return
        qty = broker.settled_position_qty(symbol, expected_max=qty - fd)
        if qty > 0:
            log.info("%s: MOON BAG trail hit (last %.2f, high-water %.2f) - closing",
                     symbol, last, st["hw"])
            trade = broker.submit_market_order(symbol, "SELL", qty, risk, last)
            journal(symbol, "SELL", "penny_moonbag_exit", qty=qty, signal_price=last,
                    note=f"[penny] trail exit, hw={st['hw']:.2f}")
            if broker.wait_order_done(trade) >= qty:  # order-confirmed, cache-proof
                del positions[symbol]
                notify(f"Penny SELL: {symbol} moon bag closed",
                       f"Trail hit: sold {qty} @ ~${last:.2f} "
                       f"({100 * (last / entry - 1):+.0f}% from entry "
                       f"${entry:.2f}, high-water ${st['hw']:.2f}).",
                       priority="default", tags="rocket")
        elif qty == 0:
            del positions[symbol]
        save_state(state)
        return

    # No stops (user directive) - so make the cost of that choice VISIBLE:
    # every scan, underwater positions report their drawdown and age.
    if last < entry:
        days = (dt.date.today() - dt.date.fromisoformat(st["date"])).days
        log.info("%s: UNDERWATER %.1f%% (entry %.2f, last %.2f, held %d day%s) "
                 "- holding per no-stop policy",
                 symbol, 100 * (last / entry - 1), entry, last, days,
                 "" if days == 1 else "s")


def flatten_all(state: dict, broker: Broker, risk: RiskManager) -> bool:
    """Close every ladder position before the bell (user directive 2026-08-05:
    'close all positions before the market closes'). This BOOKS LOSSES on
    underwater positions - at the bell, the flatten supersedes the no-stop
    rule. Returns True only when verified flat."""
    clean = True
    for symbol in list(state["positions"]):
        try:
            qty_pre = broker.position_qty(symbol)  # read BEFORE the cancel
            all_done, fd = broker.cancel_symbol_orders(symbol, actions=("SELL",))
            if not all_done:
                clean = False
                continue
            # Arithmetic bound over the lagging cache (2026-08-04 incident).
            qty = broker.settled_position_qty(symbol, expected_max=qty_pre - fd) \
                if qty_pre > 0 else broker.position_qty(symbol)
            if qty < 0 and symbol not in broker.pending_order_symbols(actions=("BUY",)):
                broker.buy_to_cover(symbol, -qty)
            elif qty > 0:
                entry = state["positions"][symbol].get("entry", 0)
                log.info("EOD FLATTEN %s: selling %d (entry %.2f) - booking the "
                         "day's result, win or lose", symbol, qty, entry)
                trade = broker.submit_market_order(symbol, "SELL", qty, risk, last_price=0.0)
                journal(symbol, "SELL", "penny_eod_flatten", qty=qty,
                        note="[penny] EOD flatten (user directive 2026-08-05)")
                # Order-confirmed success only - re-selling on the lagging
                # position cache is the double-sell that made us short once.
                if broker.wait_order_done(trade) >= qty:
                    state["positions"].pop(symbol, None)
                    save_state(state)
                    fp = getattr(broker, "last_fill_price", 0) or 0
                    notify(f"Penny SELL: {symbol} EOD flatten",
                           f"Closed {qty} before the bell"
                           + (f" @ ~${fp:.2f} ({100 * (fp / entry - 1):+.0f}% "
                              f"from entry ${entry:.2f})" if fp and entry else "")
                           + ". Day's result booked.",
                           priority="default", tags="checkered_flag")
                else:
                    clean = False
                continue
            if broker.position_qty(symbol) != 0:
                clean = False
            else:
                state["positions"].pop(symbol, None)
                save_state(state)
        except Exception:
            log.exception("EOD flatten failed for %s - will retry", symbol)
            clean = False
    return clean


def try_enter(symbol: str, state: dict, broker: Broker, risk: RiskManager,
              last: float, rvol: float | None = None,
              spread: float | None = None,
              nbbo_spread: float | None = None) -> bool:
    today = dt.date.today().isoformat()
    entered_today = state["entered"].setdefault(today, [])
    if symbol in EXCLUDE or symbol in entered_today or symbol in state["pending"]:
        return False  # one attempt per symbol per day - never re-chase a fade
    try:
        # Halted names: exits can't fill until resume, and news-halts resume
        # violently. The free NASDAQ feed makes this checkable (Gap 7).
        from bot.halts import halt_block_reason

        hb = halt_block_reason(symbol)
        if hb:
            log.warning("%s: SKIPPING entry - %s", symbol, hb)
            journal(symbol, "BUY", "blocked_entry", signal_price=last,
                    note=f"[penny] halt: {hb[:70]}")
            return False
    except Exception:
        pass
    if len(entered_today) >= MAX_NEW_PER_DAY or len(state["positions"]) >= MAX_OPEN:
        return False
    try:
        from bot.edgar import is_china_based

        if is_china_based(symbol):  # cached after the radar's own check
            log.warning("%s: SKIPPING entry - China/HK-based (user directive)", symbol)
            return False
    except Exception:
        pass
    if SKIP_DILUTION_FLAGS and DILUTION.get(symbol):
        log.warning("%s: SKIPPING entry - active dilution filings on record: %s",
                    symbol, DILUTION[symbol])
        journal(symbol, "BUY", "blocked_entry", signal_price=last,
                note=f"[penny] dilution filings: {DILUTION[symbol][:80]}")
        return False
    if broker.position_qty(symbol) != 0:
        # Held outside the ladder (manual buy, or state lost) - buying more
        # would build a ladder over shares with an unknown cost basis.
        log.info("%s: account already holds this outside the ladder - not entering", symbol)
        return False
    if symbol in broker.pending_order_symbols(actions=("BUY",)):
        return False

    try:
        budget = ENTRY_FRACTION * broker.net_liquidation()
    except Exception:
        budget = FALLBACK_BUDGET  # equity read failed - fixed clip, never skip
    qty = int(budget // last)
    if qty < 1 or qty * last < 300:
        return False

    log.info("%s: MOVER at %.2f - entering ~$%.0f (%d shares, %d%% of equity)",
             symbol, last, qty * last, qty, int(ENTRY_FRACTION * 100))
    entered_today.append(symbol)  # counts even if unfilled - no repeat chasing
    save_state(state)             # persisted BEFORE the order: crash != re-chase
    filled = broker.submit_entry_limit(symbol, qty, risk, last,
                                       cap_pct=0.01, wait_s=8, cancel_unfilled=True,
                                       risk_cap_pct=ENTRY_FRACTION)
    pos = broker.position_qty(symbol)
    got = max(pos, filled)        # order fills can lead the position cache
    if got <= 0:
        if symbol in broker.pending_order_symbols(actions=("BUY",)):
            # Cancel unconfirmed - the BUY may still fill later. Remember it,
            # or a late fill becomes a position no scan ever manages.
            state["pending"][symbol] = {"date": today, "signal": last}
            log.warning("%s: BUY still working after cancel - will adopt if it fills", symbol)
        else:
            log.info("%s: entry did not fill - standing down on this name today", symbol)
        save_state(state)
        return False
    # True cost basis when IBKR reports it; else the capped limit's worst case.
    entry_px = broker.last_fill_price if broker.last_fill_price > 0 else last * 1.01
    state["positions"][symbol] = new_ladder(today, entry_px, got)
    save_state(state)
    try:
        from bot.scanner import spread_tier

        # Real NBBO outranks the OHLC estimate when present: spreadcheck
        # graded EDGE at 58% tier agreement on pennies, errors both ways.
        tier = spread_tier(nbbo_spread if nbbo_spread is not None else spread)
    except Exception:
        tier = "unknown"
    try:
        from bot.scanner import cached_enrich

        growth = cached_enrich(symbol).get("rev_growth", "?")
    except Exception:
        growth = "?"
    journal(symbol, "BUY", "penny_entry", qty=qty, filled=got, signal_price=last,
            note=f"[penny] jeet ladder +8/+15, trail {int(TRAIL * 100)}%, floor on, "
                 f"rvol={rvol if rvol is not None else '?'}, "
                 f"spread={spread if spread is not None else '?'}%, "
                 f"nbbo={nbbo_spread if nbbo_spread is not None else '?'}% tier={tier}, "
                 f"growth={growth}")
    notify(f"Penny BUY: {symbol}",
           f"Bought {got} @ ~${entry_px:.2f} (~${got * entry_px:,.0f}). "
           f"Ladder +8%/+15%, 10% trail, breakeven floor on.",
           priority="default", tags="chart_with_upwards_trend")
    return True  # rvol + spread are LOGGED evidence, not gates (yet)


# ---- main loop -------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Penny momentum sleeve (DCA-out ladder)")
    parser.add_argument("--once", action="store_true", help="Single pass, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("pennymo.log", encoding="utf-8")],
    )
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    cfg = load_config()  # enforces the paper guard
    from bot.config import BLOCKED_SYMBOLS

    EXCLUDE.update(cfg.strategy.symbols)
    EXCLUDE.update(cfg.intraday.symbols)
    EXCLUDE.update(BLOCKED_SYMBOLS)  # no ETFs, no gambling stocks (directives)
    cfg.ib.client_id = CLIENT_ID
    broker = Broker(cfg, sleeve="penny")

    while True:
        try:
            broker.connect()
            break
        except SystemExit:
            raise
        except Exception as e:
            if args.once or market_phase() == "closed":
                raise
            log.warning("Can't reach TWS (%s) - retrying in 2 minutes", e)
            time.sleep(120)

    state: dict | None = None
    try:
        equity = broker.net_liquidation()
        prev = broker.previous_day_equity()
        risk = RiskManager(cfg.risk, day_start_equity=prev if prev and prev > 0 else equity)
        state = load_state()

        # Adopt reality against the ACTUAL account, not just the state file:
        #  - vanished ladders: cancel their leftover SELLs (an orphaned legacy
        #    stop would eventually fire into a naked short), then drop them
        #  - ANY symbol where clientId 49 has a resting SELL: cancel it. This
        #    sleeve never leaves a SELL resting, so those can only be legacy
        #    protective stops - including on positions a lost state file no
        #    longer remembers.
        for symbol in list(state["positions"]):
            try:
                if broker.position_qty(symbol) == 0:
                    log.info("%s: no longer held - clearing its ladder", symbol)
                    broker.cancel_symbol_orders(symbol, actions=("SELL",))
                    del state["positions"][symbol]
                else:
                    state["positions"][symbol].pop("stop", None)
            except Exception:
                log.exception("Startup adoption failed for %s", symbol)
        try:
            my_id = broker.ib.client.clientId
            own_sells = {
                t.contract.symbol for t in broker.ib.reqAllOpenOrders()
                if t.order.action == "SELL" and t.order.clientId == my_id
                and t.orderStatus.status in broker.ACTIVE_STATUSES
            }
            for symbol in own_sells:
                log.info("%s: cancelling legacy resting SELL (no-stop policy)", symbol)
                all_done, _ = broker.cancel_symbol_orders(symbol, actions=("SELL",))
                if not all_done:
                    log.error("%s: legacy SELL cancel unconfirmed - check TWS", symbol)
        except Exception:
            log.exception("Legacy stop sweep failed")
        save_state(state)
        log.info("Penny sleeve up. Equity $%s | managing %s | %d%% of equity"
                 "/entry (~$%.0f), rungs +%d/+%d, trail %d%%, breakeven floor "
                 "%s | EOD FLATTEN 15:55 | no intraday stops (user directive)",
                 f"{equity:,.0f}", list(state["positions"]) or "nothing",
                 int(ENTRY_FRACTION * 100), ENTRY_FRACTION * equity,
                 round(100 * (RUNGS[0] - 1)), round(100 * (RUNGS[1] - 1)),
                 int(TRAIL * 100), "ON" if BREAKEVEN_FLOOR else "off")

        scans = 0
        flattened = False
        while True:
            phase = market_phase()
            try:  # heartbeat for the watchdog: proof this loop is alive
                Path("heartbeat_penny.txt").write_text(
                    dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            except Exception:
                pass
            if phase == "premarket":
                broker.ib.sleep(20)  # 9:28 start, 9:30 bell - wait, never exit
                continue
            if phase == "closed":
                if not args.once:
                    log.info("Market closed. All positions were flattened before "
                             "the bell (user directive 2026-08-05). Exiting.")
                break
            if not broker.ib.isConnected():
                log.warning("Lost TWS connection - attempting reconnect")
                try:
                    broker.connect()
                except SystemExit:
                    raise
                except Exception:
                    broker.ib.sleep(SCAN_MINUTES * 60)
                    continue

            if phase == "flatten":
                if not flattened:
                    flattened = flatten_all(state, broker, risk)
                    if not flattened:
                        log.warning("EOD flatten incomplete - retrying in 30s")
                        broker.ib.sleep(30)
                        continue
                    log.info("EOD flatten complete. Waiting for the close.")
                broker.ib.sleep(60)
                continue

            if scans % REFRESH_EVERY == 0:
                refresh_radar()
            adopt_pending(state, broker)

            entry_ok = phase == "open" and dt.datetime.now().time() < ENTRY_CUTOFF
            for symbol in watchlist(state):
                try:
                    bars = fetch_intraday_bars(broker.ib, symbol, 90, "5 mins")
                    archive_intraday_bars(symbol, bars)
                    if not len(bars):
                        continue
                    shadow_dip_check(symbol, bars)  # journal-only, never trades
                    if symbol in state["positions"]:
                        manage(symbol, state, broker, risk,
                               float(bars["close"].iloc[-1]))
                    elif entry_ok:
                        last = entry_price(bars)
                        if last is not None:
                            # PREMARKET-HIGH BREAK FILTER (evidence-based edit,
                            # 2026-08-08, from 1,506 real gapper replays):
                            # movers that broke their PM high were ~breakeven
                            # with an 83% win rate; those below it were -11.6%
                            # with 49%. Waiting for the break skips the losing
                            # cohort - and does NOT consume the one-attempt-
                            # per-day (the break may come later). Fail-open
                            # when the PM high is unavailable.
                            pmh, pmh_t = None, None
                            try:
                                pmh, pmh_t = pm_high_today(broker, symbol)
                            except Exception:
                                pass
                            if pmh is not None and last <= pmh:
                                log.info("%s: mover but BELOW premarket high "
                                         "(%.2f <= %.2f) - waiting for the "
                                         "break", symbol, last, pmh)
                                continue
                            # EARLY-HIGH filter (2nd evidence-based edit): a
                            # PM high set at/after 08:00 = the spike is
                            # already extended into the open; its break nets
                            # -1.35% vs +2.50% for early highs. Skip the day.
                            if pmh is not None and pmh_t is not None \
                                    and pmh_t >= dt.time(8, 0):
                                if symbol not in LATE_PMH_LOGGED:
                                    LATE_PMH_LOGGED.add(symbol)
                                    log.info("%s: PM high set late (%s) - "
                                             "skipping today (early-high "
                                             "filter)", symbol, pmh_t)
                                    journal(symbol, "BUY", "blocked_entry",
                                            signal_price=last,
                                            note=f"[penny] late PM high {pmh_t}")
                                continue
                            # FIRST-15-MIN GATE (user directive 2026-08-14):
                            # inside 9:30-9:45 an entry additionally needs
                            # SOLID BUY-SIDE DEMAND - NBBO bid size >= 1.5x
                            # ask size. Fail-closed: unreadable book = wait.
                            # Does NOT consume the one-attempt-per-day.
                            if dt.datetime.now().time() < ENTRY_START:
                                demand_ok = False
                                try:
                                    from bot import polygon

                                    bs, ask_sz, bage = \
                                        polygon.last_nbbo_sizes(symbol)
                                    demand_ok = (bs is not None and ask_sz
                                                 and bage is not None
                                                 and bage <= 30
                                                 and bs >= DEMAND_RATIO * ask_sz)
                                except Exception:
                                    demand_ok = False
                                if not demand_ok:
                                    log.info("%s: mover in the opening 15 min "
                                             "without demand confirmation - "
                                             "waiting for 9:45", symbol)
                                    continue
                                log.info("%s: opening-window entry ALLOWED - "
                                         "early PMH broken and bid-side demand "
                                         ">= %.1fx ask", symbol, DEMAND_RATIO)
                            rv, sp = None, None
                            try:
                                # RVOL (logged, not gated) + EDGE spread
                                # (advisory tiers) + split-mismatch
                                # quarantine (adjusted-Yahoo vs raw-IBKR
                                # joins corrupt features after splits).
                                from zoneinfo import ZoneInfo

                                from bot.scanner import (cached_symbol_curve,
                                                         edge_spread_pct,
                                                         rvol_estimate,
                                                         split_mismatch)

                                pr, dv = RADAR_STATS.get(symbol, (None, None))
                                tb = todays_bars(bars)
                                now_et = dt.datetime.now(ZoneInfo("America/New_York"))
                                # Sanity gate: the radar's capture price and
                                # IBKR's live price are BOTH raw actual prices;
                                # a >3x disagreement means a reverse-split scale
                                # error in the Yahoo-derived denominator, not a
                                # day's move. (True split-vs-raw AUDIT runs in
                                # the nightly lab on clean daily bars - doing it
                                # here on the capture price false-tripped on
                                # every +12% mover, review finding.)
                                scale_ok = bool(pr and last and 0.33 < last / pr < 3.0)
                                if pr and dv and scale_ok and len(tb) \
                                        and tb.index[-1].date() == now_et.date():
                                    rv = rvol_estimate(
                                        # IBKR reports US-stock bar volume in
                                        # LOTS of 100; Yahoo's avg is shares.
                                        float(tb["volume"].sum()) * 100,
                                        dv * 1e6 / pr, now_et,
                                        # per-symbol curve when the radar has
                                        # warmed one (cache read - no network
                                        # in the trading loop, ever)
                                        curve=cached_symbol_curve(symbol))
                                sp = edge_spread_pct(bars)
                            except Exception:
                                pass
                            nb = None
                            try:
                                # Real NBBO spread (Polygon, real-time). The
                                # spreadcheck run graded EDGE at 58% tier
                                # agreement on pennies - when the real quote
                                # is available it outranks the estimate.
                                # Staleness gate (review): a quote >30s old
                                # (halt, thin tape) must not be journaled as
                                # current truth - drop it, EDGE stands.
                                from bot import polygon

                                if polygon.available():
                                    nb, nb_age = polygon.last_nbbo_spread(symbol)
                                    if nb_age is None or nb_age > 30:
                                        nb = None
                            except Exception:
                                pass
                            try_enter(symbol, state, broker, risk, last,
                                      rvol=rv, spread=sp, nbbo_spread=nb)
                except Exception:
                    log.exception("Scan error on %s - continuing", symbol)
            save_state(state)

            if args.once:
                log.info("--once: single pass complete")
                break
            scans += 1
            # Sleep in ~60s slices, running the EDGAR tripwire on HELD names
            # between scans. A dilution filing mid-hold is the one event worth
            # waking for: detection target is <=60s, not a full scan cycle.
            # process_tripwire also retries any pending exit until flat.
            for _ in range(SCAN_MINUTES):
                broker.ib.sleep(60)
                if state["positions"] or state.get("pending_flatten"):
                    process_tripwire(state, broker, risk)
    finally:
        if state is not None:
            save_state(state)
        broker.disconnect()


if __name__ == "__main__":
    main()
