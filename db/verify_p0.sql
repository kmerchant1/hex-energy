-- ============================================================================
--  Hex Energy — P0 Milestone Verification
--  Confirms the data foundation is complete and the full (vector) feature set is
--  computable for a sample county, before moving to P1 (labels -> features -> model).
--
--  Run:
--    docker exec -i hex-postgis psql -U hex -d hex < db/verify_p0.sql
--  (raster features — slope / NLCD — are checked separately by
--   ingestion/verify_rasters.py, since they live as files, not in PostGIS)
--
--  All distance/area math uses EPSG:3083 (TX Albers) or ::geography for accuracy.
-- ============================================================================
\set SAMPLE_COUNTY 'Pecos'
\timing on

\echo ''
\echo '================ 1. TABLE INVENTORY (rows + SRID) ================'
SELECT table_name, rows, srid, expected FROM (
  SELECT 'tx_counties'        AS table_name, (SELECT count(*) FROM tx_counties)        AS rows, (SELECT ST_SRID(geom) FROM tx_counties LIMIT 1)        AS srid, '254'                  AS expected
  UNION ALL SELECT 'pilot_boundary',     (SELECT count(*) FROM pilot_boundary),     (SELECT ST_SRID(geom) FROM pilot_boundary LIMIT 1),     '1'
  UNION ALL SELECT 'eia860_plants',      (SELECT count(*) FROM eia860_plants),      (SELECT ST_SRID(geom) FROM eia860_plants LIMIT 1),      '~1367'
  UNION ALL SELECT 'queue_lbnl',         (SELECT count(*) FROM queue_lbnl),         NULL,                                                   '~38201 (non-spatial)'
  UNION ALL SELECT 'floodways',          (SELECT count(*) FROM floodways),          (SELECT ST_SRID(geom) FROM floodways LIMIT 1),          '~4257'
  UNION ALL SELECT 'transmission_lines', (SELECT count(*) FROM transmission_lines), (SELECT ST_SRID(geom) FROM transmission_lines LIMIT 1), '~7579'
  UNION ALL SELECT 'substations',        (SELECT count(*) FROM substations),        (SELECT ST_SRID(geom) FROM substations LIMIT 1),        '~6117'
  UNION ALL SELECT 'wetlands',           (SELECT count(*) FROM wetlands),           (SELECT ST_SRID(geom) FROM wetlands LIMIT 1),           '~2.23M'
  UNION ALL SELECT 'protected_areas',    (SELECT count(*) FROM protected_areas),    (SELECT ST_SRID(geom) FROM protected_areas LIMIT 1),    'PAD-US'
) t ORDER BY table_name;

\echo ''
\echo '================ 2. SPATIAL INDEXES (GIST present per table) ================'
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public' AND indexdef ILIKE '%USING gist%'
ORDER BY tablename;

\echo ''
\echo '================ 3. GEOMETRY VALIDITY (invalid counts; wetlands sampled) ================'
SELECT 'transmission_lines' AS tbl, count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid FROM transmission_lines
UNION ALL SELECT 'substations',     count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM substations
UNION ALL SELECT 'floodways',       count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM floodways
UNION ALL SELECT 'protected_areas', count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM protected_areas
UNION ALL SELECT 'wetlands (50k sample)', count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM (SELECT geom FROM wetlands LIMIT 50000) w;

\echo ''
\echo '================ 4. FEATURE COMPUTATION — sample points in :SAMPLE_COUNTY ================'
\echo '(each EIA plant in the county; all features should return sane, non-null values)'
WITH pts AS (
    SELECT p.plant_code, p.name, p.geom,
           ST_Transform(p.geom, 3083)            AS g3,
           ST_Buffer(p.geom::geography, 1000)::geometry AS buf1km  -- 1 km circle for overlap %
    FROM eia860_plants p
    JOIN tx_counties c ON ST_Contains(c.geom, p.geom)
    WHERE c.name = :'SAMPLE_COUNTY'
    LIMIT 8
)
SELECT
    left(pts.name, 26)                                              AS plant,
    (SELECT c.name FROM tx_counties c
       WHERE ST_Contains(c.geom, pts.geom) LIMIT 1)                 AS county,
    round(line.mi::numeric, 2)                                      AS near_line_mi,
    line.voltage                                                    AS line_kv,
    round(sub.mi::numeric, 2)                                       AS near_sub_mi,
    sub.max_volt                                                    AS sub_kv,
    (SELECT count(*) FROM transmission_lines t
       WHERE ST_DWithin(t.geom::geography, pts.geom::geography, 8047)) AS lines_5mi,
    EXISTS (SELECT 1 FROM floodways f
       WHERE ST_DWithin(f.geom::geography, pts.geom::geography, 1609)) AS floodway_1mi,
    EXISTS (SELECT 1 FROM protected_areas pa
       WHERE ST_DWithin(pa.geom::geography, pts.geom::geography, 1609)) AS protected_1mi,
    round(wet.pct::numeric, 2)                                      AS wetland_pct_1km
FROM pts
LEFT JOIN LATERAL (
    SELECT ST_Distance(pts.g3, ST_Transform(t.geom, 3083)) / 1609.34 AS mi, t.voltage
    FROM transmission_lines t ORDER BY t.geom <-> pts.geom LIMIT 1
) line ON true
LEFT JOIN LATERAL (
    SELECT ST_Distance(pts.g3, ST_Transform(s.geom, 3083)) / 1609.34 AS mi, s.max_volt
    FROM substations s ORDER BY s.geom <-> pts.geom LIMIT 1
) sub ON true
LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(ST_Area(ST_Intersection(w.geom, pts.buf1km)::geography)), 0)
           / NULLIF(ST_Area(pts.buf1km::geography), 0) * 100 AS pct
    FROM wetlands w WHERE ST_Intersects(w.geom, pts.buf1km)
) wet ON true;

\echo ''
\echo '================ 5. LABEL READINESS FOR P1 (ERCOT trainable) ================'
SELECT q_status, count(*) AS n
FROM queue_lbnl
WHERE region = 'ERCOT' AND q_status IN ('operational', 'withdrawn')
GROUP BY q_status ORDER BY n DESC;

\echo ''
\echo '-- county-level queued MW (headroom-proxy input) for :SAMPLE_COUNTY --'
SELECT c.name AS county, count(q.*) AS queue_projects, round(sum(q.mw_1)::numeric, 0) AS queued_mw
FROM tx_counties c
LEFT JOIN queue_lbnl q ON q.fips_code = c.fips AND q.region = 'ERCOT'
WHERE c.name = :'SAMPLE_COUNTY'
GROUP BY c.name;

\echo ''
\echo '================ P0 VERIFICATION COMPLETE ================'
\echo 'PASS criteria: all tables present w/ SRID 4326, GIST indexes present, ~0 invalid'
\echo 'geoms, feature columns non-null & sane, ERCOT labels present. Then run'
\echo 'ingestion/verify_rasters.py for slope/NLCD, and P0 is done.'
