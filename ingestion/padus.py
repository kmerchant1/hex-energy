"""Load PAD-US 4.1 (Texas) protected areas into PostGIS — the protected/federal-land veto.

Uses the combined analysis layer (Fee + Designation + Easement + ...). The fatal-flaw
veto keys on GAP status 1/2 (strict biodiversity protection) and federal ownership.

Creates:
  protected_areas — TX protected polygons (geom MultiPolygon 4326, gap_sts, own_type, ...)

Usage: python ingestion/padus.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion._db import get_pilot_boundary, query, write_gdf  # noqa: E402

GDB = "data/raw/PADUS4_1_StateTX.gdb"
LAYER = "PADUS4_1Comb_DOD_Trib_NGP_Fee_Desig_Ease_State_TX"
COLS = ["Category", "Own_Type", "Own_Name", "Mang_Type", "Unit_Nm",
        "Pub_Access", "GAP_Sts", "d_GAP_Sts", "IUCN_Cat"]


def main() -> None:
    print(f"PAD-US protected areas: {LAYER}")
    gdf = gpd.read_file(GDB, layer=LAYER, columns=COLS, engine="pyogrio")
    n = write_gdf(gdf, "protected_areas", clip_geom=get_pilot_boundary())
    print(f"  protected_areas rows: {n}")

    print("  breakdown by GAP status (1/2 = strict protection -> hard veto):")
    for gap, cnt in query(
        "SELECT gap_sts, count(*) FROM protected_areas GROUP BY gap_sts ORDER BY gap_sts"
    ):
        print(f"    GAP {gap}: {cnt}")
    print("  by ownership type:")
    for ot, cnt in query(
        "SELECT own_type, count(*) FROM protected_areas GROUP BY own_type ORDER BY count(*) DESC LIMIT 6"
    ):
        print(f"    {ot}: {cnt}")


if __name__ == "__main__":
    main()
