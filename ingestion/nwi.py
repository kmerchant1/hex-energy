"""Load NWI wetlands (TX_Wetlands, ~2.23M polygons) into PostGIS — streaming.

Constraints: ~8.6 GB RAM (can't read all at once) and File-GDB skip_features is O(n)
per chunk (too slow). Solution: stream the layer in a single sequential pass with
Fiona and flush fixed-size batches to PostGIS (bounded memory, no re-seeking).

Creates:
  wetlands — TX wetland polygons (geom MultiPolygon 4326, wetland_type, attribute, acres)

Usage: python ingestion/nwi.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import fiona
import geopandas as gpd
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion._db import engine, run_sql, table_count  # noqa: E402

GDB = "data/raw/TX_geodatabase_wetlands.gdb"
LAYER = "TX_Wetlands"
KEEP = ["WETLAND_TYPE", "ATTRIBUTE", "ACRES"]
BATCH = 50_000


def _flush(records, src_crs, eng, first):
    gdf = gpd.GeoDataFrame.from_features(records, crs=src_crs).to_crs("EPSG:4326")
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    gdf.columns = [c.lower() for c in gdf.columns]
    if gdf.geometry.name != "geom":
        gdf = gdf.rename_geometry("geom")
    gdf.to_postgis("wetlands", eng, if_exists="replace" if first else "append", index=False)
    return len(gdf)


def main() -> None:
    eng = engine()
    written = 0
    batch: list = []
    first = True
    with fiona.open(GDB, layer=LAYER) as src:
        src_crs = src.crs
        for feat in src:
            props = feat["properties"]
            batch.append({
                "geometry": shape(feat["geometry"]),
                "properties": {k: props.get(k) for k in KEEP},
            })
            if len(batch) >= BATCH:
                written += _flush(batch, src_crs, eng, first)
                first = False
                batch = []
                print(f"  ... {written:,} loaded", flush=True)
        if batch:
            written += _flush(batch, src_crs, eng, first)

    print("  building spatial index ...", flush=True)
    run_sql("CREATE INDEX IF NOT EXISTS wetlands_geom_idx ON wetlands USING GIST (geom);")
    print(f"  wetlands rows: {table_count('wetlands'):,}", flush=True)


if __name__ == "__main__":
    main()
