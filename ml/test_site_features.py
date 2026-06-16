"""P1 Path B — Does a SITE-level grid feature (connection voltage) add signal?

County geography added nothing (Checkpoint 4). The most tractable leakage-free
site-level feature is the grid-connection voltage parsed from poi_name (same source,
both classes). Its *missingness* leaks (no-POI projects skew built via a recording
artifact), so we IMPUTE missing voltage with the median and test only the magnitude.

Compares, under county-grouped CV (XGBoost), ALL-TX and ERCOT-only:
  A. project attrs only            (region, type, mw, q_year)   <- Checkpoint-4 winner
  B. + county geography
  C. + connection voltage (imputed)
  D. + county geography + connection voltage

Usage: python ml/test_site_features.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold
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
PROJ = ["mw", "q_year"]
CAT = ["region", "type_clean"]
SITE = ["poi_voltage_kv"]
N_SPLITS = 5


def xgb():
    return XGBClassifier(
        max_depth=3, n_estimators=250, learning_rate=0.04, subsample=0.8,
        colsample_bytree=0.8, reg_lambda=1.0, min_child_weight=3,
        eval_metric="logloss", n_jobs=-1, verbosity=0,
    )


def cv(X, y, groups):
    rocs, prs = [], []
    for tr, te in GroupKFold(N_SPLITS).split(X, y, groups):
        if len(np.unique(y[te])) < 2:
            continue
        m = xgb()
        m.fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[te])[:, 1]
        rocs.append(roc_auc_score(y[te], p))
        prs.append(average_precision_score(y[te], p))
    return np.mean(rocs), np.std(rocs), np.mean(prs), np.std(prs)


def run(df, title):
    y = df["label"].to_numpy()
    g = df["fips_code"].to_numpy()
    print(f"\n{'='*70}\n{title}  (n={len(df)}, built={int(y.sum())}, base={y.mean():.0%})\n{'='*70}")
    if len(df) < 60 or df["label"].nunique() < 2:
        print("  skipped (too small)"); return

    feats = {
        "A. project attrs only": PROJ + CAT,
        "B. + county geography": PROJ + CAT + GEO,
        "C. + connection voltage": PROJ + CAT + SITE,
        "D. + county geo + voltage": PROJ + CAT + GEO + SITE,
    }
    print(f"  {'features':28} {'AUC-ROC':>16} {'AUC-PR':>16}")
    for name, cols in feats.items():
        X = pd.get_dummies(df[cols], columns=CAT)
        r = cv(X, y, g)
        print(f"  {name:28} {r[0]:.3f} ± {r[1]:.3f}    {r[2]:.3f} ± {r[3]:.3f}")


def main():
    df = pd.read_parquet(ARTIFACTS / "training_data.parquet")
    df["q_year"] = pd.to_numeric(df["q_year"], errors="coerce")
    df["q_year"] = df["q_year"].fillna(df["q_year"].median())
    df["mw"] = df["mw"].fillna(df["mw"].median())
    # IMPUTE connection voltage so MISSINGNESS (the leaky part) carries no signal;
    # only the magnitude is tested.
    med_v = df["poi_voltage_kv"].median()
    df["poi_voltage_kv"] = df["poi_voltage_kv"].fillna(med_v)

    run(df, "ALL TX")
    run(df[df.region == "ERCOT"].reset_index(drop=True), "ERCOT ONLY")
    print("\nRead: if C/D ≈ A, connection voltage adds ~nothing → geography (even at the"
          "\nconnection point) doesn't predict success → pivot to deterministic screening.")


if __name__ == "__main__":
    main()
