"""P1 Step 3 — Assemble the training table and gut-check for signal.

Joins labels (the answer key) to county_features (the descriptors) on fips → one row
per labeled project = [project fields] + [county features] + label. Persists to a DB
table and a parquet file, then runs the "hint of signal" check: do BUILT projects sit
in counties with meaningfully different features than DEAD ones?

Creates:
  training_data (DB table) + ml/artifacts/training_data.parquet

Usage: python ml/build_training_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import ARTIFACTS  # noqa: E402
from ingestion._db import engine, query  # noqa: E402

# County-level numeric features used for the signal check / training.
FEATURES = [
    "dist_line_mi", "dist_hv_line_mi", "dist_substation_mi",
    "line_km_per_1000km2", "substation_count", "substation_per_1000km2",
    "max_substation_kv", "queued_mw", "wetland_pct", "floodway_pct",
    "protected_pct", "mean_slope_pct", "pct_steep", "developable_frac", "area_km2",
]

JOIN_SQL = """
    SELECT l.project_id, l.q_id, l.label, l.fips_code, l.county, l.region,
           l.type_clean, l.mw, l.q_year, l.poi_voltage_kv,
           cf.area_km2, cf.dist_line_mi, cf.dist_hv_line_mi, cf.dist_substation_mi,
           cf.line_km_per_1000km2, cf.substation_count, cf.substation_per_1000km2,
           cf.max_substation_kv, cf.queued_mw, cf.wetland_pct, cf.floodway_pct,
           cf.protected_pct, cf.mean_slope_pct, cf.pct_steep, cf.developable_frac
    FROM labels l
    JOIN county_features cf ON cf.fips = l.fips_code
"""


def main() -> None:
    eng = engine()
    df = pd.read_sql(JOIN_SQL, eng)

    n_labels = query("SELECT count(*) FROM labels")[0][0]
    print(f"join: {len(df)} training rows  (labels={n_labels}, "
          f"dropped {n_labels - len(df)} with no matching county)")

    # Persist
    df.to_sql("training_data", eng, if_exists="replace", index=False)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ARTIFACTS / "training_data.parquet", index=False)
    print(f"wrote training_data table + {ARTIFACTS/'training_data.parquet'}")

    # --- null report on project-level fields ---
    print("\nnull counts (project fields):")
    for c in ["mw", "q_year", "type_clean"]:
        print(f"  {c:12} {int(df[c].isna().sum())}")

    # --- coarse signal: build rate by region/type ---
    print("\nbuild rate by region:")
    for region, g in df.groupby("region"):
        print(f"  {region or '(none)':8} {g['label'].mean()*100:5.1f}%  (n={len(g)})")
    print("build rate by resource type:")
    for t, g in df.groupby("type_clean"):
        if len(g) >= 20:
            print(f"  {str(t):10} {g['label'].mean()*100:5.1f}%  (n={len(g)})")

    # --- feature signal: built vs dead means + point-biserial corr ---
    def signal(sub: pd.DataFrame, title: str) -> None:
        print(f"\n=== HINT OF SIGNAL — {title} (n={len(sub)}, built={int(sub['label'].sum())}) ===")
        built = sub[sub.label == 1][FEATURES].mean()
        dead = sub[sub.label == 0][FEATURES].mean()
        corr = sub[FEATURES + ["label"]].corr(numeric_only=True)["label"].drop("label")
        order = corr.abs().sort_values(ascending=False).index
        print(f"  {'feature':22} {'built':>10} {'dead':>10} {'corr':>7}")
        for f in order:
            print(f"  {f:22} {built[f]:>10.2f} {dead[f]:>10.2f} {corr[f]:>+7.2f}")

    signal(df, "ALL TX")
    signal(df[df.region == "ERCOT"], "ERCOT only")


if __name__ == "__main__":
    main()
