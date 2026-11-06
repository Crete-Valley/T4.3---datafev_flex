#!/usr/bin/env python3
"""Import Stage-1 workbook data into the database and planning API.

This script reads the sample workbook and writes the fleet/cluster rows into the
configured database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.db_models import Cluster, Fleet, MarketPrice
from database.db_transactions import insert_cluster, insert_fleet, insert_market_price

DEFAULT_INPUT_FILE = ROOT_DIR / "inputs" / "stage1_sample_input.xlsx"


def import_excel_inputs(input_file: str | os.PathLike[str] = DEFAULT_INPUT_FILE) -> dict[str, Any]:
    """Read the sample workbook and persist the data into the database."""
    input_path = Path(input_file)
    workbook = pd.ExcelFile(input_path)

    if "Fleet" not in workbook.sheet_names:
        raise ValueError("Input workbook must contain a 'Fleet' sheet.")
    if "Clusters" not in workbook.sheet_names:
        raise ValueError("Input workbook must contain a 'Clusters' sheet.")
    if "DayAheadMarketPrices" not in workbook.sheet_names:
        raise ValueError("Input workbook must contain a 'DayAheadMarketPrices' sheet.")

    fleet_df = workbook.parse("Fleet")
    clusters_df = workbook.parse("Clusters")
    market_prices_df = workbook.parse("DayAheadMarketPrices")

    for _, row in clusters_df.iterrows():
        cluster = Cluster(
            cluster_id=int(row["cluster_id"]),
            cu_id=str(row["cu_id"]),
            p_max_ch_kW=float(row["p_max_ch_kW"]),
            p_max_ds_kW=float(row["p_max_ds_kW"]),
            efficiency=float(row["efficiency"]),
        )
        insert_cluster(cluster)

    for _, row in fleet_df.iterrows():
        fleet = Fleet(
            vehicle_id=str(row["vehicle_id"]),
            battery_capacity_kWh=float(row["battery_capacity_kWh"]),
            arrival_time=pd.to_datetime(row["arrival_time"]).to_pydatetime(),
            departure_time=pd.to_datetime(row["departure_time"]).to_pydatetime(),
            initial_soc=float(row["initial_soc"]),
            target_soc=float(row["target_soc"]),
            use_target_soc=bool(int(row["use_target_soc"])),
            min_allowed_soc=float(row["min_allowed_soc"]),
            max_allowed_soc=float(row["max_allowed_soc"]),
            target_cluster=str(row["target_cluster"]),
            p_max_charge_kW=float(row["p_max_charge_kW"]),
            p_max_discharge_kW=float(row["p_max_discharge_kW"]),
            exact_target_soc=bool(int(row.get("exact_target_soc", 0))),
        )
        insert_fleet(fleet)

    for _, row in market_prices_df.iterrows():
        market_price = MarketPrice(
            timestamp=pd.to_datetime(row["timestamp"]).to_pydatetime(),
            price_eur_per_kwh=float(row["price_eur_per_kwh"]),
        )
        insert_market_price(market_price)

    return {
        "cluster_rows": int(len(clusters_df)),
        "fleet_rows": int(len(fleet_df)),
        "market_price_rows": int(len(market_prices_df)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Excel inputs into the database")
    parser.add_argument("--input-file", default=str(DEFAULT_INPUT_FILE), help="Path to the Excel workbook")
    args = parser.parse_args()

    result = import_excel_inputs(input_file=args.input_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
