"""Growthlab: the user's indicator spec, put on trial before it trades.

User directive 2026-08-14: "Look at the earnings, volume, rsi and macd as
well to pick high growth stocks with good volume." This lab tests exactly
that recipe on the survivorship-free machine BEFORE any wiring - the same
road every live rule traveled.

PRE-REGISTERED DESIGN (declared before results were seen):
  Universe   snapshot of a session ~6 MONTHS AGO (not today - a today-
             universe would quietly exclude everything that died since,
             the survivorship sin this lab exists to avoid): $5-1000,
             >= $10M traded, common-stock symbols, 350 sampled (seed 7).
  Data       1y adjusted daily bars per symbol; TTM revenue growth from
             quarterly financials (Polygon).
  Signals    RSI14 crossing up through 55 ("rsi_ignite")
             MACD(12,26,9) line crossing above signal ("macd_cross")
             volume >= 1.5x its 20d average ("vol_expand")
             TTM revenue growth >= +15% ("growth")
  Cells      C1 rsi_ignite | C2 macd_cross | C3 (C1|C2)+vol_expand
             C4 THE USER SPEC: (C1|C2) + vol_expand + growth
             C5 growth + vol_expand only
  Entry      next session's OPEN after the signal day
  Exits      close of day +1 / +3 / +5 (OVERNIGHT HOLDS - lab only; wiring
             a growth sleeve needs the user to relax the EOD-flatten rule
             for it, an explicit decision at the gate)
  Friction   0.4% round trip. One live signal per symbol at a time.
  Bar        n>=250 AND net>=+1.5%/trade is the standing wiring bar; for
             1-5 day large-cap holds even +0.5% can be excellent - report
             everything, judge at the gate.

Run:  python run_growthlab.py [--sample 350]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from bot import polygon

log = logging.getLogger("bot.growthlab")

UNIVERSE_DAY = "2026-02-13"   # ~6 months back: includes the later-dead
LO, HI = 5.0, 1000.0
MIN_DVOL = 10e6
GROWTH_MIN = 0.15
VOL_EXPAND = 1.5
FRICTION = 0.004
HORIZONS = (1, 3, 5)
OUT = Path("growthlab_results.csv")


def rsi14(close: pd.Series) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = up / dn.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)


def macd_cross_up(close: pd.Series) -> pd.Series:
    macd = close.ewm(span=12, adjust=False).mean() \
        - close.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    above = macd > sig
    return above & ~above.shift(1, fill_value=False)


def symbol_events(symbol: str, growth: float | None) -> list[dict]:
    """All signal-day events for one symbol with forward returns."""
    end = dt.date.today().isoformat()
    start = (dt.date.today() - dt.timedelta(days=380)).isoformat()
    bars = polygon.daily_bars(symbol, start, end, adjusted=True)
    if bars is None or len(bars) < 60:
        return []
    c, v, o = bars["close"], bars["volume"], bars["open"]
    rsi = rsi14(c)
    rsi_ig = (rsi > 55) & (rsi.shift(1) <= 55)
    macd_x = macd_cross_up(c)
    volx = v >= VOL_EXPAND * v.rolling(20).mean()
    out = []
    last_exit = -1
    for i in range(30, len(bars) - max(HORIZONS) - 1):
        if i <= last_exit:
            continue  # one live signal per symbol at a time
        sig_any = bool(rsi_ig.iloc[i] or macd_x.iloc[i])
        if not (sig_any or volx.iloc[i]):
            continue
        entry = float(o.iloc[i + 1])
        if not (LO <= entry <= HI) or entry <= 0:
            continue
        ev = {"symbol": symbol, "day": str(bars["date"].iloc[i].date()),
              "rsi_ignite": bool(rsi_ig.iloc[i]),
              "macd_cross": bool(macd_x.iloc[i]),
              "vol_expand": bool(volx.iloc[i]),
              "growth": (growth is not None and growth >= GROWTH_MIN)}
        for h in HORIZONS:
            ev[f"net_{h}d"] = round(
                100 * (float(c.iloc[i + h]) / entry - 1 - FRICTION), 2)
        out.append(ev)
        last_exit = i + max(HORIZONS)
    return out


def study(sample: int = 350) -> list[str]:
    g = polygon.grouped_daily(UNIVERSE_DAY, adjusted=False)
    if g is None:
        return ["growthlab: universe day unavailable"]
    g = g[[bool(s.isalpha() and len(s) <= 5 and not (len(s) == 5 and s[-1] in "WUR"))
           for s in g["symbol"].astype(str)]]
    g = g[(g["close"] >= LO) & (g["close"] <= HI)
          & (g["volume"] * g["vwap"] >= MIN_DVOL)]
    syms = sorted(g["symbol"])
    if len(syms) > sample:
        syms = list(pd.Series(syms).sample(sample, random_state=7))
    log.info("growthlab universe (%s): %d symbols, sampling %d",
             UNIVERSE_DAY, len(g), len(syms))
    rows: list[dict] = []
    for n, s in enumerate(syms, 1):
        rows.extend(symbol_events(s, polygon.ttm_revenue_growth(s)))
        if n % 50 == 0:
            log.info("growthlab: %d/%d symbols, %d events", n, len(syms), len(rows))
    if not rows:
        return ["growthlab: no events"]
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)

    sig_any = df["rsi_ignite"] | df["macd_cross"]
    cells = [
        ("C1 rsi_ignite", df[df["rsi_ignite"]]),
        ("C2 macd_cross", df[df["macd_cross"]]),
        ("C3 (rsi|macd)+vol", df[sig_any & df["vol_expand"]]),
        ("C4 USER SPEC +growth", df[sig_any & df["vol_expand"] & df["growth"]]),
        ("C5 growth+vol only", df[df["growth"] & df["vol_expand"]]),
    ]
    lines = [f"GROWTHLAB - user spec on trial ({len(df)} events, "
             f"{df['symbol'].nunique()} symbols, universe of {UNIVERSE_DAY}, "
             f"delisted included, overnight holds)"]
    for label, c in cells:
        if not len(c):
            lines.append(f"  {label:22} (no events)")
            continue
        parts = []
        for h in HORIZONS:
            net = c[f"net_{h}d"]
            parts.append(f"{h}d {net.mean():+5.2f}%/{100 * (net > 0).mean():.0f}%")
        lines.append(f"  {label:22} n={len(c):<6} " + "  ".join(parts))
    lines.append("  (net/win per horizon; friction 0.4% RT; wiring bar n>=250 &"
                 " net>=+1.5% - horizon-adjusted judgment at the gate)")
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=350)
    args = ap.parse_args()
    if not polygon.available():
        raise SystemExit("POLYGON_API_KEY not set")
    print("\n".join(study(args.sample)))


if __name__ == "__main__":
    main()
