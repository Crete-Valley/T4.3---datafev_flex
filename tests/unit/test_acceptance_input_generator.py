from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def _load_generator_module():
    script_path = Path("scripts/generate_acceptance_inputs.py")
    spec = importlib.util.spec_from_file_location(
        "generate_acceptance_inputs_module",
        script_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(path) as writer:
        for sheet_name, dataframe in sheets.items():
            dataframe.to_excel(writer, sheet_name=sheet_name, index=False)


def test_generate_cases_preserves_day_ahead_market_prices(tmp_path):
    module = _load_generator_module()

    planning = pd.DataFrame(
        [
            {
                "planning_start": "2024-01-01 08:00",
                "planning_end": "2024-01-01 14:00",
                "time_step_minutes": 15,
            }
        ]
    )
    fleet = pd.DataFrame(
        [
            {
                "vehicle_id": "EV1",
                "battery_capacity_kWh": 60,
                "arrival_time": "2024-01-01 08:00",
                "departure_time": "2024-01-01 10:00",
                "initial_soc": 0.4,
                "target_soc": 0.8,
                "use_target_soc": 1,
                "min_allowed_soc": 0.2,
                "max_allowed_soc": 1.0,
                "target_cluster": "1",
                "p_max_charge_kW": 11.0,
                "p_max_discharge_kW": 0.0,
            }
        ]
    )
    clusters = pd.DataFrame(
        [
            {
                "cluster_id": "1",
                "cu_id": "CU1",
                "p_max_ch_kW": 11.0,
                "p_max_ds_kW": 0.0,
                "efficiency": 0.95,
            }
        ]
    )
    day_ahead_prices = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01 08:00", periods=24, freq="15min"),
            "price_eur_per_kwh": [0.10 + 0.01 * i for i in range(24)],
        }
    )
    absolute_setpoints = pd.DataFrame(
        {
            "cluster_id": ["1", "1"],
            "timestamp": pd.date_range("2024-01-01 08:00", periods=2, freq="15min"),
            "p_set_kw": [1.0, 1.0],
        }
    )
    flex_band_setpoints = pd.DataFrame(
        {
            "cluster_id": ["1", "1"],
            "timestamp": pd.date_range("2024-01-01 08:00", periods=2, freq="15min"),
            "p_min_kw": [0.0, 0.0],
            "p_max_kw": [2.0, 2.0],
        }
    )

    sample_stage1 = tmp_path / "stage1_sample_input.xlsx"
    sample_stage2_absolute = tmp_path / "stage2_sample_absolute_setpoints.xlsx"
    sample_stage2_flex = tmp_path / "stage2_sample_flex_band_commands.xlsx"
    output_dir = tmp_path / "acceptance_cases"

    _write_workbook(
        sample_stage1,
        {
            "Planning": planning,
            "Fleet": fleet,
            "Clusters": clusters,
            "DayAheadMarketPrices": day_ahead_prices,
        },
    )
    _write_workbook(sample_stage2_absolute, {"Setpoints": absolute_setpoints})
    _write_workbook(sample_stage2_flex, {"Setpoints": flex_band_setpoints})

    module.SAMPLE_STAGE1_FILE = sample_stage1
    module.SAMPLE_STAGE2_ABSOLUTE_FILE = sample_stage2_absolute
    module.SAMPLE_STAGE2_FLEX_BAND_FILE = sample_stage2_flex

    module.generate_cases(output_dir)

    baseline_prices = pd.read_excel(
        output_dir / "stage1_baseline_multi_cluster.xlsx",
        sheet_name="DayAheadMarketPrices",
    )
    short_prices = pd.read_excel(
        output_dir / "stage1_single_cluster_short_horizon.xlsx",
        sheet_name="DayAheadMarketPrices",
    )

    assert list(baseline_prices.columns) == ["timestamp", "price_eur_per_kwh"]
    assert len(baseline_prices) == 24
    assert len(short_prices) == 20
