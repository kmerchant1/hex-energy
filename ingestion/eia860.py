"""Download EIA-860 and load Texas plant locations (lat/long) into PostGIS.

EIA-860 is the bridge that gives ENERGIZED projects precise coordinates for the
two-resolution label join (withdrawn projects only have county).

Creates:
  eia860_plants — TX plants: plant_code, name, county, technology, status, geom(Point,4326)

Usage: python ingestion/eia860.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_RAW  # noqa: E402
from ingestion._db import download, engine, run_sql, table_count  # noqa: E402

EIA_URL = "https://www.eia.gov/electricity/data/eia860/xls/eia8602024.zip"


def main() -> None:
    print("EIA-860 plants")
    zip_path = download(EIA_URL, "eia8602024.zip")

    extract_dir = DATA_RAW / "eia860_2024"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)

    # The plant-level workbook is named like "2___Plant_Y2024.xlsx"
    plant_files = list(extract_dir.glob("*Plant*.xlsx")) + list(extract_dir.glob("*Plant*.xls"))
    if not plant_files:
        raise FileNotFoundError(f"No *Plant* workbook in {extract_dir}: {list(extract_dir.iterdir())}")
    plant_file = plant_files[0]
    print(f"  reading {plant_file.name}")

    # EIA sheets have a 1-row title banner above the header.
    df = pd.read_excel(plant_file, skiprows=1)
    df.columns = [str(c).strip() for c in df.columns]

    # Normalize the columns we need (names are stable but be defensive).
    colmap = {c.lower(): c for c in df.columns}
    def col(*cands):
        for c in cands:
            if c.lower() in colmap:
                return colmap[c.lower()]
        raise KeyError(f"none of {cands} in {list(df.columns)}")

    lat, lon = col("Latitude"), col("Longitude")
    state = col("State")
    df = df[df[state].astype(str).str.upper() == "TX"].copy()

    keep = {
        col("Plant Code"): "plant_code",
        col("Plant Name"): "name",
        col("County"): "county",
        lat: "latitude",
        lon: "longitude",
    }
    df = df.rename(columns=keep)[list(keep.values())]

    # EIA leaves some coordinates blank (' ') — coerce to numeric and drop those.
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    if before != len(df):
        print(f"  dropped {before - len(df)} plants with missing coordinates")

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    ).rename_geometry("geom")
    gdf.to_postgis("eia860_plants", engine(), if_exists="replace", index=False)
    run_sql("CREATE INDEX IF NOT EXISTS eia860_plants_geom_idx ON eia860_plants USING GIST (geom);")

    print(f"  eia860_plants rows (TX): {table_count('eia860_plants')}")


if __name__ == "__main__":
    main()
