"""Pull FEMA NFHL FLOODWAYS for Texas via the ArcGIS REST service into PostGIS.

FEMA's statewide bulk download is broken and a full flood-zone REST pull times out,
but FLOODWAYS (the hard fatal-flaw veto — building essentially prohibited) are only
~5,285 polygons statewide. A single statewide query 500s, so we tile the state into
a grid of small bboxes, paginate each, and dedup by feature id. Broader A/AE/VE risk
zones are deferred (handled per-AOI later).

Creates:
  floodways — TX floodway polygons (geom MultiPolygon 4326) for the fatal-flaw veto.

Usage: python ingestion/fema.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import geopandas as gpd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion._db import engine, run_sql, table_count  # noqa: E402

LAYER = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
# Texas bbox
W, S, E, N = -106.65, 25.84, -93.51, 36.50
STEP = 1.0      # 1-degree tiles -> small per-query result sets
PAGE = 1000


def _query(bbox: str, offset: int) -> dict:
    params = {
        "where": "ZONE_SUBTY='FLOODWAY'",
        "geometry": bbox,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326", "outSR": "4326",
        "outFields": "FLD_ZONE,ZONE_SUBTY,DFIRM_ID",
        "returnGeometry": "true",
        "resultOffset": offset, "resultRecordCount": PAGE,
        "f": "geojson",
    }
    for attempt in range(4):
        try:
            r = requests.get(LAYER, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))  # backoff on transient 500s
    return {"features": []}


def main() -> None:
    print("FEMA floodways (TX) — tiled ArcGIS REST pull")
    seen: set = set()
    feats: list = []
    lat = S
    while lat < N:
        lon = W
        while lon < E:
            bbox = f"{lon},{lat},{min(lon+STEP, E)},{min(lat+STEP, N)}"
            offset = 0
            while True:
                js = _query(bbox, offset)
                page = js.get("features", [])
                new = 0
                for ft in page:
                    fid = ft.get("id") or id(ft)
                    if fid not in seen:
                        seen.add(fid)
                        feats.append(ft)
                        new += 1
                if new:
                    print(f"  tile {bbox}: +{new} (total {len(feats)})")
                if len(page) < PAGE:
                    break
                offset += PAGE
            lon += STEP
        lat += STEP

    if not feats:
        print("  no floodways returned")
        return

    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf.columns = [c.lower() for c in gdf.columns]
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    gdf = gdf.rename_geometry("geom")
    gdf.to_postgis("floodways", engine(), if_exists="replace", index=False)
    run_sql("CREATE INDEX IF NOT EXISTS floodways_geom_idx ON floodways USING GIST (geom);")
    print(f"  floodways rows: {table_count('floodways')}")


if __name__ == "__main__":
    main()
