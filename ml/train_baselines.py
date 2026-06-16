"""P1 Checkpoint 4 — Baselines: does any real signal exist (and does geography help)?

Compares, under COUNTY-GROUPED cross-validation (no county in both train & test):
  1. region base rate   — predict each region's mean build rate (the market signal)
  2. logistic (all features)
  3. XGBoost (ALL features: geography + region + type + size)
  4. XGBoost (NO geography: region + type + size only)

The decisive test: if (3) ~= (4) ~= (2), the geographic county features add nothing
and we pivot to site-level features. Run for ALL-TX and ERCOT-only.

Usage: python ml/train_baselines.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import ARTIFACTS  # noqa: E402

warnings.filterwarnings("ignore")

GEO = [
    "dist_line_mi", "dist_hv_line_mi", "dist_substation_mi", "line_km_per_1000km2",
    "substation_count", "substation_per_1000km2", "max_substation_kv", "queued_mw",
    "wetland_pct", "floodway_pct", "protected_pct", "mean_slope_pct", "pct_steep",
    "developable_frac", "area_km2",
]
PROJ_NUM = ["mw", "q_year"]
CAT = ["region", "type_clean"]
N_SPLITS = 5


def xgb():
    return XGBClassifier(
        max_depth=3, n_estimators=250, learning_rate=0.04, subsample=0.8,
        colsample_bytree=0.8, reg_lambda=1.0, min_child_weight=3,
        eval_metric="logloss", n_jobs=-1, verbosity=0,
    )


def cv_model(make_model, X, y, groups):
    rocs, prs = [], []
    for tr, te in GroupKFold(N_SPLITS).split(X, y, groups):
        m = make_model()
        m.fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[te])[:, 1]
        rocs.append(roc_auc_score(y[te], p))
        prs.append(average_precision_score(y[te], p))
    return np.mean(rocs), np.std(rocs), np.mean(prs), np.std(prs)


def cv_region_baseline(df, y, groups):
    rocs, prs = [], []
    for tr, te in GroupKFold(N_SPLITS).split(df, y, groups):
        rate = df.iloc[tr].groupby("region")["label"].mean()
        glob = y[tr].mean()
        p = df.iloc[te]["region"].map(rate).fillna(glob).to_numpy()
        # roc undefined if test fold is single-class; guard
        if len(np.unique(y[te])) < 2:
            continue
        rocs.append(roc_auc_score(y[te], p))
        prs.append(average_precision_score(y[te], p))
    return np.mean(rocs), np.std(rocs), np.mean(prs), np.std(prs)


def run(df: pd.DataFrame, title: str) -> None:
    y = df["label"].to_numpy()
    groups = df["fips_code"].to_numpy()
    base = y.mean()
    print(f"\n{'='*64}\n{title}  (n={len(df)}, built={int(y.sum())}, base rate={base:.0%})\n{'='*64}")
    if len(df) < 60 or df["label"].nunique() < 2:
        print("  too small / single-class — skipped")
        return

    X_all = pd.get_dummies(df[GEO + PROJ_NUM + CAT], columns=CAT)
    X_nogeo = pd.get_dummies(df[PROJ_NUM + CAT], columns=CAT)

    print(f"  {'model':28} {'AUC-ROC':>16} {'AUC-PR':>16}   (PR floor={base:.2f})")
    def line(name, r):
        print(f"  {name:28} {r[0]:.3f} ± {r[1]:.3f}    {r[2]:.3f} ± {r[3]:.3f}")

    line("region base rate", cv_region_baseline(df, y, groups))
    line("logistic (all features)", cv_model(
        lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)), X_all, y, groups))
    line("XGBoost (ALL features)", cv_model(xgb, X_all, y, groups))
    line("XGBoost (NO geography)", cv_model(xgb, X_nogeo, y, groups))


def main() -> None:
    df = pd.read_parquet(ARTIFACTS / "training_data.parquet")
    df["q_year"] = pd.to_numeric(df["q_year"], errors="coerce")
    df["q_year"] = df["q_year"].fillna(df["q_year"].median())
    df["mw"] = df["mw"].fillna(df["mw"].median())

    run(df, "ALL TX")
    run(df[df.region == "ERCOT"].reset_index(drop=True), "ERCOT ONLY")

    print("\nRead: if 'XGBoost (ALL)' ≈ 'XGBoost (NO geography)' ≈ 'region base rate',")
    print("the county-level geographic features add ~nothing → pivot to site-level features.")


if __name__ == "__main__":
    main()
