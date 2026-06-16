"""Shared configuration for the Hex Energy pipeline.

Central place for the DB connection, CRS constants, and the pilot county scope.
Disk-constrained MVP: we build for a few adjacent counties first, then scale to
statewide Texas later. Edit PILOT_COUNTIES to change scope.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
ROOT = Path(__file__).resolve().parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
ARTIFACTS = ROOT / "ml" / "artifacts"

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hex:hex@localhost:5432/hex")
PG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": os.getenv("PGPORT", "5432"),
    "user": os.getenv("PGUSER", "hex"),
    "password": os.getenv("PGPASSWORD", "hex"),
    "dbname": os.getenv("PGDATABASE", "hex"),
}

def ogr_pg_conn() -> str:
    """Connection string in the form ogr2ogr expects."""
    return (
        f"PG:host={PG['host']} port={PG['port']} user={PG['user']} "
        f"password={PG['password']} dbname={PG['dbname']}"
    )

# --- CRS ---
STORAGE_CRS = os.getenv("STORAGE_CRS", "EPSG:4326")    # store/serve
PROJECTED_CRS = os.getenv("PROJECTED_CRS", "EPSG:3083")  # distance/area math (TX Albers)

# --- Scope ---
# STATEWIDE=True clips/loads all of Texas (better model training coverage).
# Set False + populate PILOT_COUNTIES to fall back to a county subset if disk gets tight.
TX_STATE_FIPS = "48"
STATEWIDE = True

# Only used when STATEWIDE is False. FIPS = state(48=TX) + county.
PILOT_COUNTIES = {
    "Pecos": "48371",
    "Crane": "48103",
    "Upton": "48461",
}

# Keep raw rasters (DEM/NLCD) as FILES and store only derived per-cell features,
# rather than loading full rasters into PostGIS — major disk saver for statewide.
RASTERS_IN_DB = False
