"""Shared ingestion helpers: DB engine, psql/ogr2ogr wrappers, download util."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests
from sqlalchemy import create_engine, text
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATABASE_URL, DATA_RAW, ogr_pg_conn  # noqa: E402


def engine():
    return create_engine(DATABASE_URL)


def run_sql(sql: str) -> None:
    """Execute a SQL statement (DDL/DML) against the DB."""
    with engine().begin() as conn:
        conn.execute(text(sql))


def query(sql: str):
    with engine().connect() as conn:
        return conn.execute(text(sql)).fetchall()


# Many gov sites (EIA, etc.) 503/403 non-browser agents — present a real UA.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def download(url: str, dest_name: str, chunk: int = 1 << 20) -> Path:
    """Stream a URL to data/raw/<dest_name>, skip if already present."""
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    dest = DATA_RAW / dest_name
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ cached {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest
    print(f"  ↓ {url}")
    with requests.get(url, stream=True, timeout=300, headers=_BROWSER_HEADERS) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=f"  {dest.name}"
        ) as bar:
            for part in r.iter_content(chunk_size=chunk):
                f.write(part)
                bar.update(len(part))
    return dest


def arcgis_features(
    layer_url: str,
    *,
    where: str = "1=1",
    bbox: tuple | None = None,
    tile_step: float | None = None,
    page: int = 1000,
    out_fields: str = "*",
):
    """Pull an ArcGIS REST FeatureServer/MapServer layer into a GeoDataFrame (EPSG:4326).

    bbox=(W,S,E,N) limits by geometry; tile_step tiles the bbox into cells (avoids
    server 500/timeout on big layers). Paginates each cell and dedups by feature id.
    """
    import time

    import geopandas as gpd
    import requests

    q = layer_url.rstrip("/") + "/query"

    def _fetch(geom, offset):
        params = {
            "where": where, "outFields": out_fields, "returnGeometry": "true",
            "outSR": "4326", "resultOffset": offset, "resultRecordCount": page, "f": "geojson",
        }
        if geom:
            params |= {"geometry": geom, "geometryType": "esriGeometryEnvelope",
                       "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"}
        for attempt in range(4):
            try:
                r = requests.get(q, params=params, timeout=120, headers=_BROWSER_HEADERS)
                if r.status_code == 200:
                    return r.json()
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        return {"features": []}

    if bbox and tile_step:
        W, S, E, N = bbox
        boxes, lat = [], S
        while lat < N:
            lon = W
            while lon < E:
                boxes.append(f"{lon},{lat},{min(lon+tile_step, E)},{min(lat+tile_step, N)}")
                lon += tile_step
            lat += tile_step
    elif bbox:
        boxes = [f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"]
    else:
        boxes = [None]

    seen, feats = set(), []
    for gm in boxes:
        offset = 0
        while True:
            js = _fetch(gm, offset)
            pg = js.get("features", [])
            for ft in pg:
                fid = ft.get("id") or id(ft)
                if fid not in seen:
                    seen.add(fid)
                    feats.append(ft)
            if len(pg) < page:
                break
            offset += page
        if boxes != [None]:
            print(f"  ... {len(feats)} features so far")
    if not feats:
        return gpd.GeoDataFrame()
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf.columns = [c.lower() for c in gdf.columns]
    return gdf


def write_gdf(gdf, table: str, *, clip_geom=None, make_multi: bool = True) -> int:
    """Clean a GeoDataFrame (validity, optional clip, optional promote-to-multi) and
    write it to PostGIS as EPSG:4326 with a 'geom' column + GIST index."""
    import geopandas as gpd
    from shapely import make_valid
    from shapely.geometry import MultiLineString, MultiPolygon

    gdf = gdf.to_crs("EPSG:4326")
    gdf.columns = [c.lower() for c in gdf.columns]  # predictable SQL (Postgres folds case)
    # Repair validity BEFORE clipping (clip's intersection fails on invalid input).
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    if clip_geom is not None:
        gdf = gpd.clip(gdf, make_valid(clip_geom))
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
        gdf["geometry"] = gdf.geometry.apply(make_valid)
    if make_multi:
        def _multi(g):
            if g.geom_type == "Polygon":
                return MultiPolygon([g])
            if g.geom_type == "LineString":
                return MultiLineString([g])
            return g
        gdf["geometry"] = gdf.geometry.apply(_multi)
    if gdf.geometry.name != "geom":
        gdf = gdf.rename_geometry("geom")
    gdf.to_postgis(table, engine(), if_exists="replace", index=False)
    run_sql(f"CREATE INDEX IF NOT EXISTS {table}_geom_idx ON {table} USING GIST (geom);")
    return len(gdf)


def get_pilot_boundary():
    """Return the dissolved scope boundary (shapely geom in EPSG:4326) from the DB,
    or None if not built yet."""
    import geopandas as gpd

    try:
        gdf = gpd.read_postgis(
            "SELECT geom FROM pilot_boundary", engine(), geom_col="geom"
        )
        return gdf.geometry.iloc[0] if len(gdf) else None
    except Exception:
        return None


def ogr_clip_to_gpkg(src: str, out_name: str, clip_path: str) -> Path:
    """Use ogr2ogr to clip a (large) vector to a GeoPackage — ogr2ogr CAN write
    GPKG (no PG driver needed). Returns the clipped file path. Used to shrink huge
    national/state files before loading into PostGIS via geopandas."""
    out = DATA_RAW / out_name
    if out.exists():
        out.unlink()
    cmd = [
        "ogr2ogr", "-f", "GPKG", str(out), src,
        "-t_srs", "EPSG:4326", "-clipsrc", clip_path,
        "-nlt", "PROMOTE_TO_MULTI", "-skipfailures",
    ]
    print(f"  ✂  clipping {Path(src).name} -> {out.name}")
    subprocess.run(cmd, check=True)
    return out


def load_vector(
    src: str,
    table: str,
    *,
    where: str | None = None,
    columns: list[str] | None = None,
    bbox=None,
    clip_geom=None,
    make_multi: bool = True,
) -> int:
    """Load a vector source into PostGIS as EPSG:4326 with a 'geom' column + GIST index.

    Uses geopandas/pyogrio (robust; avoids the conda GDAL PG-driver version mismatch).
    where:     OGR-SQL attribute filter pushed down on read (e.g. "STATEFP = '48'").
    bbox:      (minx, miny, maxx, maxy) in source CRS to limit read (memory saver).
    clip_geom: shapely geom (EPSG:4326) for a precise post-read clip.
    """
    import geopandas as gpd
    from shapely import make_valid

    print(f"  → reading {Path(src).name}")
    gdf = gpd.read_file(src, where=where, columns=columns, bbox=bbox, engine="pyogrio")
    # Lowercase attribute names so downstream SQL is predictable (Postgres folds case).
    gdf.columns = [c.lower() for c in gdf.columns]
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")

    if clip_geom is not None:
        gdf = gpd.clip(gdf, clip_geom)

    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    if make_multi:
        from shapely.geometry import MultiLineString, MultiPolygon
        def _to_multi(g):
            if g.geom_type == "Polygon":
                return MultiPolygon([g])
            if g.geom_type == "LineString":
                return MultiLineString([g])
            return g
        gdf["geometry"] = gdf.geometry.apply(_to_multi)

    gdf = gdf.rename_geometry("geom")
    print(f"  → writing {len(gdf)} rows to {table}")
    gdf.to_postgis(table, engine(), if_exists="replace", index=False)
    run_sql(f"CREATE INDEX IF NOT EXISTS {table}_geom_idx ON {table} USING GIST (geom);")
    return len(gdf)


def table_count(table: str) -> int:
    try:
        return query(f"SELECT count(*) FROM {table}")[0][0]
    except Exception:
        return -1
