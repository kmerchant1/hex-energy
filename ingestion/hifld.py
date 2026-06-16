"""Load electric transmission lines + substations for Texas into PostGIS.

Grid-proximity features: distance to nearest line (by voltage class) and distance to
nearest substation (+ a headroom proxy from voltage). Transmission lines come from the
public HIFLD ArcGIS mirror; substations from OpenStreetMap via Overpass (the public
HIFLD substation mirrors are gated/stubbed, so OSM is the authoritative free source).

Creates:
  transmission_lines — TX lines (geom MultiLineString 4326, voltage, volt_class)
  substations        — TX substations (geom Point 4326, max_volt, name)

Usage: python ingestion/hifld.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion._db import (  # noqa: E402
    arcgis_features, get_pilot_boundary, table_count, write_gdf,
)

TX_BBOX = (-106.65, 25.84, -93.51, 36.50)

LINES_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
    "US_Electric_Power_Transmission_Lines/FeatureServer/0"
)
OVERPASS = "https://overpass-api.de/api/interpreter"


def _parse_voltage(v) -> float | None:
    """OSM voltage tags can be '138000' or '138000;69000' — return the max in kV."""
    if not v:
        return None
    vals = []
    for part in str(v).replace(",", ";").split(";"):
        part = part.strip()
        if part.isdigit():
            vals.append(int(part) / 1000.0)  # volts -> kV
    return max(vals) if vals else None


def _osm_substations(boundary):
    W, S, E, N = TX_BBOX
    ql = (
        f"[out:json][timeout:180];"
        f'(node["power"="substation"]({S},{W},{N},{E});'
        f'way["power"="substation"]({S},{W},{N},{E}););out center tags;'
    )
    print("OSM substations (Overpass)")
    r = requests.get(OVERPASS, params={"data": ql}, timeout=240,
                     headers={"User-Agent": "hex-energy/1.0"})
    r.raise_for_status()
    els = r.json().get("elements", [])
    rows = []
    for el in els:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": el.get("id"),
            "name": tags.get("name"),
            "max_volt": _parse_voltage(tags.get("voltage")),
            "longitude": lon, "latitude": lat,
        })
    gdf = gpd.GeoDataFrame(
        rows, geometry=gpd.points_from_xy([r["longitude"] for r in rows],
                                          [r["latitude"] for r in rows]),
        crs="EPSG:4326",
    )
    return write_gdf(gdf, "substations", clip_geom=boundary, make_multi=False)


def main() -> None:
    boundary = get_pilot_boundary()

    print("Transmission lines (HIFLD, tiled REST pull)")
    lines = arcgis_features(
        LINES_URL, bbox=TX_BBOX, tile_step=2.0,
        out_fields="ID,VOLTAGE,VOLT_CLASS,OWNER,STATUS,SUB_1,SUB_2",
    )
    n = write_gdf(lines, "transmission_lines", clip_geom=boundary)
    print(f"  transmission_lines rows: {n}")

    n = _osm_substations(boundary)
    print(f"  substations rows: {n}")

    print(f"  (verify) lines={table_count('transmission_lines')} subs={table_count('substations')}")


if __name__ == "__main__":
    main()
