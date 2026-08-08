"""Event study: the penny DCA-out playbook replayed over years of history.

Finds every historical day a low-priced stock spiked the way the penny
sleeve's entry rule demands (+12% above the open, price $0.20-$7), then
replays the EXACT live ladder on the daily bars that followed:

  enter at the crossing (+1% cap)  ->  sell 1/3 at +50%  ->  1/3 at +100%
  ->  moon bag trails 30% off its high-water mark (only ever sold ABOVE
  entry - the no-stop directive)  ->  anything still held at the horizon
  is marked at the last close.

The universe includes every symbol ever captured in screens_archive/ -
the point-in-time record. Today that's still survivorship-biased, but
every archived day adds names BEFORE their fate is known, so the bias
heals as the archive grows: future corpses will be in the data.

READ THE CAVEATS - they bias the results OPTIMISTIC:
  - SURVIVORSHIP (shrinking but real): names already dead can't appear.
  - Daily bars can't order intraday moves: same-day rung fills are allowed
    when the high reached them (flattering), the entry's within-15%-of-high
    condition can't be checked, and trail exits use the day's extremes.
  - Fills are frictionless beyond the entry cap and the cost haircut.
This study answers "does the LADDER SHAPE make sense on real spike paths?"
It cannot answer "will the live bot make money" - only live paper can.

Run:  python run_eventstudy.py            # full study
      python run_eventstudy.py --horizon 60

Importable: study(...) runs one parameterized pass (used by run_lab.py's
nightly Research Lab, including its pre-registered parameter sweep).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from bot.data import fetch_yahoo_daily

ENTRY_GAIN = 0.12
CAP = 0.01
MIN_PRICE, MAX_PRICE = 0.20, 7.0
RUNGS = (1.5, 2.0)
TRAIL = 0.30
COST_PCT = 0.020          # friction haircut per event (penny spreads are 1-3%)
MAX_SYMBOLS = 80
YEARS = 5


def universe() -> list[str]:
    syms: list[str] = []
    # Point-in-time screens archive first: names captured while hot,
    # BEFORE their fate was known - the survivorship-healing set.
    for p in sorted(Path("screens_archive").glob("screen_*.csv")):
        try:
            syms += pd.read_csv(p)["symbol"].astype(str).tolist()
        except Exception:
            pass
    for f in ("screen_results.csv", "penny_radar.csv"):
        p = Path(f)
        if p.exists():
            try:
                syms += pd.read_csv(p)["symbol"].astype(str).tolist()
            except Exception:
                pass
    # Mirror the LIVE sleeve's exclusions exactly: the penny ladder may never
    # trade the other sleeves' basket symbols, so a study that includes them
    # measures trades the bot cannot take. (Adversarial review caught this:
    # the basket names supplied 61% of events and ALL the profit - with them
    # removed, the historical ladder result flipped negative.)
    exclude = {"SPY", "QQQ"}
    try:
        from bot.config import load_config

        cfg = load_config()
        exclude |= set(cfg.strategy.symbols) | set(cfg.intraday.symbols)
    except Exception:
        pass
    out: list[str] = []
    for s in syms:
        s = s.upper()
        if s not in out and s not in exclude:
            out.append(s)
    return out[:MAX_SYMBOLS]


def replay_ladder(t: int, entry: float, o, h, l, c, n: int, horizon: int,
                  rungs=RUNGS, trail=TRAIL) -> dict:
    """Replay the live ladder from event day t. Proceeds are in multiples of
    the entry-priced position (1.0 = got exactly the entry value back)."""
    frac = 1.0 / (len(rungs) + 1)     # equal tranches + a moon bag
    remaining = 1.0
    proceeds = 0.0
    rungs_done = [False] * len(rungs)
    hw = entry
    end = min(n - 1, t + horizon)
    exit_day = end
    for j in range(t, end + 1):
        hi, lo, op = h[j], l[j], o[j]
        hw = max(hw, hi)
        for k, mult in enumerate(rungs):
            level = entry * mult
            if not rungs_done[k] and remaining > 1e-9 and hi >= level:
                # Gap-through honesty: if a later day OPENS above the rung,
                # the market sell fills at that open, not the rung price.
                fill = max(level, op) if j > t else level
                proceeds += frac * (fill / entry)
                remaining -= frac
                rungs_done[k] = True
        if all(rungs_done) and remaining > 1e-9 and j > t:
            trig = hw * (1 - trail)
            if trig > entry and lo <= trig:
                fill = min(trig, op) if op < trig else trig
                if fill > entry:  # live rule: the moon bag NEVER sells below entry
                    proceeds += remaining * (fill / entry)
                    remaining = 0.0
        if remaining <= 1e-9:
            exit_day = j
            break
    mark = remaining * (c[end] / entry) if remaining > 1e-9 else 0.0
    gross = proceeds + mark - 1.0
    bag = remaining > 1e-9 and (c[end] / entry) < 1.0
    return {
        "ret_gross": gross,
        "ret_net": max(gross - COST_PCT, -1.0),  # a long clip can't lose >100%
        "days": exit_day - t if remaining <= 1e-9 else end - t,
        "hit_r1": rungs_done[0], "hit_r2": rungs_done[-1],
        "closed": remaining <= 1e-9,
        "bag_held": bag,
        # The remaining tranche's OWN mark (not the blended event return) -
        # the honest measure of what the bag itself is worth.
        "bag_tranche_pct": round(100 * (c[end] / entry - 1), 1) if bag else None,
        "_end": exit_day if remaining <= 1e-9 else end,
    }


MIN_DVOL = 3_000_000  # the live sleeve's $3M/day liquidity floor


def simulate_symbol(bars: pd.DataFrame, horizon: int,
                    entry_gain=ENTRY_GAIN, rungs=RUNGS, trail=TRAIL) -> list[dict]:
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    # Liquidity gate as of the PRIOR day, like the live watchlist rule.
    dv = (bars["close"] * bars["volume"]).rolling(20).mean().shift(1).to_numpy(float)
    n = len(bars)
    events = []
    i = 0
    while i < n - 1:
        trigger = o[i] * (1 + entry_gain)
        entry = trigger * (1 + CAP)
        if not (MIN_PRICE <= o[i] <= MAX_PRICE) or h[i] < entry or o[i] <= 0 \
                or not (dv[i] == dv[i] and dv[i] >= MIN_DVOL):
            i += 1
            continue
        res = replay_ladder(i, entry, o, h, l, c, n, horizon, rungs, trail)
        events.append({"date": bars.index[i].date().isoformat(),
                       "entry": round(entry, 4), **res})
        i = res.pop("_end") + 1  # one ladder per symbol at a time, like live
    return events


def study(horizon: int = 120, entry_gain: float = ENTRY_GAIN, rungs=RUNGS,
          trail: float = TRAIL, syms: list[str] | None = None,
          verbose: bool = True):
    """One full parameterized pass. Returns (events_df|None, summary dict)."""
    syms = syms if syms is not None else universe()
    all_events = []
    for s in syms:
        try:
            bars = fetch_yahoo_daily(s, years=YEARS)
            bars = bars[bars.index < pd.Timestamp.today().normalize()]
            evs = simulate_symbol(bars, horizon, entry_gain, rungs, trail)
            for e in evs:
                e["symbol"] = s
            all_events += evs
            if evs and verbose:
                print(f"  {s}: {len(evs)} spike events")
        except Exception as e:
            if verbose:
                print(f"  {s}: skipped ({e})")
    if not all_events:
        return None, {"events": 0}
    df = pd.DataFrame(all_events)
    ne = df["ret_net"]
    bags = df[df["bag_held"]]
    summary = {
        "events": len(df), "symbols": int(df["symbol"].nunique()),
        "net_avg_pct": round(100 * ne.mean(), 2),
        "net_median_pct": round(100 * ne.median(), 2),
        "win_pct": round(100 * (ne > 0).mean()),
        "r1_pct": round(100 * df["hit_r1"].mean()),
        "r2_pct": round(100 * df["hit_r2"].mean()),
        "bag_pct": round(100 * df["bag_held"].mean()),
        "bag_tranche_avg_pct": round(float(pd.to_numeric(
            bags["bag_tranche_pct"], errors="coerce").mean()), 1) if len(bags) else None,
        "avg_days": round(df["days"].mean()),
        "params": {"horizon": horizon, "entry_gain": entry_gain,
                   "rungs": list(rungs), "trail": trail},
    }
    return df, summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Penny ladder historical event study")
    ap.add_argument("--horizon", type=int, default=120, help="Max holding days")
    args = ap.parse_args()

    syms = universe()
    print(f"Universe: {len(syms)} symbols (survivorship-biased today; the "
          f"screens archive heals it a little every day)\n")
    df, s = study(horizon=args.horizon, syms=syms)
    if df is None:
        print("No events found.")
        return
    df.to_csv("eventstudy_results.csv", index=False)

    print(f"\n{'=' * 62}")
    print(f"PENNY LADDER EVENT STUDY - {s['events']} historical spike entries, "
          f"{s['symbols']} symbols, horizon {args.horizon}d")
    print(f"{'=' * 62}")
    print(f"  Per $2,000 clip, NET of {100 * COST_PCT:.1f}% friction:")
    print(f"    average:  {s['net_avg_pct']:+7.2f}%   (${20 * s['net_avg_pct']:+,.0f})")
    print(f"    median:   {s['net_median_pct']:+7.2f}%    winners: {s['win_pct']}%")
    print(f"  Ladder mechanics: +50% rung {s['r1_pct']}% | +100% rung {s['r2_pct']}% "
          f"| bag-held {s['bag_pct']}% (bag tranche avg {s['bag_tranche_avg_pct']}%) "
          f"| avg {s['avg_days']}d")
    df["year"] = df["date"].str[:4]
    for y, grp in df.groupby("year"):
        print(f"    {y}: {len(grp):>4} events, net avg {100 * grp['ret_net'].mean():+7.2f}%, "
              f"winners {100 * (grp['ret_net'] > 0).mean():.0f}%")
    print(f"\nCAVEATS (they flatter the results): survivorship universe; REVERSE-")
    print(f"SPLIT adjustment hides pre-split penny episodes of serial splitters")
    print(f"(their worst repeated collapses are invisible); same-day rung fills;")
    print(f"ideal entries vs live's delayed capped limits; frictionless beyond")
    print(f"the {100 * COST_PCT:.1f}% haircut. Per-event rows: eventstudy_results.csv")


if __name__ == "__main__":
    main()
