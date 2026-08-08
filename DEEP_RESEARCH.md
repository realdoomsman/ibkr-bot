# Deep Research Expedition — hidden gems report (2026-08-08)

18 agents, 4 waves (hunt -> triage -> deep-dive -> synthesis), 561 research
actions. Several finds were VERIFIED HANDS-ON: libraries installed into our
venv and numerically tested, SEC/NASDAQ endpoints live-probed, math re-derived
against our actual ladder parameters. Full cited detail: workflow archive.

## THE GEMS (ranked)

1. **EDGAR intraday tripwire** — one unfiltered `getcurrent` Atom call per
   30-60s covers the ENTIRE market's new filings (8-K item numbers arrive in
   the feed summary; ~1-min dissemination). Plus EFTS keyword sweeps on held
   names ("warrant inducement", "registered direct"). FLATTEN-NOW rule table
   for held names on 424B1/B4/B5, EFFECT, 8-K 3.02/5.03. Closes our single
   worst tail: the mid-session dilution rug on a stop-less penny. Exit on
   INFORMATION, not price — fully compatible with the no-stop directive.
   ~1 day to build on bot/halts.py patterns. BUILD FIRST.
2. **Dedicated paper usernames have NO 2FA** (IBKR-support-confirmed in a
   buried GitHub discussion, Mar 2026) — the contradicting reports all
   conflate logging in with the LIVE username in paper mode. If a 10-min test
   confirms: IBC auto-login becomes fully zero-touch and the Sunday ritual
   dies. Fallback keys verified if wrong.
3. **bidask EDGE estimator** (JFE 2024) — effective spread computed from the
   5-min OHLC bars we already fetch. INSTALLED + verified in our venv
   (synthetic 1% spread recovered as 1.06%±0.02%). Live on our picks:
   DAMD 0.57% (tradeable), BJDX 1.35% (half-size), TNMG 1.53% (reject).
   Derived gate from our ladder math: full clip <=0.75%, half 0.75-1.5%,
   reject >1.5%. The system's largest unmeasured cost, measured. ~60 lines.
4. **purgedcv + arch SPA/MCS** — the statistical honesty layer, INSTALLED +
   verified to 1e-12 vs hand-coded Lopez de Prado formulas. Deflated Sharpe
   at true trial counts, PBO (transpose footgun documented), min track record
   length ("promotable in ~N more days"). ~40 lines into gate/review/lab.
5. **SmallCapLab dataset** — 6,600+ labeled gapper events + regime file,
   $14.99/one month (NEEDS USER APPROVAL to buy). Instant priors for our
   pre-registered gates; headline base rates re-verified on-site (two
   corrected). Their short log trades OUR radar names (BJDX, TNMG).
6. **The split-adjustment bug class** — a 0-star repo's forensic ledger:
   adjusted-daily joined to raw-intraday minted 63% of a replication's
   profit. bot/data.py HAS THIS PATTERN TODAY (ADJUSTED_LAST rescales volume
   too → post-reverse-split RVOL wrong by the split factor). Audit + ATR%
   normalization + NVDA/CMG split regression tests. Half a day. PROTECTS THE
   ENGINES RUNNING NOW.
7. **OAuth 1.0a + ibind kill switch** — IBKR quietly allows individual/paper
   OAuth consumer registration (docs say gateway-only). A ~40-line
   flatten-everything script that shares ZERO failure modes with TWS/Java —
   the only channel that works when the gateway layer itself is what died.
   Registration is user-executed, free, ~2-week activation: file it BEFORE
   it's needed.
8. **XBRL companyfacts runway** — free JSON: cash + burn -> months-of-runway
   (worked example: 1.96 months -> warrant-inducement 8-K on schedule); share
   -count series doubles as a free reverse-split + death-spiral detector.
9. **Free toxicity triangle** — nasdaqlisted.txt FSI codes (331 live
   exchange-certified-broken companies today), exact ETF flag, category-105
   reverse-split RSS, and the UNADVERTISED open ftp.nyse.com SSR/LULD
   directories. Hours of work, keyless, makes three of our bans exact.
10. **tr8dr AmplitudeBasedLabeler** — ex-HFT quant's momentum-episode labeler
    that matches the ladder's payoff question ("did a real episode follow the
    trigger?") better than fixed-horizon labels. Vendor 2 files, offline only.

