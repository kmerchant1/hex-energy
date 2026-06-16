"""P0 raster-feature verification: sample slope (and NLCD) at sample-county points.

Complements db/verify_p0.sql (which covers the vector layers). Confirms the file-based
raster features sample correctly and return sane values.

Usage: python ingestion/verify_rasters.py [County]
"""
from __future__ import annotations

import sys
from pathlib import Path

import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import ROOT  # noqa: E402
from ingestion._db import query  # noqa: E402

SLOPE = ROOT / "data" / "processed" / "tx_slope.tif"
NLCD = ROOT / "data" / "processed" / "tx_nlcd.tif"  # created when NLCD is processed


def sample(raster_path: Path, pts):
    """Return point-sampled values from a raster for [(name, lon, lat), ...]."""
    if not raster_path.exists():
        return None
    with rasterio.open(raster_path) as src:
        coords = [(lon, lat) for _, lon, lat in pts]
        vals = [v[0] for v in src.sample(coords)]
    return vals


def main() -> None:
    county = sys.argv[1] if len(sys.argv) > 1 else "Pecos"
    rows = query(
        f"""
        SELECT p.name, ST_X(p.geom) AS lon, ST_Y(p.geom) AS lat
        FROM eia860_plants p
        JOIN tx_counties c ON ST_Contains(c.geom, p.geom)
        WHERE c.name = '{county}'
        LIMIT 8
        """
    )
    pts = [(r[0], r[1], r[2]) for r in rows]
    if not pts:
        print(f"No sample points found in {county}.")
        return

    slope_vals = sample(SLOPE, pts)
    nlcd_vals = sample(NLCD, pts)

    print(f"\n=== RASTER FEATURE CHECK — {county} ({len(pts)} points) ===")
    print(f"{'plant':28} {'slope_%':>8} {'nlcd_class':>11}")
    print("-" * 50)
    for i, (name, lon, lat) in enumerate(pts):
        s = f"{slope_vals[i]:.2f}" if slope_vals else "n/a"
        n = f"{int(nlcd_vals[i])}" if nlcd_vals else "n/a (not loaded)"
        print(f"{name[:28]:28} {s:>8} {n:>11}")

    print("\nPASS if slope_% values are sane (TX mean ~6%, flat sites <10%).")
    if not nlcd_vals:
        print("NLCD not yet processed to data/processed/tx_nlcd.tif — slope alone is fine for P0.")


if __name__ == "__main__":
    main()
