"""Per-site suitability engine — the core of the (pivoted) product.

Scores ANY location with location-driven, transparent logic (no ML success model):
  1. Fatal-flaw vetoes (deterministic): wetland / floodway / protected land / steep slope.
  2. Grid-cost-risk: distance to nearest line of sufficient voltage -> gen-tie $ estimate,
     HV-backbone access (dist to 345 kV), congestion proxy (county queued MW).
  3. A 0-100 suitability score + human-readable reason drivers.

Honest scope: captures the connection/gen-tie cost (the siting-controllable part) + a
saturation proxy — NOT the full network-upgrade cost (needs study data we don't have).

Used by the heatmap (batch) and the per-parcel report (single). Cost rates are rough
industry figures, clearly calibratable.

Usage (verify): python scoring/site_score.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_PROCESSED  # noqa: E402
from ingestion._db import query  # noqa: E402

SLOPE_TIF = str(DATA_PROCESSED / "tx_slope.tif")
NLCD_TIF = str(DATA_PROCESSED / "tx_nlcd.tif")

# Tunable thresholds / rates (illustrative — calibrate with real data later)
SLOPE_FATAL_PCT = 15.0
VETO_BUFFER_M = 50.0
DEVELOPABLE_NLCD = {31, 52, 71, 81, 82}
GENTIE_RATE = {69: 1.0, 138: 1.5, 230: 2.0, 345: 2.8}  # $M per mile by connect voltage
BASE_INTERCONNECT_M = 2.0  # $M fixed interconnection facilities (rough)
ACCESS_DECAY_MI = 5.0      # grid-access score e-folding distance


def suitable_kv(mw: float | None) -> int:
    """Voltage a project of this size realistically needs to connect at."""
    if mw is None or mw < 20:
        return 69
    if mw < 150:
        return 138
    return 345


def _grid_sql(lon: float, lat: float, suit_kv: int) -> dict:
    rows = query(f"""
        WITH pt AS (
            SELECT g AS geom, ST_Transform(g, 3083) AS g3,
                   -- {VETO_BUFFER_M} m buffer back in 4326 so vetoes use the GIST index
                   ST_Transform(ST_Buffer(ST_Transform(g, 3083), {VETO_BUFFER_M}), 4326) AS buf
            FROM (SELECT ST_SetSRID(ST_MakePoint({lon:.6f}, {lat:.6f}), 4326) AS g) s
        )
        SELECT
          c.name AS county, c.fips,
          nl.mi AS dist_line_mi, nl.kv AS nearest_line_kv,
          nsuit.mi AS dist_suitable_mi,
          n345.mi AS dist_345_mi,
          ns.mi AS dist_sub_mi, ns.kv AS sub_kv,
          cf.queued_mw,
          wet.f AS wetland, fld.f AS floodway, prt.f AS protected
        FROM pt
        LEFT JOIN tx_counties c ON ST_Contains(c.geom, pt.geom)
        LEFT JOIN county_features cf ON cf.fips = c.fips
        LEFT JOIN LATERAL (SELECT ST_Distance(pt.g3, ST_Transform(t.geom,3083))/1609.34 mi, t.voltage kv
                           FROM transmission_lines t ORDER BY t.geom <-> pt.geom LIMIT 1) nl ON true
        LEFT JOIN LATERAL (SELECT ST_Distance(pt.g3, ST_Transform(t.geom,3083))/1609.34 mi
                           FROM transmission_lines t WHERE t.voltage >= {suit_kv}
                           ORDER BY t.geom <-> pt.geom LIMIT 1) nsuit ON true
        LEFT JOIN LATERAL (SELECT ST_Distance(pt.g3, ST_Transform(t.geom,3083))/1609.34 mi
                           FROM transmission_lines t WHERE t.voltage >= 345
                           ORDER BY t.geom <-> pt.geom LIMIT 1) n345 ON true
        LEFT JOIN LATERAL (SELECT ST_Distance(pt.g3, ST_Transform(s.geom,3083))/1609.34 mi, s.max_volt kv
                           FROM substations s ORDER BY s.geom <-> pt.geom LIMIT 1) ns ON true
        LEFT JOIN LATERAL (SELECT EXISTS(SELECT 1 FROM wetlands w
                           WHERE ST_Intersects(w.geom, pt.buf)) f) wet ON true
        LEFT JOIN LATERAL (SELECT EXISTS(SELECT 1 FROM floodways f
                           WHERE ST_Intersects(f.geom, pt.buf)) f) fld ON true
        LEFT JOIN LATERAL (SELECT EXISTS(SELECT 1 FROM protected_areas p
                           WHERE p.gap_sts IN ('1','2') AND ST_Intersects(p.geom, pt.buf)) f) prt ON true
    """)
    keys = ["county", "fips", "dist_line_mi", "nearest_line_kv", "dist_suitable_mi",
            "dist_345_mi", "dist_sub_mi", "sub_kv", "queued_mw", "wetland", "floodway", "protected"]
    return dict(zip(keys, rows[0])) if rows else {}


def _sample_raster(path: str, lon: float, lat: float):
    with rasterio.open(path) as src:
        for v in src.sample([(lon, lat)]):
            return v[0]
    return None


def score_site(lon: float, lat: float, mw: float = 100.0) -> dict:
    suit_kv = suitable_kv(mw)
    g = _grid_sql(lon, lat, suit_kv)
    if not g.get("county"):
        return {"error": "outside Texas"}

    slope = float(_sample_raster(SLOPE_TIF, lon, lat) or 0.0)
    nlcd = int(_sample_raster(NLCD_TIF, lon, lat) or 0)
    developable = nlcd in DEVELOPABLE_NLCD

    # --- fatal-flaw vetoes ---
    fatal_reasons = []
    if g["wetland"]:
        fatal_reasons.append("mapped wetland on/adjacent to site")
    if g["floodway"]:
        fatal_reasons.append("FEMA floodway on/adjacent to site")
    if g["protected"]:
        fatal_reasons.append("protected/conservation land (GAP 1/2)")
    if slope > SLOPE_FATAL_PCT:
        fatal_reasons.append(f"slope {slope:.0f}% exceeds {SLOPE_FATAL_PCT:.0f}%")
    fatal = bool(fatal_reasons)

    # --- gen-tie cost estimate ($M) ---
    dist_suit = g["dist_suitable_mi"] if g["dist_suitable_mi"] is not None else g["dist_line_mi"]
    gentie_cost_m = round(dist_suit * GENTIE_RATE[suit_kv] + BASE_INTERCONNECT_M, 1)

    # --- transparent 0-100 grid-access score ---
    access = 100.0 * math.exp(-dist_suit / ACCESS_DECAY_MI)
    qmw = g["queued_mw"] or 0
    congestion_penalty = min(qmw / 20000.0, 1.0) * 15.0  # busy areas -> upgrade risk
    if not developable:
        access -= 10.0
    score = max(0.0, min(100.0, access - congestion_penalty))

    # --- reason drivers ---
    drivers = [
        f"{dist_suit:.1f} mi to nearest {suit_kv} kV line (gen-tie ~${gentie_cost_m}M)",
        f"{g['dist_345_mi']:.1f} mi to 345 kV backbone",
        f"{g['dist_sub_mi']:.1f} mi to nearest substation",
    ]
    if congestion_penalty > 3:
        drivers.append(f"high local queue saturation ({qmw:.0f} MW)")
    if not developable:
        drivers.append("land cover not ideal (forest/developed/water)")

    return {
        "county": g["county"], "mw": mw, "suitable_kv": suit_kv,
        "fatal": fatal, "fatal_reasons": fatal_reasons,
        "score": round(score, 1),
        "gentie_cost_m": gentie_cost_m,
        "dist_suitable_mi": round(dist_suit, 2),
        "dist_345_mi": round(g["dist_345_mi"], 2) if g["dist_345_mi"] else None,
        "dist_substation_mi": round(g["dist_sub_mi"], 2),
        "slope_pct": round(slope, 1), "developable": developable,
        "queued_mw": round(qmw),
        "drivers": drivers,
    }


SAMPLES = [
    ("Limestone plant (on the grid)", -96.253, 31.422, 150),
    ("West Texas, remote from HV", -103.5, 31.0, 200),
    ("Gulf Coast marsh (Chambers)", -94.55, 29.72, 100),
    ("Travis/Hill Country", -98.05, 30.30, 100),
]


def main() -> None:
    for label, lon, lat, mw in SAMPLES:
        r = score_site(lon, lat, mw)
        print(f"\n### {label}  ({lon},{lat}, {mw} MW)")
        if r.get("error"):
            print(f"   {r['error']}"); continue
        verdict = "🔴 FATAL FLAW" if r["fatal"] else f"score {r['score']}/100"
        print(f"   {r['county']} County — {verdict}   gen-tie ~${r['gentie_cost_m']}M")
        if r["fatal"]:
            print(f"   fatal: {', '.join(r['fatal_reasons'])}")
        for d in r["drivers"]:
            print(f"     • {d}")


if __name__ == "__main__":
    main()
