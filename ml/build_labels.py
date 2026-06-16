"""P1 Step 1 — Build the training-label table from the interconnection queue.

Each historical Texas project becomes a labeled row:
  operational -> 1 (got built)   |   withdrawn -> 0 (died)
Active/suspended/unknown are dropped (outcome not yet known — censored).

Geolocation is two-resolution:
  - operational: best-effort fuzzy match to eia860_plants (county + name) -> point geom
  - withdrawn:   county (fips_code) only — no coordinates exist
Coordinates are a BONUS here (training uses county-level features per the anti-leakage
design); the match rate is a verification metric, not a gate.

Creates:
  labels(project_id, q_id, label, q_status, fips_code, county, region, type_clean,
         mw, q_year, eia_plant_code, latitude, longitude, geom[Point,4326] NULL)

Usage: python ml/build_labels.py
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion._db import engine, query, run_sql  # noqa: E402

NAME_SIM_THRESHOLD = 0.6
# Common words that inflate similarity ("Corazon Solar" vs "Mars Solar") — strip
# before matching so we key on the distinctive part of the name.
_STOP = {
    "solar", "wind", "energy", "farm", "project", "projects", "llc", "lp", "lc",
    "storage", "bess", "generation", "power", "plant", "units", "unit", "gas",
    "the", "of", "center", "park", "ranch", "facility", "station", "i", "ii", "iii",
    "1", "2", "3", "4", "battery", "repower", "hybrid",
}


def _norm(s) -> str:
    return str(s).strip().lower() if s is not None else ""


def _name_key(s) -> str:
    """Distinctive part of a plant name (common energy words removed)."""
    raw = _norm(s).replace(",", " ").replace("-", " ").replace("/", " ")
    return " ".join(w for w in raw.split() if w not in _STOP)


_VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*kv", re.IGNORECASE)


def _poi_voltage(s) -> float | None:
    """Parse the grid-connection voltage (kV) embedded in a POI name, e.g.
    'Crane Switch 138kV' -> 138. Leakage-free site-level signal (both classes)."""
    if not s:
        return None
    vals = [float(v) for v in _VOLT_RE.findall(str(s))]
    return max(vals) if vals else None


def main() -> None:
    eng = engine()

    # 1. Pull labeled Texas projects (operational=1, withdrawn=0).
    # Filter on fips_code LIKE '48%' (TX counties) — the reliable "in Texas + has a
    # usable county" signal. The `state` column is sparsely populated; ~844 ERCOT
    # rows have NO location at all (blank state/fips/county) and are unusable for
    # county-level training, so we exclude them here. `region` is kept as a feature.
    q = pd.read_sql(
        """
        SELECT q_id, q_status, county, fips_code, project_name, poi_name, type_clean,
               mw_1 AS mw, q_year, region
        FROM queue_lbnl
        WHERE LEFT(fips_code, 2) = '48' AND q_status IN ('operational', 'withdrawn')
        """,
        eng,
    )
    q["poi_voltage_kv"] = q["poi_name"].map(_poi_voltage)
    q["label"] = (q["q_status"] == "operational").astype(int)
    q = q.reset_index(drop=True)
    q["project_id"] = q.index + 1  # surrogate key (q_id has dupes / 'not assigned')

    # 2. EIA plants indexed by normalized county for the operational geolocation
    eia = pd.read_sql(
        "SELECT plant_code, name, county, latitude, longitude FROM eia860_plants", eng
    )
    eia["cty"] = eia["county"].map(_norm)
    by_county: dict[str, list] = {}
    for _, r in eia.iterrows():
        by_county.setdefault(r["cty"], []).append(r)

    # 3. Geolocate operational projects (best-effort name match within county)
    plant_code = [None] * len(q)
    lat = [None] * len(q)
    lon = [None] * len(q)
    for i, row in q.iterrows():
        if row["label"] != 1:
            continue
        cands = by_county.get(_norm(row["county"]), [])
        if not cands:
            continue
        pkey = _name_key(row["project_name"])
        best, best_score = None, 0.0
        if pkey:  # only name-match when the queue row has a distinctive name
            for c in cands:
                ckey = _name_key(c["name"])
                if not ckey:
                    continue
                score = SequenceMatcher(None, pkey, ckey).ratio()
                if score > best_score:
                    best, best_score = c, score
        if best is not None and best_score >= NAME_SIM_THRESHOLD:
            plant_code[i] = int(best["plant_code"])
            lat[i] = float(best["latitude"])
            lon[i] = float(best["longitude"])

    q["eia_plant_code"] = plant_code
    q["latitude"] = lat
    q["longitude"] = lon
    q["mw"] = pd.to_numeric(q["mw"], errors="coerce")
    q["q_year"] = pd.to_numeric(q["q_year"], errors="coerce").astype("Int64")

    out = q[[
        "project_id", "q_id", "label", "q_status", "fips_code", "county",
        "project_name", "poi_name", "poi_voltage_kv", "region", "type_clean",
        "mw", "q_year", "eia_plant_code", "latitude", "longitude",
    ]]
    out.to_sql("labels", eng, if_exists="replace", index=False)

    # 4. Add a point geom where coordinates exist
    run_sql("ALTER TABLE labels ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);")
    run_sql(
        """
        UPDATE labels
        SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        WHERE longitude IS NOT NULL AND latitude IS NOT NULL;
        """
    )
    run_sql("CREATE INDEX IF NOT EXISTS labels_geom_idx ON labels USING GIST (geom);")
    run_sql("CREATE INDEX IF NOT EXISTS labels_fips_idx ON labels (fips_code);")

    # 5. Summary
    n = len(out)
    n_op = int((out["label"] == 1).sum())
    n_wd = int((out["label"] == 0).sum())
    n_geo = int(out["latitude"].notna().sum())
    print(f"labels rows: {n}  (operational={n_op}, withdrawn={n_wd})")
    print(f"operational geolocated to EIA: {n_geo}/{n_op} "
          f"({100*n_geo/max(n_op,1):.0f}%)")
    print("by region:")
    for region, lab, cnt in query(
        "SELECT region, label, count(*) FROM labels GROUP BY region, label ORDER BY region, label"
    ):
        print(f"  {region or '(none)':8} label={lab}  {cnt}")


if __name__ == "__main__":
    main()
