-- ============================================================================
--  Hex Energy — P1 Checkpoint 2 Verification: county_features
--  Run: docker exec -i hex-postgis psql -U hex -d hex < ml/verify_county_features.sql
--  PASS = 254 rows, zero nulls, sane ranges, and sample counties match real geography
--         (Gulf Coast = wet+flat, West TX = dry+developable, Harris = dense grid).
-- ============================================================================
\echo ''
\echo '======== 1. ROW COUNT (expect 254 TX counties) ========'
SELECT count(*) AS county_features_rows FROM county_features;

\echo ''
\echo '======== 2. NULL CHECK (every column should be 0) ========'
SELECT
  count(*) FILTER (WHERE dist_line_mi IS NULL)        AS dist_line,
  count(*) FILTER (WHERE dist_hv_line_mi IS NULL)     AS dist_hv,
  count(*) FILTER (WHERE line_km_per_1000km2 IS NULL) AS line_dens,
  count(*) FILTER (WHERE max_substation_kv IS NULL)   AS max_kv,
  count(*) FILTER (WHERE queued_mw IS NULL)           AS qmw,
  count(*) FILTER (WHERE wetland_pct IS NULL)         AS wet,
  count(*) FILTER (WHERE floodway_pct IS NULL)        AS flood,
  count(*) FILTER (WHERE protected_pct IS NULL)       AS prot,
  count(*) FILTER (WHERE mean_slope_pct IS NULL)      AS slope,
  count(*) FILTER (WHERE developable_frac IS NULL)    AS dev
FROM county_features;

\echo ''
\echo '======== 3. FEATURE RANGES (min / avg / max — sanity) ========'
SELECT 'dist_line_mi' AS feature, round(min(dist_line_mi)::numeric,2) AS mn, round(avg(dist_line_mi)::numeric,2) AS avg, round(max(dist_line_mi)::numeric,2) AS mx FROM county_features
UNION ALL SELECT 'mean_slope_pct',   round(min(mean_slope_pct)::numeric,2),   round(avg(mean_slope_pct)::numeric,2),   round(max(mean_slope_pct)::numeric,2) FROM county_features
UNION ALL SELECT 'wetland_pct',      round(min(wetland_pct)::numeric,2),      round(avg(wetland_pct)::numeric,2),      round(max(wetland_pct)::numeric,2) FROM county_features
UNION ALL SELECT 'developable_frac', round(min(developable_frac)::numeric,2), round(avg(developable_frac)::numeric,2), round(max(developable_frac)::numeric,2) FROM county_features
UNION ALL SELECT 'line_km_per_1000km2', round(min(line_km_per_1000km2)::numeric,0), round(avg(line_km_per_1000km2)::numeric,0), round(max(line_km_per_1000km2)::numeric,0) FROM county_features
UNION ALL SELECT 'substation_count', min(substation_count), round(avg(substation_count)::numeric,1), max(substation_count) FROM county_features
ORDER BY feature;

\echo ''
\echo '======== 4. SAMPLE COUNTIES (eyeball vs known geography) ========'
SELECT county_name,
       round(dist_line_mi::numeric,1) AS line_mi, round(line_km_per_1000km2::numeric,0) AS dens,
       substation_count AS subs, round(mean_slope_pct::numeric,1) AS slope_pct,
       round(wetland_pct::numeric,1) AS wet_pct, round(developable_frac::numeric,2) AS dev,
       round(queued_mw::numeric,0) AS queued_mw
FROM county_features
WHERE county_name IN ('Pecos','Crane','Webb','Harris','Travis','Chambers','Jefferson')
ORDER BY county_name;
