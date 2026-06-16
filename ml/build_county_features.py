"""P1 Step 2 — Build the county_features table (county-level aggregates).

Per the locked anti-leakage design, training features are computed at COUNTY
granularity for all projects (joined to labels by fips). These are true county
aggregates — densities, area fractions, terrain means — that faithfully describe
each county (not noisy single points).

Vector features come from PostGIS (distance/area math in EPSG:3083); slope and
land-cover come from the raster files via rasterstats zonal statistics.

Creates:
  county_features(fips, county_name, area_km2,
    dist_line_mi, dist_hv_line_mi, dist_substation_mi,         -- centroid -> nearest
    line_km_per_1000km2, substation_count, substation_per_1000km2, max_substation_kv,
    queued_mw,                                                  -- saturation proxy
    wetland_pct, floodway_pct, protected_pct,                   -- land constraints
    mean_slope_pct, pct_steep, developable_frac)                -- terrain / land cover

Usage: python ml/build_county_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterstats import zonal_stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_PROCESSED  # noqa: E402
from ingestion._db import engine, query, run_sql  # noqa: E402

SLOPE_TIF = str(DATA_PROCESSED / "tx_slope.tif")
NLCD_TIF = str(DATA_PROCESSED / "tx_nlcd.tif")
ACRE_TO_KM2 = 0.00404686
DEVELOPABLE_NLCD = {31, 52, 71, 81, 82}  # barren, shrub, grassland, pasture, crops
STEEP_PCT = 15.0


def build_vector_features() -> None:
    print("county_features: base table + vector aggregates")
    run_sql(
        """
        DROP TABLE IF EXISTS county_features;
        CREATE TABLE county_features AS
        SELECT fips,
               name AS county_name,
               ST_Area(ST_Transform(geom, 3083)) / 1e6 AS area_km2,
               ST_Centroid(geom) AS centroid
        FROM tx_counties;
        ALTER TABLE county_features ADD PRIMARY KEY (fips);
        CREATE INDEX cf_centroid_idx ON county_features USING GIST (centroid);
        """
    )

    # Centroid -> nearest transmission line (any + HV>=230kV) and substation (miles)
    print("  centroid distances (line / hv line / substation)")
    run_sql(
        """
        ALTER TABLE county_features
            ADD COLUMN dist_line_mi double precision,
            ADD COLUMN dist_hv_line_mi double precision,
            ADD COLUMN dist_substation_mi double precision;
        UPDATE county_features cf SET
          dist_line_mi = (SELECT ST_Distance(ST_Transform(cf.centroid,3083), ST_Transform(t.geom,3083))/1609.34
                          FROM transmission_lines t ORDER BY t.geom <-> cf.centroid LIMIT 1),
          dist_hv_line_mi = (SELECT ST_Distance(ST_Transform(cf.centroid,3083), ST_Transform(t.geom,3083))/1609.34
                          FROM transmission_lines t WHERE t.voltage >= 230 ORDER BY t.geom <-> cf.centroid LIMIT 1),
          dist_substation_mi = (SELECT ST_Distance(ST_Transform(cf.centroid,3083), ST_Transform(s.geom,3083))/1609.34
                          FROM substations s ORDER BY s.geom <-> cf.centroid LIMIT 1);
        """
    )

    # Transmission line density (km of line within county per 1000 km2)
    print("  line density")
    run_sql(
        """
        ALTER TABLE county_features ADD COLUMN line_km_per_1000km2 double precision;
        WITH ld AS (
          SELECT c.fips,
                 SUM(ST_Length(ST_Transform(ST_Intersection(t.geom, c.geom), 3083))) / 1000.0 AS km
          FROM tx_counties c JOIN transmission_lines t ON ST_Intersects(t.geom, c.geom)
          GROUP BY c.fips
        )
        UPDATE county_features cf
        SET line_km_per_1000km2 = COALESCE(ld.km, 0) / cf.area_km2 * 1000
        FROM ld WHERE ld.fips = cf.fips;
        UPDATE county_features SET line_km_per_1000km2 = 0 WHERE line_km_per_1000km2 IS NULL;
        """
    )

    # Substations: count, density, max voltage (headroom proxy)
    print("  substations")
    run_sql(
        """
        ALTER TABLE county_features
            ADD COLUMN substation_count int,
            ADD COLUMN substation_per_1000km2 double precision,
            ADD COLUMN max_substation_kv double precision;
        WITH sc AS (
          SELECT c.fips, count(*) AS n, max(s.max_volt) AS mv
          FROM tx_counties c JOIN substations s ON ST_Contains(c.geom, s.geom)
          GROUP BY c.fips
        )
        UPDATE county_features cf SET
          substation_count = COALESCE(sc.n, 0),
          max_substation_kv = sc.mv,
          substation_per_1000km2 = COALESCE(sc.n, 0) / cf.area_km2 * 1000
        FROM sc WHERE sc.fips = cf.fips;
        UPDATE county_features
        SET substation_count = 0, substation_per_1000km2 = 0
        WHERE substation_count IS NULL;
        """
    )

    # Queued MW in county (development saturation / congestion proxy)
    print("  queued MW")
    run_sql(
        """
        ALTER TABLE county_features ADD COLUMN queued_mw double precision;
        WITH qm AS (
          SELECT fips_code AS fips,
                 SUM(CASE WHEN mw_1::text ~ '^[0-9]+(\\.[0-9]+)?$' THEN mw_1::text::numeric ELSE 0 END) AS mw
          FROM queue_lbnl WHERE LEFT(fips_code, 2) = '48' GROUP BY fips_code
        )
        UPDATE county_features cf SET queued_mw = COALESCE(qm.mw, 0)
        FROM qm WHERE qm.fips = cf.fips;
        UPDATE county_features SET queued_mw = 0 WHERE queued_mw IS NULL;
        """
    )

    # Wetland %: total wetland acres (intersecting county) / county area
    print("  wetland % (2.2M polygons — index-join, may take a minute)")
    run_sql(
        """
        ALTER TABLE county_features ADD COLUMN wetland_pct double precision;
        WITH wp AS (
          SELECT c.fips, SUM(w.acres) * %s AS km2
          FROM tx_counties c JOIN wetlands w ON ST_Intersects(c.geom, w.geom)
          GROUP BY c.fips
        )
        UPDATE county_features cf
        SET wetland_pct = LEAST(COALESCE(wp.km2, 0) / cf.area_km2 * 100, 100)
        FROM wp WHERE wp.fips = cf.fips;
        UPDATE county_features SET wetland_pct = 0 WHERE wetland_pct IS NULL;
        """ % ACRE_TO_KM2
    )

    # Floodway % and protected (GAP 1/2) % — accurate intersection areas
    print("  floodway % + protected %")
    run_sql(
        """
        ALTER TABLE county_features
            ADD COLUMN floodway_pct double precision,
            ADD COLUMN protected_pct double precision;
        WITH fp AS (
          SELECT c.fips, SUM(ST_Area(ST_Transform(ST_Intersection(f.geom, c.geom), 3083)))/1e6 AS km2
          FROM tx_counties c JOIN floodways f ON ST_Intersects(f.geom, c.geom)
          GROUP BY c.fips
        )
        UPDATE county_features cf SET floodway_pct = LEAST(COALESCE(fp.km2,0)/cf.area_km2*100, 100)
        FROM fp WHERE fp.fips = cf.fips;

        WITH pp AS (
          SELECT c.fips, SUM(ST_Area(ST_Transform(ST_Intersection(p.geom, c.geom), 3083)))/1e6 AS km2
          FROM tx_counties c JOIN protected_areas p ON ST_Intersects(p.geom, c.geom)
          WHERE p.gap_sts IN ('1','2')
          GROUP BY c.fips
        )
        UPDATE county_features cf SET protected_pct = LEAST(COALESCE(pp.km2,0)/cf.area_km2*100, 100)
        FROM pp WHERE pp.fips = cf.fips;

        UPDATE county_features SET floodway_pct = 0 WHERE floodway_pct IS NULL;
        UPDATE county_features SET protected_pct = 0 WHERE protected_pct IS NULL;
        ALTER TABLE county_features DROP COLUMN centroid;
        """
    )


def build_raster_features() -> None:
    print("county_features: raster zonal stats (slope + land cover)")
    counties = gpd.read_postgis(
        "SELECT fips, geom FROM tx_counties", engine(), geom_col="geom"
    )

    def pct_steep(arr):
        a = np.ma.compressed(arr)
        return float((a > STEEP_PCT).sum()) / a.size if a.size else None

    print("  slope (mean + % steep) — reading the 5.5GB raster per county, be patient")
    slope = zonal_stats(
        counties, SLOPE_TIF, stats=["mean"], add_stats={"pct_steep": pct_steep},
        all_touched=False,
    )

    print("  land cover (developable fraction)")
    nlcd = zonal_stats(counties, NLCD_TIF, categorical=True, nodata=-128)

    rows = []
    for i, fips in enumerate(counties["fips"]):
        s = slope[i] or {}
        hist = {int(k): v for k, v in (nlcd[i] or {}).items() if k is not None and int(k) > 0}
        total = sum(hist.values())
        dev = sum(v for k, v in hist.items() if k in DEVELOPABLE_NLCD)
        rows.append({
            "fips": fips,
            "mean_slope_pct": s.get("mean"),
            "pct_steep": s.get("pct_steep"),
            "developable_frac": (dev / total) if total else None,
        })
    df = pd.DataFrame(rows)
    df.to_sql("_cf_raster", engine(), if_exists="replace", index=False)
    run_sql(
        """
        ALTER TABLE county_features
            ADD COLUMN mean_slope_pct double precision,
            ADD COLUMN pct_steep double precision,
            ADD COLUMN developable_frac double precision;
        UPDATE county_features cf SET
          mean_slope_pct = r.mean_slope_pct,
          pct_steep = r.pct_steep,
          developable_frac = r.developable_frac
        FROM _cf_raster r WHERE r.fips = cf.fips;
        DROP TABLE _cf_raster;
        """
    )


def summarize() -> None:
    n = query("SELECT count(*) FROM county_features")[0][0]
    print(f"\ncounty_features rows: {n}")
    print("sample counties (eyeball: Pecos=flat+near grid, Gulf Coast=wet):")
    for r in query(
        """
        SELECT county_name,
               round(dist_line_mi::numeric,1) AS line_mi,
               round(line_km_per_1000km2::numeric,0) AS line_dens,
               substation_count AS subs,
               round(mean_slope_pct::numeric,1) AS slope,
               round(wetland_pct::numeric,1) AS wet,
               round(floodway_pct::numeric,1) AS flood,
               round(developable_frac::numeric,2) AS dev,
               round(queued_mw::numeric,0) AS q_mw
        FROM county_features
        WHERE county_name IN ('Pecos','Harris','Jefferson','Chambers','Crane','Webb','Travis')
        ORDER BY county_name
        """
    ):
        print(f"  {r[0]:11} line={r[1]:>5}mi dens={r[2]:>4} subs={r[3]:>4} "
              f"slope={r[4]:>4}% wet={r[5]:>5}% flood={r[6]:>4}% dev={r[7]} qMW={r[8]}")


def main() -> None:
    build_vector_features()
    build_raster_features()
    summarize()


if __name__ == "__main__":
    main()
