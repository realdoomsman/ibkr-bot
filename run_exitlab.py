"""Exit-strategy tournament: profit-taking schemes fight on identical history.

Every scheme replays the SAME historical spike entries (the penny sleeve's
+12% trigger) - only the exits differ. Schemes marked [books losses] violate
the live no-stop-loss directive and are shown for honest comparison only.

  A  current_ladder    thirds at +50%/+100%, 30% trail after both,
                       never sells below entry (the LIVE config - baseline)
  B  quicker_rungs     thirds at +25%/+75% - banks the fade earlier
  C  breakeven_floor   ladder A + once the +50% rung fills, the rest exits
                       if price ever falls back to entry. Books ~zero instead
                       of riding to -60%. (Gaps can fill slightly below.)
  D  time_stop_10d     ladder A + everything liquidated at day 10's close
                       [books losses]
  E  day2_close        no rungs - sell everything at day 2's close (the
                       research view: the spike day IS the top) [books losses]
  F  pure_trail_25     no rungs - one 25% trailing exit from day 1, letting
                       winners run uncapped [books losses]
  G  early_trail       ladder A but the 30% trail is active from day 1, not
                       only after both rungs [books losses]

Self-validation: scheme A must reproduce run_eventstudy's numbers on the
same universe - if it doesn't, this file has a bug and nothing here counts.

Caveats are the event study's, plus one more: comparing 7 schemes on a
small event set and crowning the best IS multiple-comparison bias - treat
the ranking as direction, not proof. Decisions only at evaluation gates.

Run:  python run_exitlab.py
"""

from __future__ import annotations

import pandas as pd

from bot.data import fetch_yahoo_daily
from run_eventstudy import (CAP, COST_PCT, ENTRY_GAIN, MAX_PRICE, MIN_DVOL,
                            MIN_PRICE, YEARS, universe)

HORIZON = 120

SCHEMES = {
    "A current_ladder":  dict(rungs=(1.5, 2.0), trail=0.30, trail_always=False,
                              breakeven_floor=False, time_stop=None, loss_ok=False),
    "B quicker_rungs":   dict(rungs=(1.25, 1.75), trail=0.30, trail_always=False,
                              breakeven_floor=False, time_stop=None, loss_ok=False),
    "C breakeven_floor": dict(rungs=(1.5, 2.0), trail=0.30, trail_always=False,
                              breakeven_floor=True, time_stop=None, loss_ok=False),
    "D time_stop_10d":   dict(rungs=(1.5, 2.0), trail=0.30, trail_always=False,
                              breakeven_floor=False, time_stop=10, loss_ok=True),
    "E day2_close":      dict(rungs=(), trail=None, trail_always=False,
                              breakeven_floor=False, time_stop=2, loss_ok=True),
    "F pure_trail_25":   dict(rungs=(), trail=0.25, trail_always=True,
                              breakeven_floor=False, time_stop=None, loss_ok=True),
    "G early_trail":     dict(rungs=(1.5, 2.0), trail=0.30, trail_always=True,
                              breakeven_floor=False, time_stop=None, loss_ok=True),
}


