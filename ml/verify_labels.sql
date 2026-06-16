-- ============================================================================
--  Hex Energy — P1 Checkpoint 1 Verification: the labels table
--  Run: docker exec -i hex-postgis psql -U hex -d hex < ml/verify_labels.sql
--  PASS = sane class balance, geolocation only on built (withdrawn have no coords),
--         matches land in the stated county, zero integrity issues.
-- ============================================================================
\echo ''
\echo '======== 1. CLASS BALANCE (overall + ERCOT subset) ========'
SELECT 'ALL TX' AS scope, count(*) AS n,
       count(*) FILTER (WHERE label=1) AS built, count(*) FILTER (WHERE label=0) AS dead,
       round(100.0*count(*) FILTER (WHERE label=1)/count(*),1) AS build_rate_pct
FROM labels
UNION ALL
SELECT 'ERCOT only', count(*), count(*) FILTER (WHERE label=1), count(*) FILTER (WHERE label=0),
       round(100.0*count(*) FILTER (WHERE label=1)/count(*),1)
FROM labels WHERE region='ERCOT';

\echo ''
\echo '======== 2. GEOLOCATION (built only; withdrawn have no coords by design) ========'
SELECT label, count(*) AS total, count(geom) AS geolocated,
       round(100.0*count(geom)/count(*),0) AS pct
FROM labels GROUP BY label ORDER BY label;

\echo ''
\echo '======== 3. SPOT-CHECK: EIA name matches (should be clearly the same project) ========'
SELECT left(NULLIF(l.project_name,''),24) AS queue_project, left(e.name,22) AS matched_eia_plant, l.county
FROM labels l JOIN eia860_plants e ON e.plant_code = l.eia_plant_code
WHERE l.label=1 AND l.geom IS NOT NULL
ORDER BY l.q_id LIMIT 10;

\echo ''
\echo '======== 4. INTEGRITY ========'
SELECT count(*) FILTER (WHERE fips_code IS NULL) AS null_fips,
       count(*) FILTER (WHERE label NOT IN (0,1)) AS bad_label,
       count(*) FILTER (WHERE region IS NULL) AS null_region,
       count(DISTINCT fips_code) AS distinct_counties
FROM labels;

\echo ''
\echo '======== 5. LABEL BREAKDOWN BY REGION ========'
SELECT region, count(*) FILTER (WHERE label=1) AS built, count(*) FILTER (WHERE label=0) AS dead
FROM labels GROUP BY region ORDER BY count(*) DESC;
