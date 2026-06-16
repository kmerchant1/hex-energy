# Hex Energy

AI-powered **siting intelligence** for renewable energy developers — pilot: **Texas / ERCOT**.

Helps a solar/storage developer screen land and pick the right site *before* spending
money on land options or entering the interconnection queue: a suitability heatmap,
per-parcel fatal-flaw analysis, and a one-click compliant shapefile export.

See the full plan in `~/.claude/plans/i-want-to-develop-encapsulated-beaver.md` and the
research in `research.md`.

## Project layout

```
ingestion/   # download + load public datasets into PostGIS
ml/          # labels, feature build, model training + testing
heatmap/     # grid Texas, score cells, export tiles
api/         # FastAPI scoring/report/export endpoints (P3+)
db/          # PostGIS init + SQL
data/raw/    # downloaded datasets (gitignored)
config.py    # DB connection, CRS, pilot county scope
```

## Setup

Prereqs already expected: Homebrew, Docker, conda, git.

```bash
# 1. Python/geospatial environment
conda env create -f environment.yml
conda activate hex-energy

# 2. Database + cache
cp .env.example .env
docker compose up -d          # PostGIS (5432) + Redis (6379)

# 3. Verify
ogr2ogr --version
psql postgresql://hex:hex@localhost:5432/hex -c "SELECT postgis_full_version();"
```

## Scope note

Disk-constrained MVP builds for a few adjacent counties first (`config.PILOT_COUNTIES`),
then scales to statewide Texas. Datasets and sizes are documented in the plan's
**Phase 0 — Expanded Runbook**.
