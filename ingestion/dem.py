"""Download USGS 3DEP 1-arc-second (~30 m) DEM tiles covering Texas from USGS S3.

The National Map UI requires adding ~130 tiles to a cart by hand; this enumerates
every 1°x1° tile intersecting the Texas bounding box and pulls them directly from
the public USGS S3 bucket. Tiles are named by their NW corner: n{lat}w{lon}.

Output: data/raw/dem/USGS_1_n{lat}w{lon}.tif  (slope is derived later in P0 step 4)

Usage: python ingestion/dem.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_RAW  # noqa: E402

BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current"

# Texas bounding box (with a small buffer); tiles named by NW corner.
LAT_NORTH_EDGES = range(26, 38)   # n26 .. n37  (covers ~25-37 N)
LON_WEST_EDGES = range(94, 108)   # w094 .. w107 (covers ~93-107 W)


def main() -> None:
    out_dir = DATA_RAW / "dem"
    out_dir.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()

    total_bytes = 0
    got = skipped = missing = 0
    for lat in LAT_NORTH_EDGES:
        for lon in LON_WEST_EDGES:
            tile = f"n{lat:02d}w{lon:03d}"
            url = f"{BASE}/{tile}/USGS_1_{tile}.tif"
            dest = out_dir / f"USGS_1_{tile}.tif"
            if dest.exists() and dest.stat().st_size > 0:
                skipped += 1
                total_bytes += dest.stat().st_size
                continue
            head = sess.head(url, timeout=30)
            if head.status_code != 200:
                missing += 1
                continue
            size = int(head.headers.get("content-length", 0))
            print(f"  ↓ {tile}  ({size/1e6:.0f} MB)")
            with sess.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            got += 1
            total_bytes += dest.stat().st_size

    print(
        f"\nDEM tiles: {got} downloaded, {skipped} cached, {missing} not present "
        f"(ocean/edge). Total on disk: {total_bytes/1e9:.2f} GB in {out_dir}"
    )


if __name__ == "__main__":
    main()