def replay(t, entry, o, h, l, c, n, s) -> dict:
    rungs = s["rungs"]
    frac = 1.0 / (len(rungs) + 1) if rungs else 1.0
    remaining, proceeds = 1.0, 0.0
    rungs_done = [False] * len(rungs)
    hw = entry
    end = min(n - 1, t + HORIZON)
    exit_day = end
    for j in range(t, end + 1):
        hi, lo, op = h[j], l[j], o[j]
        hw = max(hw, hi)
        # 1. rung sells (same optimistic same-day treatment as the baseline)
        for k, mult in enumerate(rungs):
            level = entry * mult
            if not rungs_done[k] and remaining > 1e-9 and hi >= level:
                fill = max(level, op) if j > t else level
                proceeds += frac * (fill / entry)
                remaining -= frac
                rungs_done[k] = True
        # 2. trailing exit
        trail_on = s["trail"] is not None and remaining > 1e-9 and j > t \
            and (s["trail_always"] or (rungs and all(rungs_done)) or not rungs)
        if trail_on:
            trig = hw * (1 - s["trail"])
            if lo <= trig:
                fill = min(trig, op) if op < trig else trig
                if s["loss_ok"] or fill > entry:
                    proceeds += remaining * (fill / entry)
                    remaining = 0.0
        # 3. breakeven floor: after the first rung banks, never round-trip
        if s["breakeven_floor"] and remaining > 1e-9 and j > t \
                and rungs and rungs_done[0] and lo <= entry:
            fill = op if op < entry else entry  # a gap can fill slightly below
            proceeds += remaining * (fill / entry)
            remaining = 0.0
        # 4. time stop
        if s["time_stop"] is not None and remaining > 1e-9 and j - t >= s["time_stop"]:
            proceeds += remaining * (c[j] / entry)
            remaining = 0.0
        if remaining <= 1e-9:
            exit_day = j
            break
    mark = remaining * (c[end] / entry) if remaining > 1e-9 else 0.0
    gross = proceeds + mark - 1.0
    return {"ret_net": max(gross - COST_PCT, -1.0),
            "days": exit_day - t if remaining <= 1e-9 else end - t,
            "bag": remaining > 1e-9 and (c[end] / entry) < 1.0}


def main() -> None:
    syms = universe()
    print(f"Universe: {len(syms)} live-tradeable symbols (same as the event study)\n")

    # Find events ONCE, replay every scheme on the identical set.
    events = []
    for sym in syms:
        try:
            bars = fetch_yahoo_daily(sym, years=YEARS)
            bars = bars[bars.index < pd.Timestamp.today().normalize()]
            o = bars["open"].to_numpy(float)
            h = bars["high"].to_numpy(float)
            l = bars["low"].to_numpy(float)
            c = bars["close"].to_numpy(float)
            dv = (bars["close"] * bars["volume"]).rolling(20).mean().shift(1).to_numpy(float)
            n = len(bars)
            i = 0
            while i < n - 1:
                trigger = o[i] * (1 + ENTRY_GAIN)
                entry = trigger * (1 + CAP)
                if (MIN_PRICE <= o[i] <= MAX_PRICE) and h[i] >= entry and o[i] > 0 \
                        and dv[i] == dv[i] and dv[i] >= MIN_DVOL:
                    events.append((sym, i, entry, o, h, l, c, n))
                    # advance past the BASELINE ladder's occupancy so every
                    # scheme sees the identical event list
                    from run_eventstudy import replay_ladder

                    base = replay_ladder(i, entry, o, h, l, c, n, HORIZON)
                    i = base["_end"] + 1
                else:
                    i += 1
        except Exception as e:
            print(f"  {sym}: skipped ({e})")
    print(f"{len(events)} spike events - every scheme replays this exact set\n")
    if not events:
        return

    rows = []
    for name, s in SCHEMES.items():
        res = [replay(t, entry, o, h, l, c, n, s)
               for _, t, entry, o, h, l, c, n in events]
        r = pd.DataFrame(res)
        rows.append({
            "scheme": name,
            "net_avg": round(100 * r["ret_net"].mean(), 2),
            "median": round(100 * r["ret_net"].median(), 2),
            "win%": round(100 * (r["ret_net"] > 0).mean()),
            "bags%": round(100 * r["bag"].mean()),
            "avg_days": round(r["days"].mean()),
            "worst": round(100 * r["ret_net"].min(), 1),
            "loss_exits": "yes" if s["loss_ok"] else "NO",
        })
    out = pd.DataFrame(rows).sort_values("net_avg", ascending=False)
    print(out.to_string(index=False))
    out.to_csv("exitlab_results.csv", index=False)
    print(f"\n[validation] scheme A must match run_eventstudy's numbers on this "
          f"universe - if it doesn't, distrust everything above.")
    print("[caveats] small n; 7-way comparison inflates the winner; survivorship;")
    print("daily-bar fills; decisions only at evaluation gates. -> exitlab_results.csv")


if __name__ == "__main__":
    main()
