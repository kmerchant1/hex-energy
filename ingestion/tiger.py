"""Load Census TIGER county boundaries for Texas, and derive the pilot boundary.

Creates:
  tx_counties     — all 254 TX counties (geom, name, fips)
  pilot_boundary  — dissolved union of PILOT_COUNTIES (the clip mask for everything else)

Usage: python ingestion/tiger.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_RAW, PILOT_COUNTIES, STATEWIDE, TX_STATE_FIPS  # noqa: E402
from ingestion._db import download, load_vector, run_sql, table_count  # noqa: E402

# National county file; we filter to Texas (STATEFP = 48) on load.
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip"


def main() -> None:
    print("TIGER counties")
    zip_path = download(TIGER_URL, "tl_2024_us_county.zip")

    # Unzip into a stable folder
    extract_dir = DATA_RAW / "tiger_county"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    shp = next(extract_dir.glob("*.shp"))

    # Load all Texas counties
    load_vector(str(shp), "tx_counties", where=f"STATEFP = '{TX_STATE_FIPS}'")

    # Normalize useful columns
    run_sql(
        """
        ALTER TABLE tx_counties ADD COLUMN IF NOT EXISTS fips text;
        UPDATE tx_counties SET fips = statefp || countyfp;
        """
    )

    # Build the dissolved boundary used to clip every other dataset.
    # STATEWIDE -> all TX counties; otherwise -> the PILOT_COUNTIES subset.
    if STATEWIDE:
        where = f"statefp = '{TX_STATE_FIPS}'"
        scope_desc = "STATEWIDE (all TX counties)"
    else:
        fips_list = ", ".join(f"'{f}'" for f in PILOT_COUNTIES.values())
        where = f"fips IN ({fips_list})"
        scope_desc = f"counties {list(PILOT_COUNTIES)}"

    run_sql("DROP TABLE IF EXISTS pilot_boundary;")
    run_sql(
        f"""
        CREATE TABLE pilot_boundary AS
        SELECT ST_Union(geom)::geometry(MultiPolygon, 4326) AS geom
        FROM tx_counties
        WHERE {where};
        CREATE INDEX ON pilot_boundary USING GIST (geom);
        """
    )

    print(f"  tx_counties rows: {table_count('tx_counties')}")
    print(f"  scope: {scope_desc} -> {table_count('pilot_boundary')} dissolved boundary row")


if __name__ == "__main__":
    main()
