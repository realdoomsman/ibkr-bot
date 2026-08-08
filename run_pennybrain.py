"""Penny Brain: machine learning on YOUR OWN radar picks.

The Penny Radar logs every day's spike-tier movers and later fills in what
each one did over the next 1/5/20 trading days. Once enough picks have
resolved, this trains a model to answer the question behind the whole penny
sleeve: WHICH spikes keep paying, and which are exit pumps?

Two outputs, both honest:
  1. The scoreboard - the raw base rates ("do they come back?"), computed
     from the user's own picks, no model needed.
  2. The model - logistic regression (tiny data demands a tiny model) that
     scores each new radar pick: P(still up 5 trading days later). Its
     coefficients say WHICH trait matters (liquidity? drawdown? momentum?).

The model stays ADVISORY: run_pennyradar logs its score next to each pick
("BRAIN p=0.31") so weeks of predictions can be checked against outcomes
before anyone trusts it. Same rule as the trade filter: commentator first,
gatekeeper only with evidence.

Run:  python run_pennybrain.py       (scheduled Fridays 17:15)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("bot.pennybrain")

RADAR_CSV = Path("penny_radar.csv")
MODEL_PATH = Path("models/penny_brain.pkl")
META_PATH = Path("models/penny_brain_meta.json")
REPORT_PATH = Path("pennybrain_report.txt")
FEATURES = ["price", "dollar_vol_m", "ann_vol", "best_day", "mom_1m", "max_dd_2y"]
LABEL_COL = "fwd_5d"
MIN_ROWS = 60          # below this, coefficients are astrology
TEST_FRAC = 0.3        # newest 30% of picks held out, time-ordered


def scoreboard(radar: pd.DataFrame) -> list[str]:
    lines = [f"Penny Brain scoreboard ({len(radar)} picks tracked)",
             "  (returns: pick-day open -> close of session 1 / 5 / 20)"]
    for col, label in (("fwd_1d", "day 1"), ("fwd_5d", "day 5"), ("fwd_20d", "day 20")):
        vals = pd.to_numeric(radar[col], errors="coerce").dropna()
        if len(vals):
            lines.append(f"  {label:>8}: avg {vals.mean():+6.1f}%  median {vals.median():+6.1f}%  "
                         f"still positive {100 * (vals > 0).mean():.0f}%  (n={len(vals)})")
    dead = int((radar["status"] == "no_data(delisted?)").sum())
    if dead:
        lines.append(f"  possibly delisted after being picked: {dead}")
    lines.append('  ("do they come back?" - this table IS the answer, updated daily)')
    return lines


EMBARGO_DAYS = 9  # calendar days ~ >5 trading sessions: the fwd_5d label span


def train(radar: pd.DataFrame) -> list[str]:
    labeled = radar.dropna(subset=[LABEL_COL]).copy()
    labeled["_label"] = (pd.to_numeric(labeled[LABEL_COL], errors="coerce") > 0).astype(int)
    labeled = labeled.sort_values("date", kind="stable")
    n = len(labeled)
    if n < MIN_ROWS:
        return [f"Model: not yet - {n}/{MIN_ROWS} resolved picks collected. "
                f"The radar adds ~8/day; training unlocks itself."]

    # Split BY DATE with an embargo, mirroring the trade filter's discipline:
    # a row-count split lets one day's correlated picks straddle the boundary,
    # and the training tail's fwd_5d windows overlap the first test days -
    # both inflate the holdout AUC exactly when it first unlocks.
    dates = pd.to_datetime(labeled["date"])
    unique = dates.drop_duplicates().sort_values()
    if len(unique) < 8:
        return [f"Model: not yet - {n} picks but only {len(unique)} distinct days; "
                f"need more calendar spread for an honest holdout."]
    cut = unique.iloc[int(len(unique) * (1 - TEST_FRAC)) - 1]
    tr_mask = (dates <= cut).to_numpy()
    te_mask = (dates > cut + pd.Timedelta(days=EMBARGO_DAYS)).to_numpy()

    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X = labeled[FEATURES].apply(pd.to_numeric, errors="coerce")
    y = labeled["_label"].to_numpy()
    if len(set(y[tr_mask])) < 2:  # guard the slice actually fitted, not the whole set
        return [f"Model: not yet - the {int(tr_mask.sum())} training picks all share one outcome."]
    pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000)),
    ])
    pipe.fit(X[tr_mask], y[tr_mask])
    split, n_test = int(tr_mask.sum()), int(te_mask.sum())

    lines = [f"Model: trained on {split} picks through {cut.date()}, tested on "
             f"{n_test} picks after a {EMBARGO_DAYS}-day embargo."]
    y_te = y[te_mask]
    if len(y_te) >= 10 and 0 < y_te.mean() < 1:
        p_te = pipe.predict_proba(X[te_mask])[:, 1]
        auc = roc_auc_score(y_te, p_te)
        lines.append(f"  Held-out AUC {auc:.3f} (0.5 = coin flip - expect near that at first)")
    else:
        auc = None
        lines.append("  Held-out set too small/one-sided for an AUC yet.")

    coefs = pipe.named_steps["clf"].coef_[0]
    order = np.argsort(-np.abs(coefs))
    lines.append("  What predicts 'spike keeps paying' so far (standardized weights):")
    for i in order:
        lines.append(f"    {FEATURES[i]:>13}: {coefs[i]:+.2f}")

    import joblib

    MODEL_PATH.parent.mkdir(exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    joblib.dump(pipe, tmp)
    import os

    os.replace(tmp, MODEL_PATH)
    META_PATH.write_text(json.dumps({
        "trained": dt.datetime.now().isoformat(timespec="seconds"),
        "n_train": split, "n_test": n_test,
        "holdout_auc": round(auc, 4) if auc is not None else None,
        "features": FEATURES, "label": f"{LABEL_COL} > 0",
        "caveats": "picks-only sample (no counterfactuals); survivorship in "
                   "forward returns is partly visible via delisted status; "
                   "advisory-only until scored against live picks",
    }, indent=2), encoding="utf-8")
    lines.append(f"  Saved {MODEL_PATH} - the radar now scores new picks with it.")
    return lines


def score_pick(pick: dict) -> float | None:
    """P(this fresh radar pick is still up in 5 days). None if no model.
    Never raises - callers log it as a bonus, not a dependency."""
    try:
        if not MODEL_PATH.exists():
            return None
        import joblib

        pipe = joblib.load(MODEL_PATH)
        row = pd.DataFrame([{f: pick.get(f) for f in FEATURES}])
        return float(pipe.predict_proba(row.apply(pd.to_numeric, errors="coerce"))[0, 1])
    except Exception:
        log.debug("penny brain scoring failed", exc_info=True)
        return None


def grade_predictions(radar: pd.DataFrame) -> list[str]:
    """Score the brain's LIVE predictions against what actually happened.

    Held-out AUC says how the model looked in the past; this says how its
    real, timestamped calls are doing - the number a promotion decision
    actually needs. Joins penny_predictions.csv to resolved radar picks."""
    pred_file = Path("penny_predictions.csv")
    if not pred_file.exists():
        return ["Live grades: no predictions ledgered yet (the radar writes them "
                "once the model exists)."]
    preds = pd.read_csv(pred_file, dtype={"symbol": str})
    preds = preds.drop_duplicates(subset=["date", "symbol"], keep="first")
    joined = preds.merge(
        radar[["date", "symbol", LABEL_COL, "status"]], on=["date", "symbol"], how="inner"
    )
    dead = int(joined["status"].astype(str).str.startswith("no_data").sum())
    merged = joined.dropna(subset=[LABEL_COL])
    if not len(merged):
        return [f"Live grades: {len(preds)} predictions made, none resolved yet "
                f"(outcomes take 5+ sessions)."]
    hi = merged[merged["p"] >= 0.5]
    lo = merged[merged["p"] < 0.5]
    lines = [f"Live grades ({len(merged)} resolved predictions):"]
    for name, g in (("said KEEPS PAYING (p>=0.5)", hi), ("said FADES (p<0.5)", lo)):
        if len(g):
            fwd = pd.to_numeric(g[LABEL_COL], errors="coerce")
            lines.append(f"  {name}: avg {fwd.mean():+.1f}% by day 5, "
                         f"{100 * (fwd > 0).mean():.0f}% still up  (n={len(g)})")
    if dead:
        lines.append(f"  CAVEAT: {dead} predicted pick(s) stopped trading before "
                     f"resolving - the grades above exclude the worst outcomes.")
    lines.append("  (The KEEPS group must beat the FADES group over weeks, or the "
                 "brain stays a commentator.)")
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not RADAR_CSV.exists():
        print("No penny_radar.csv yet - run the radar first.")
        return
    radar = pd.read_csv(RADAR_CSV, dtype={"symbol": str})
    lines = scoreboard(radar) + [""] + grade_predictions(radar) + [""] + train(radar)
    report = "\n".join(lines)
    REPORT_PATH.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
