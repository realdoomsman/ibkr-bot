"""ORBlab: opening-range breakouts on stocks-in-play - the audition.

The last documented intraday edge family untested for the $5-1000 band
(Zarattini/Aziz-style: gap-up names with heavy early interest, buy the
break of the opening range, done by the close). Everything else tried on
this band tested negative; this is the setup the literature actually
endorses for it - now measured on OUR survivorship-free machine before a
single dollar follows it.

PRE-REGISTERED DESIGN (declared before results):
  Universe/day  grouped daily D and D-1 (raw, delisted included):
                $5-1000, >= $20M traded on D, GAP UP >= 2% at the open
                (open_D vs close_{D-1}) - "in play" proxy; top 12 by
                dollar volume per session.
  Ranges        first 15 min and first 30 min of 5-min RTH bars.
  Entry         first later bar whose high breaks the range high; fill at
                the range-high price; entries before 15:00 only.
  Exits (cells) EOD    - hold to the close (the fleet-legal shape)
                LADDER - thirds at +4% / +8% from entry, rest at close
                STOP1R - stop at range low, else close (INFORMATIONAL
                         ONLY: the user's no-stop directive stands; this
                         cell exists to measure what stops would be worth)
  Friction      0.4% round trip. One trade per symbol-day per range.
  Bar           n>=250 AND net>=+1.5%/trade (horizon-adjusted judgment at
                the gate; for one-day holds on liquid names, +0.5%+ with
                high n is already interesting).

Run:  python run_orblab.py [--sessions 40]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from bot import polygon

log = logging.getLogger("bot.orblab")

LO, HI = 5.0, 1000.0
MIN_DVOL = 20e6
MIN_GAP = 0.02
TOP_N = 12
FRICTION = 0.004
LADDER = (1.04, 1.08)
CACHE = Path("orblab_events.csv")


def replay_orb(bars: pd.DataFrame, range_bars: int) -> dict | None:
    """One symbol-day x one range length. None = no breakout entry."""
    rth = bars[[dt.time(9, 30) <= t.time() < dt.time(16, 0)
                for t in bars["date"]]].reset_index(drop=True)
    if len(rth) < range_bars + 3:
        return None
    rng_hi = float(rth.iloc[:range_bars]["high"].max())
    rng_lo = float(rth.iloc[:range_bars]["low"].min())
    if rng_hi <= 0 or rng_lo <= 0:
        return None
    entry_i = None
    for i in range(range_bars, len(rth)):
        if rth.iloc[i]["date"].time() >= dt.time(15, 0):
            break
        if float(rth.iloc[i]["high"]) > rng_hi:
            entry_i = i
            break
    if entry_i is None:
        return None
    entry = rng_hi
    close = float(rth.iloc[-1]["close"])
    after = rth.iloc[entry_i:]
    worst = float(after["low"].min())
    # EOD exit
    eod = close / entry - 1 - FRICTION
    # Ladder exit: thirds at +4%/+8% when touched, remainder at close
    r1, r2 = entry * LADDER[0], entry * LADDER[1]
    hit1 = bool((after["high"] >= r1).any())
    hit2 = bool((after["high"] >= r2).any())
    ladder = ((r1 if hit1 else close) + (r2 if hit2 else close) + close) \
        / (3 * entry) - 1 - FRICTION
    # Stop-at-range-low (informational): stopped if low touches rng_lo after
    # entry (approximation: any touch = stopped at rng_lo), else EOD close.
    stopped = bool((after["low"] <= rng_lo).any())
    stop1r = (rng_lo / entry - 1 - FRICTION) if stopped else eod
    return {"range_bars": range_bars,
            "net_eod": round(100 * eod, 2),
            "net_ladder": round(100 * ladder, 2),
            "net_stop1r": round(100 * stop1r, 2),
            "mae_pct": round(100 * (worst / entry - 1), 2),
            "entry_time": rth.iloc[entry_i]["date"].strftime("%H:%M")}


def scan_session(day: str, prev_day: str) -> list[dict]:
    g = polygon.grouped_daily(day, adjusted=False)
    gp = polygon.grouped_daily(prev_day, adjusted=False)
    if g is None or gp is None:
        return []
    prev_close = dict(zip(gp["symbol"], gp["close"]))
    g = g[[bool(s.isalpha() and len(s) <= 5 and not (len(s) == 5 and s[-1] in "WUR"))
           for s in g["symbol"].astype(str)]]
    g = g[(g["open"] >= LO) & (g["open"] <= HI)
          & (g["volume"] * g["vwap"] >= MIN_DVOL)]
    g = g.assign(pc=[prev_close.get(s) for s in g["symbol"]])
    g = g[g["pc"].notna() & (g["pc"] > 0)]
    g = g[g["open"] >= g["pc"] * (1 + MIN_GAP)]           # gap UP >= 2%
    g = g.assign(dvol=g["volume"] * g["vwap"]) \
         .sort_values("dvol", ascending=False).head(TOP_N)
    out = []
    for _, r in g.iterrows():
        bars = polygon.minute_bars(r["symbol"], day, minutes=5, adjusted=False)
        if bars is None:
            continue
        for rb in (3, 6):  # 15-min and 30-min opening ranges
            got = replay_orb(bars, rb)
            if got:
                out.append({"day": day, "symbol": r["symbol"],
                            "gap_pct": round(100 * (r["open"] / r["pc"] - 1), 1),
                            **got})
    return out


def study(sessions: int = 40) -> list[str]:
    prev = pd.read_csv(CACHE, dtype={"symbol": str}) if CACHE.exists() \
        else pd.DataFrame()
    have = set(prev["day"]) if len(prev) else set()
    days: list[str] = []
    d = dt.date.today() - dt.timedelta(days=1)
    while len(days) < sessions + 1:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= dt.timedelta(days=1)
    for i in range(len(days) - 1):
        day, prev_day = days[i], days[i + 1]
        if day in have:
            continue
        evs = scan_session(day, prev_day)
        if evs:
            prev = pd.concat([prev, pd.DataFrame(evs)], ignore_index=True)
            tmp = CACHE.with_suffix(".csv.tmp")
            prev.to_csv(tmp, index=False)
            import os
            os.replace(tmp, CACHE)
            log.info("orblab %s: %d breakout events", day, len(evs))
    if not len(prev):
        return ["ORBLAB: no events"]
    lines = [f"ORBLAB - opening-range breakouts on gap-up stocks-in-play "
             f"({prev['day'].nunique()} sessions, {len(prev)} events, "
             f"survivorship-free, raw prices)"]
    for rb, label in ((3, "15-min range"), (6, "30-min range")):
        c = prev[prev["range_bars"] == rb]
        if not len(c):
            continue
        for col, name in (("net_eod", "hold-to-close"),
                          ("net_ladder", "ladder +4/+8"),
                          ("net_stop1r", "stop@range-low*")):
            net = pd.to_numeric(c[col], errors="coerce").dropna()
            lines.append(f"  {label} {name:<16} n={len(net):<5} "
                         f"net {net.mean():+6.2f}%  med {net.median():+6.2f}%  "
                         f"win {100 * (net > 0).mean():.0f}%")
    lines.append("  (*stop cell is informational - the no-stop directive "
                 "stands; it prices what stops would be worth)")
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=40)
    args = ap.parse_args()
    if not polygon.available():
        raise SystemExit("POLYGON_API_KEY not set")
    print("\n".join(study(args.sessions)))


if __name__ == "__main__":
    main()
