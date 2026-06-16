"""Load the LBNL 'Queued Up' Complete Queue Data into PostGIS as the label source.

This is the project-level interconnection-request dataset: each row is a request with
a status (operational / withdrawn / active / ...), region (ISO), state, county+fips,
resource type, capacity, and queue year. It is the PRIMARY training-label source.

Creates:
  queue_lbnl — full national project-level table (filtered to TX/ERCOT later in P1).

Usage: python ingestion/lbnl.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_RAW  # noqa: E402
from ingestion._db import engine, query, table_count  # noqa: E402

SHEET = "03. Complete Queue Data"


def main() -> None:
    files = list(DATA_RAW.glob("LBNL*Queue*.xlsx")) + list(DATA_RAW.glob("*Queued*Data*.xlsx"))
    if not files:
        raise FileNotFoundError(
            "LBNL queue xlsx not found in data/raw. Download the 'Queued Up ... Data File "
            "XLSX' from https://emp.lbl.gov/queues into data/raw/."
        )
    f = files[0]
    print(f"LBNL queue: {f.name}")

    df = pd.read_excel(f, sheet_name=SHEET, header=1)
    df.columns = [str(c).strip().lower() for c in df.columns]
    # Drop fully-empty trailing rows
    df = df.dropna(how="all")
    # fips_code -> zero-padded 5-char string to match tx_counties.fips
    if "fips_code" in df.columns:
        df["fips_code"] = (
            df["fips_code"].dropna().astype(float).astype(int).astype(str).str.zfill(5)
            if df["fips_code"].notna().any() else df["fips_code"]
        )

    df.to_sql("queue_lbnl", engine(), if_exists="replace", index=False, chunksize=5000)

    print(f"  queue_lbnl rows: {table_count('queue_lbnl')}")
    print("  status distribution (all US):")
    for status, n in query(
        "SELECT q_status, count(*) FROM queue_lbnl GROUP BY q_status ORDER BY 2 DESC"
    ):
        print(f"    {status:14} {n}")
    print("  Texas rows by region:")
    for region, n in query(
        "SELECT region, count(*) FROM queue_lbnl WHERE state='TX' GROUP BY region ORDER BY 2 DESC"
    ):
        print(f"    {region:14} {n}")


if __name__ == "__main__":
    main()
