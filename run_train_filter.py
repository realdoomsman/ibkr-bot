"""Train the meta-labeling trade filter on REAL market data.

Meta-labeling (Lopez de Prado): the strategies keep proposing trades exactly
as they do now; this model learns - from how thousands of past signal-days
actually resolved - to estimate P(win) for each setup. Deployment is
ADVISORY-FIRST: the live bots journal the filter's opinion next to every
entry and the nightly review keeps the scoreboard. It gates nothing until
weeks of live evidence say it has earned that.

Training design (all machinery from the ML lab):
  - Universe: current baskets + the liquid end of the screen results, pooled
    across symbols for statistical power.
  - Features: the 5 classic signals + volatility/momentum context, all
    mechanically lagged (tests/ proves no feature sees the future).
  - Labels: triple-barrier outcomes (profit target vs stop vs expiry).
  - Validation: expanding walk-forward split BY DATE with an embargo of the
    label horizon, so no training label's window touches a test date.

Run:  python run_train_filter.py
Retrain policy: at evaluation gates (monthly), not on daily whims.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report
from xgboost import XGBClassifier

from bot.data import fetch_yahoo_daily
from run_ml_filter import HORIZON, SEED, build_dataset

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "trade_filter.ubj"
META_PATH = MODEL_DIR / "trade_filter_meta.json"
RESERVED = {"SPY", "QQQ"}  # ETF sleeves have their own logic
N_FOLDS = 5
MAX_SYMBOLS = 20


def training_universe() -> list[str]:
    """Current baskets + the liquid end of the last screen, deduped."""
    from bot.config import load_config

    cfg = load_config()
    symbols = list(dict.fromkeys(cfg.strategy.symbols + cfg.intraday.symbols))

    screen = Path("screen_results.csv")
    if screen.exists():
        with screen.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        rows.sort(key=lambda r: -float(r.get("dollar_vol_m", 0) or 0))
        for r in rows:
            s = r["symbol"].upper()
            if s not in symbols and s not in RESERVED and float(r.get("dollar_vol_m", 0) or 0) >= 50:
                symbols.append(s)
            if len(symbols) >= MAX_SYMBOLS:
                break
    return [s for s in symbols if s not in RESERVED]


def uniqueness_weights(exit_offsets: np.ndarray) -> np.ndarray:
    """Lopez de Prado average-uniqueness weights from each event's ACTUAL span.

    Adjacent daily events share most of their forward label window, and the
    panel pools correlated symbols on the same dates - unweighted, the model
    treats near-duplicate observations as independent evidence.
    """
    n = len(exit_offsets)
    conc = np.zeros(n + int(exit_offsets.max()) + 1)
    for i in range(n):
        conc[i : i + exit_offsets[i] + 1] += 1
    return np.array([
        float(np.mean(1.0 / conc[i : i + exit_offsets[i] + 1])) for i in range(n)
    ])


def build_panel(symbols: list[str]):
    frames = []
    today = pd.Timestamp.today().normalize()
    try:
        spy = fetch_yahoo_daily("SPY", years=5)
        spy = spy[spy.index < today]
    except Exception as e:
        print(f"  SPY unavailable ({e}) - market-context features will be absent")
        spy = None
    for sym in symbols:
        try:
            bars = fetch_yahoo_daily(sym, years=5)
            # COMPLETED sessions only: an intraday fetch includes today's
            # in-progress bar, which would make labels (and thus the model)
            # depend on what time of day training ran.
            bars = bars[bars.index < today]
            X, y, meta = build_dataset(bars, market=spy)
        except Exception as e:
            print(f"  {sym}: skipped ({e})")
            continue
        df = X.copy()
        df["_y"] = y
        df["_ret"] = meta["realized_ret"]
        df["_symbol"] = sym
        df["_w"] = uniqueness_weights(meta["exit_offset"].to_numpy(int))
        frames.append(df)
        print(f"  {sym}: {len(df)} samples")
    panel = pd.concat(frames).sort_index(kind="stable")
    return panel


def date_walk_forward(dates: pd.DatetimeIndex, n_folds: int, embargo_days: int):
    """Expanding train window, contiguous test blocks, embargo between them."""
    unique = dates.unique().sort_values()
    n = len(unique)
    first_test = n // (n_folds + 1)
    block = (n - first_test) // n_folds
    for k in range(n_folds):
        test_start = first_test + k * block
        test_end = n if k == n_folds - 1 else test_start + block
        train_end = max(0, test_start - embargo_days)  # purge the label overlap
        yield unique[:train_end], unique[test_start:test_end]


def make_model() -> XGBClassifier:
    return XGBClassifier(
        n_estimators=300, max_depth=3, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9,
        eval_metric="logloss", random_state=SEED,
    )


# Features added 2026-08-04. They deploy ONLY if the ablation below shows the
# expanded set at least matches the base set out-of-sample - features are
# guilty until proven innocent.
NEW_FEATURES = ["ret_1d", "gap_1d", "range_pct", "rel_volume",
                "dist_52w_high", "vol_ratio",
                "mkt_ret_5d", "mkt_ret_21d", "mkt_vol_20d"]


def walk_forward_eval(panel: pd.DataFrame, feature_cols: list[str], tag: str):
    """Walk-forward OOS predictions for one feature set. Returns
    (oos_probs, mask, auc, fold_baseline_evs)."""
    from sklearn.metrics import roc_auc_score

    oos = np.full(len(panel), np.nan)
    fold_evs: list[float] = []
    for k, (train_dates, test_dates) in enumerate(
        date_walk_forward(panel.index, N_FOLDS, HORIZON), 1
    ):
        tr = panel.index.isin(train_dates)
        te = panel.index.isin(test_dates)
        if tr.sum() < 500 or te.sum() == 0:
            print(f"  [{tag}] fold {k}: skipped (too little data)")
            continue
        model = make_model()
        model.fit(panel.loc[tr, feature_cols], panel.loc[tr, "_y"],
                  sample_weight=panel.loc[tr, "_w"])
        oos[te] = model.predict_proba(panel.loc[te, feature_cols])[:, 1]
        fold_ev = 100 * float(panel.loc[te, "_ret"].mean())
        fold_evs.append(round(fold_ev, 4))
        print(f"  [{tag}] fold {k}: train {tr.sum():>6d} -> test {te.sum():>5d} samples"
              f" (baseline EV {fold_ev:+.3f}%)")
    mask = ~np.isnan(oos)
    y = panel["_y"].to_numpy()[mask]
    auc = float(roc_auc_score(y, oos[mask])) if mask.any() and 0 < y.mean() < 1 else float("nan")
    return oos, mask, auc, fold_evs


def take_ev(oos, mask, rets, thr: float):
    """(EV%, n) of the trades the filter would take at `thr`, OOS only."""
    sel = mask & (oos > thr)
    return (100 * float(rets[sel].mean()), int(sel.sum())) if sel.any() else (None, 0)


def derive_thresholds(oos, mask, rets, baseline_pct: float,
                      min_trades: int = 300, min_edge_pct: float = 0.05):
    """Pick the TAKE threshold from measured OOS profit, not convention.

    The lowest threshold whose selected trades beat the baseline EV by
    min_edge_pct with at least min_trades behind it wins (lowest = most
    trades at acceptable quality). Falls back to 0.5 if nothing qualifies.
    The whole grid is returned for the metadata - transparency over magic."""
    table = []
    take_thr = None
    for thr in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        ev, n = take_ev(oos, mask, rets, thr)
        table.append({"thr": thr, "n": n, "ev_pct": round(ev, 4) if ev is not None else None})
        if take_thr is None and n >= min_trades and ev is not None \
                and ev >= baseline_pct + min_edge_pct:
            take_thr = thr
    return take_thr if take_thr is not None else 0.5, 0.35, table


def main() -> None:
    print("Assembling training universe...")
    symbols = training_universe()
    print(f"Universe ({len(symbols)}): {symbols}\n\nBuilding panel from 5y of real data:")
    panel = build_panel(symbols)
    feature_cols = [c for c in panel.columns if not c.startswith("_")]
    print(f"\nPanel: {len(panel)} samples x {len(feature_cols)} features, "
          f"{panel['_symbol'].nunique()} symbols, "
          f"{panel.index.nunique()} UNIQUE dates "
          f"({panel.index.min().date()} -> {panel.index.max().date()})")
    print("(Overlapping label windows + correlated symbols mean effective sample")
    print(" size is far below the row count - uniqueness weights partly correct this.)")
    if panel["_symbol"].nunique() < 8 or len(panel) < 10_000:
        raise SystemExit("Panel too small - refusing to overwrite the deployed model.")

    # ---- ABLATION: expanded features must beat the base set OOS to deploy.
    base_cols = [c for c in feature_cols if c not in NEW_FEATURES]
    rets_all = panel["_ret"].to_numpy()

    print("\nWalk-forward validation, BASE feature set "
          f"({len(base_cols)} features):")
    oos_b, mask_b, auc_b, _ = walk_forward_eval(panel, base_cols, "base")
    print(f"\nWalk-forward validation, EXPANDED feature set "
          f"({len(feature_cols)} features):")
    oos_f, mask_f, auc_f, fold_evs = walk_forward_eval(panel, feature_cols, "full")

    ev_b, n_b = take_ev(oos_b, mask_b, rets_all, 0.5)
    ev_f, n_f = take_ev(oos_f, mask_f, rets_all, 0.5)
    print("\nABLATION (out-of-sample):")
    print(f"  base:     AUC {auc_b:.4f} | take@0.5 EV "
          f"{'n/a' if ev_b is None else f'{ev_b:+.3f}%'} ({n_b} days)")
    print(f"  expanded: AUC {auc_f:.4f} | take@0.5 EV "
          f"{'n/a' if ev_f is None else f'{ev_f:+.3f}%'} ({n_f} days)")
    # Champion by OOS take-EV only when BOTH cells clear the same min-trades
    # floor derive_thresholds trusts (a mean over ~100 tail-selected trades is
    # noise, not evidence); otherwise fall back to AUC over all OOS days.
    # Strict > in both cases: new features must WIN, ties keep the base set.
    MIN_AB_TRADES = 300
    if ev_b is not None and ev_f is not None and n_b >= MIN_AB_TRADES and n_f >= MIN_AB_TRADES:
        use_full, basis = ev_f > ev_b, f"take@0.5 EV (both cells >= {MIN_AB_TRADES} trades)"
    else:
        use_full, basis = auc_f > auc_b, "AUC over all OOS days (EV cells too thin to trust)"
    if use_full:
        champ_cols, champ_name, oos_c, mask_c = feature_cols, "expanded", oos_f, mask_f
    else:
        champ_cols, champ_name, oos_c, mask_c = base_cols, "base", oos_b, mask_b
    print(f"  -> deploying the {champ_name.upper()} feature set (basis: {basis})")

    y_oos = panel["_y"].to_numpy()[mask_c]
    ret_oos = rets_all[mask_c]
    p_oos = oos_c[mask_c]

    print("\nOut-of-sample classification (threshold 0.5):")
    print(classification_report(y_oos, (p_oos > 0.5).astype(int), digits=3))

    print("Expected value per signal-day (REAL data, frictionless labels):")
    print(f"  Take EVERY day (baseline):   {100 * ret_oos.mean():+.3f}%  ({len(ret_oos)} days)")
    for thr in (0.5, 0.6):
        sel = p_oos > thr
        if sel.any():
            print(f"  Filter P(win) > {thr:.1f}:        "
                  f"{100 * ret_oos[sel].mean():+.3f}%  ({int(sel.sum())} days)")
        else:
            print(f"  Filter P(win) > {thr:.1f}:        selects nothing")
    vetoed = p_oos <= 0.5
    if vetoed.any():
        print(f"  What the filter VETOES:      {100 * ret_oos[vetoed].mean():+.3f}%  "
              f"({int(vetoed.sum())} days)  <- should be the worst bucket")

    # Decision thresholds from measured OOS profit, not convention.
    baseline_pct = 100 * float(ret_oos.mean())
    take_thr, skip_thr, thr_table = derive_thresholds(oos_c, mask_c, rets_all, baseline_pct)
    print(f"\nEV-derived thresholds: TAKE >= {take_thr:.2f}, SKIP < {skip_thr:.2f}")
    for row in thr_table:
        ev_s = "n/a" if row["ev_pct"] is None else f"{row['ev_pct']:+.3f}%"
        print(f"    p > {row['thr']:.2f}: EV {ev_s:>9}  ({row['n']} days)")

    print("\nTraining the deployment model on all data...")
    final = make_model()
    final.fit(panel[champ_cols], panel["_y"], sample_weight=panel["_w"])
    MODEL_DIR.mkdir(exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    final.save_model(tmp)
    import os

    # META FIRST, then the model: the advisor hot-reloads when the MODEL's
    # mtime changes and reads meta at that moment - writing meta second
    # opens a window where a new booster pairs with the old feature list.
    meta_tmp = META_PATH.with_suffix(".tmp")
    meta_tmp.write_text(json.dumps({
        "trained": dt.datetime.now().isoformat(timespec="seconds"),
        "symbols": symbols,
        "features": champ_cols,
        "feature_set": champ_name,
        "ablation_basis": basis,
        "ablation": {
            "base": {"auc": round(auc_b, 4), "take05_ev_pct": ev_b, "take05_n": n_b},
            "expanded": {"auc": round(auc_f, 4), "take05_ev_pct": ev_f, "take05_n": n_f},
        },
        "take_threshold": take_thr,
        "skip_threshold": skip_thr,
        "threshold_table": thr_table,
        "samples": int(len(panel)),
        "unique_dates": int(panel.index.nunique()),
        "per_fold_baseline_ev_pct": fold_evs,
        "oos_baseline_ev_pct": round(100 * float(ret_oos.mean()), 4),
        "oos_take05_ev_pct": round(100 * float(ret_oos[p_oos > 0.5].mean()), 4)
        if (p_oos > 0.5).any() else None,
        "horizon": HORIZON,
        "universe_selection": "survivorship-biased (chosen on CURRENT liquidity; "
                              "delisted/faded names structurally absent)",
        "caveats": "label-level EVs, no costs; gap-through fills modeled at the "
                   "exit-day open; effective sample size far below row count",
    }, indent=2), encoding="utf-8")
    os.replace(meta_tmp, META_PATH)
    os.replace(tmp, MODEL_PATH)
    print(f"Saved {MODEL_PATH} + metadata.")
    print(
        "\nHonest reading: these EVs are label-level (no costs), from ONE pooled"
        "\nhistory of a SURVIVORSHIP-BIASED universe (symbols picked for being"
        "\nliquid TODAY - the names that died on the way can never appear, so the"
        "\nbaseline EV overstates what was achievable). The filter runs ADVISORY-"
        "\nONLY; the nightly review keeps the real scoreboard, and gating is"
        "\nearned at an evaluation gate, not assumed today."
    )


if __name__ == "__main__":
    main()
