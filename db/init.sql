-- Runs once on first container start (mounted into docker-entrypoint-initdb.d).
-- Enables the spatial extensions the whole project depends on.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Allow out-of-db rasters and all GDAL drivers (needed for raster2pgsql workflows)
SET postgis.enable_outdb_rasters = true;
SET postgis.gdal_enabled_drivers = 'ENABLE_ALL';
