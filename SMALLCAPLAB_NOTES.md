# SmallCapLab findings (captured 2026-08-08)

Premium data ($14.99/mo) browsed with the user logged in. The site's value
for us is DATA/base-rates, not its strategies (all short-side - incompatible
with our long-only no-stop system). Aggregate stats captured below; per-event
CSV export location still TBD (see bottom).

## Regime dashboard (2026-08-07 session)
- **Current regime score: 63/100 — ELEVATED** (0=heavy fades, 100=big squeezes)
- Fade rate 86% today (5d 80%); avg HOD move +21.2% (5d +38.0%); gapper count 7
- Sector heat: Consumer/Other 1.42x, Technology 1.05x hottest
- Geographic heat: **China/HK 1.91x hottest** — validates our China exclusion
  is filtering an active-pump cohort
- Primary drivers: failure pressure 86% vs 67% (5d) → "favors shorts"

## Research base rates (their multi-year history — our ground truth to match)
- **Close-below-open rate: avg 64.1%** (gap-ups fail ~2/3 of the time)
- PMH break rate: avg 41.4%
- Median HOD move from open: avg 31.9%
- Avg LOD move from open: avg -21.9%
- Gap fill "round trip" (close below prior close): avg 3.5%
- Close-below-VWAP rate: avg 72.5%
- HOD collapse rate: avg 13.1%
- **Time-of-HOD distribution: 46.6% of HODs by 9:45, 85%+ by 10:30, 99.9% by 13:00**
  → directly supports the Gate A test (ENTRY_CUTOFF 15:00 → earlier)
- Gap-fill speed: 71% of 50%+ gappers fill within 1 trading day (median 1d,
  mean 4.6d), ~18% never fill
- Prev close is a cover level ~38% of the time when touched intraday

## Dilution tool — INDEPENDENT VALIDATION of our EDGAR pipeline
**BJDX**: Dilution risk MODERATE (structural 5/10, near-term 4/10, severity 6/8)
- Mechanism: **Shelf (S-3)**; last raise 2026-06 (**43 days ago**); resale/share
  unlock **LIVE**; 1.03M shares out (confirms our 0.9M float finding); runway
  ~7mo; pro-forma cash $1.36M; share CAGR -59%/yr (reverse-split stable)
- **OUR BOT independently flagged the same**: S-3 (6/17) + EFFECT (6/26) +
  424B3 from our EDGAR checks. Two systems, same conclusion → our dilution
  detection works.

## TODO on next login
- Locate the per-event CSV exports ("Raw data downloads" premium feature) —
  not found on /research or the account menu; may be a per-chart download
  icon or a separate members area. Download gapper DB, runner DB, Market
  Character Daily, then the lab can calibrate against them.
- Dilution-score lookups still to capture: GPUS, WFF, TNMG, DAMD, ONFO, LGHL, YYAI.