## ADOPT (ops/measurement, no strategy change)
- Corrected IBC runbook (our dormant IBC_SETUP.md has 3 defects incl. a
  config key that DOESN'T EXIST); pinned IBC 3.24.1 + offline Gateway;
  OverrideTwsApiPort=7497 makes the swap zero-code-change; IBC retires
  Sep 2026 — successor map pre-drawn.
- Watchdog grows hands: IBC telnet RECONNECTDATA -> RESTART -> ntfy ladder
  (~30 lines) converts stale-data alerts into 5-second self-heals.
- Fill-quality markout rig (Rule-605-style effective/realized spread at
  +15s/1m/5m, SQLite) — the measuring stick for every execution idea, and
  the first real consumer of our L1 subscription. Paper-simulator epistemics
  printed in every report (passive-fill conclusions VOID until live).
- Nightly toxicity enrichment (8 fields) + three regulatory-grade vetoes:
  FSI codes, suspension history, FINRA 22-25 ramp-profile conjunction
  (closes the non-China ramp-IPO gap).
- Meta-labeler honesty: true per-event purged CV, count the 14 hidden
  selection trials into the meta json, PBO diagnostics, isotonic calibration.
- Live-ops bundle: MOC unmodifiable after 15:50 (submit 15:49:55), empty-
  pacing-response detection, position-mismatch freeze, price-spike
  quarantine (one 50-share print can fake a +12% mover), GCP instance
  schedules (~70% Windows-license savings), DST/weekly-trigger bug, Saturday
  patch pinning. Carver's live 12-yr SR 0.80 = expect live ~50% of backtest.

## LAB QUEUE (pre-registered studies)
- Gates A-E on the jeet ladder anchored to SmallCapLab base rates (~70% of
  HODs by 10:30 -> test ENTRY_CUTOFF 15:00 vs 11:30; extension-fade tiers;
  regime throttle; PMH-chase veto; Gate E LOCKS the EOD flatten behind a
  27.7%-continuation null).
- RVOL correctness: slot-bucket time-of-day method + EMA denominator +
  ib_async's NATIVE volumeRate3/5/10Min fields (we hand-rolled what the
  library ships).
- SSR asymmetry (peer-reviewed sign): ssr_active = mild hold-longer;
  ssr_triggered_TODAY = hard entry veto (the "+12% mover" that got there
  via a -10% flush). Computable from our own L1.
- Execution A/B queue: PEG MID / MIDPRICE for ladder legs, Adaptive-Urgent
  for urgent exits, sub-penny pricing under $1 — judged only by the markout
  rig. CALENDAR ITEM: Nov 2026 half-penny tick regime breaks all baselines
  and any hardcoded $0.01 arithmetic.
- Promotion/insider block: 5-day promotion clock, social-noise fizzle
  detection, Form 4 buys-into-strength (the one POSITIVE microcap signal).
- LULD band prediction (regulation math, free): position near its upper band
  is about to freeze — act BEFORE the halt. Start archiving halt history
  TODAY (rolls off after ~1 yr).

## STRATEGY CANDIDATES (through gates only)
- ORB on Stocks-in-Play: 2-week validation harness on free Alpaca SIP
  minute bars; gates G1-G8 committed to git BEFORE results (replication
  band, >=300 OOS trades, DSR>0 at declared trials, 3x measured slippage,
  perturbation survival). Nobody has published its 2024-26 OOS — our run IS
  the missing experiment. Expected honest OOS Sharpe 0.4-0.9.
- Flatten-adjacent shadow measurements (cost nothing, change nothing):
  late-day new-HOD trail widening; close-to-open RVOL premium ledger. Both
  face Gate E's null; the flatten stays locked unless evidence clears it.

## REJECTED (with reasons — money and months saved)
- IBeam/Web API as the trading path (wrong wire protocol; 5-concurrent
  history cap kills the scanner; OAuth kill switch is its ONLY role for us)
- IEX HIST pcaps for spread validation (13.3 GB/DAY measured; our own L1
  logging dominates)
- Nasdaq Daily List (login-walled; free triangle substitutes) and qlib PIT
  (wrong continent, framework buy-in)
- Community strategy marketplaces as engine sources (every live census:
  adversely-selected junk; QC Alpha Streams' top-5% still failed OOS)
- Sub-15-min mean reversion (Carver's live verdict: "It didn't work") and
  SmallCapLab's $299-699 short-side products (need stops + locates =
  structurally incompatible). Bonus: their own stop-sweep data implies the
  user's no-stop + hard-flatten combo is "at least not-crazy" in this tape.
