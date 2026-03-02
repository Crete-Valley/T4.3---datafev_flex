import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from utils.input_parser import (
    parse_planning_sheet,
    parse_stage2_setpoints_sheet,
    parse_xlsx_input,
)


def _create_fleet_rows():
    return pd.DataFrame(
        [
            {
                "vehicle_id": "EV1",
                "battery_capacity_kWh": 60,
                "arrival_time": "2024-01-01 08:00",
                "departure_time": "2024-01-01 10:00",
                "initial_soc": 0.4,
                "target_soc": 0.9,
                "use_target_soc": True,
                "min_allowed_soc": 0.2,
                "max_allowed_soc": 1.0,
                "target_cluster": "1",
                "p_max_charge_kW": 7.2,
                "p_max_discharge_kW": 3.0,
            },
            {
                "vehicle_id": "EV2",
                "battery_capacity_kWh": 55,
                "arrival_time": "2024-01-01 09:00",
                "departure_time": "2024-01-01 11:00",
                "initial_soc": 0.5,
                "target_soc": 0.8,
                "use_target_soc": False,
                "min_allowed_soc": 0.3,
                "max_allowed_soc": 1.0,
                "target_cluster": "2",
                "p_max_charge_kW": 11.0,
                "p_max_discharge_kW": 3.0,
            },
        ]
    )


def test_parse_xlsx_input_with_cluster_sheets_returns_expected_structures(excel_builder):
    """Happy path: verify fleets and per-cluster tables are parsed correctly."""
    fleet_df = _create_fleet_rows()
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1", "CU2"],
            "p_max_ch_kW": [40, 40],
            "p_max_ds_kW": [30, 25],
            "efficiency": [0.95, 0.96],
        }
    )

    file_path = excel_builder({"Fleet": fleet_df, "Cluster1": cluster_df})

    clusters, parsed_fleet = parse_xlsx_input(str(file_path))

    assert set(clusters.keys()) == {"1"}, "Expected a single cluster key when sheet is Cluster1"
    assert_frame_equal(clusters["1"].reset_index(drop=True), cluster_df)

    assert len(parsed_fleet) == 2, "All fleet rows should be preserved"
    assert parsed_fleet["use_target_soc"].tolist() == [1, 0], "Flags should be coerced to 0/1"
    assert pd.api.types.is_datetime64_ns_dtype(parsed_fleet["arrival_time"]), (
        "Arrival times should be normalized to datetime64[ns]"
    )
    assert pd.api.types.is_datetime64_ns_dtype(parsed_fleet["departure_time"]), (
        "Departure times should be normalized to datetime64[ns]"
    )


def test_parse_xlsx_input_consolidated_cluster_sheet_is_split_by_id(excel_builder):
    """Sheets named 'Clusters' with cluster_id column should fan out into multiple entries."""
    fleet_df = _create_fleet_rows()
    clusters_df = pd.DataFrame(
        {
            "cluster_id": [1, 2],
            "cu_id": ["A", "B"],
            "p_max_ch_kW": [30, 50],
            "p_max_ds_kW": [20, 35],
            "efficiency": [0.95, 0.9],
        }
    )

    file_path = excel_builder({"Fleet": fleet_df, "Clusters": clusters_df})

    clusters, _ = parse_xlsx_input(str(file_path))

    assert set(clusters) == {"1", "2"}, "cluster_id column should define the dictionary keys"
    assert clusters["1"].iloc[0]["cu_id"] == "A"
    assert clusters["2"].iloc[0]["cu_id"] == "B"


def test_parse_xlsx_input_without_cluster_sheet_raises(excel_builder):
    """A helpful error should be raised when no cluster definitions exist."""
    fleet_df = _create_fleet_rows()
    file_path = excel_builder({"Fleet": fleet_df})

    with pytest.raises(ValueError, match="No cluster sheets"):
        parse_xlsx_input(str(file_path))


def test_parse_xlsx_input_without_fleet_sheet_raises(excel_builder):
    """The Fleet sheet is mandatory; verify the parser validates this requirement."""
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "p_max_ch_kW": [40],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    file_path = excel_builder({"Cluster1": cluster_df})

    with pytest.raises(ValueError, match="must contain a 'Fleet' sheet"):
        parse_xlsx_input(str(file_path))


def test_parse_xlsx_input_defaults_exact_target_soc_to_zero(excel_builder):
    fleet_df = _create_fleet_rows()
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "p_max_ch_kW": [40],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    file_path = excel_builder({"Fleet": fleet_df, "Cluster1": cluster_df})

    _, parsed_fleet = parse_xlsx_input(str(file_path))
    assert "exact_target_soc" in parsed_fleet.columns
    assert parsed_fleet["exact_target_soc"].tolist() == [0, 0]


def test_parse_xlsx_input_reads_exact_target_soc_flag(excel_builder):
    fleet_df = _create_fleet_rows()
    fleet_df["exact_target_soc"] = [1, 0]
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "p_max_ch_kW": [40],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    file_path = excel_builder({"Fleet": fleet_df, "Cluster1": cluster_df})

    _, parsed_fleet = parse_xlsx_input(str(file_path))
    assert parsed_fleet["exact_target_soc"].tolist() == [1, 0]


def test_parse_planning_sheet_reads_minimum_required_fields(excel_builder):
    planning_df = pd.DataFrame(
        [
            {
                "planning_start": "2024-01-01 08:00",
                "planning_end": "2024-01-01 12:00",
                "time_step_minutes": 15,
            }
        ]
    )
    fleet_df = _create_fleet_rows()
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "p_max_ch_kW": [40],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    file_path = excel_builder(
        {"Planning": planning_df, "Fleet": fleet_df, "Cluster1": cluster_df}
    )

    planning = parse_planning_sheet(str(file_path))

    assert planning["planning_start"] == pd.Timestamp("2024-01-01 08:00").to_pydatetime()
    assert planning["planning_end"] == pd.Timestamp("2024-01-01 12:00").to_pydatetime()
    assert planning["time_step_minutes"] == 15


def test_parse_planning_sheet_raises_without_required_columns(excel_builder):
    planning_df = pd.DataFrame(
        [
            {
                "planning_start": "2024-01-01 08:00",
                "planning_end": "2024-01-01 12:00",
            }
        ]
    )
    fleet_df = _create_fleet_rows()
    cluster_df = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "p_max_ch_kW": [40],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    file_path = excel_builder(
        {"Planning": planning_df, "Fleet": fleet_df, "Cluster1": cluster_df}
    )

    with pytest.raises(ValueError, match="Planning"):
        parse_planning_sheet(str(file_path))


def test_parse_stage2_setpoints_sheet_returns_cluster_series(excel_builder):
    setpoints_df = pd.DataFrame(
        [
            {"cluster_id": "2", "timestamp": "2024-01-01 08:15", "p_set_kw": 5.0},
            {"cluster_id": "1", "timestamp": "2024-01-01 08:00", "p_set_kw": 1.5},
            {"cluster_id": "1", "timestamp": "2024-01-01 08:15", "p_set_kw": 2.0},
        ]
    )
    file_path = excel_builder({"Setpoints": setpoints_df})

    commands = parse_stage2_setpoints_sheet(str(file_path))

    assert set(commands.keys()) == {"1", "2"}
    assert commands["1"].index[0] == pd.Timestamp("2024-01-01 08:00")
    assert commands["1"].iloc[1] == pytest.approx(2.0)
    assert commands["2"].iloc[0] == pytest.approx(5.0)


def test_parse_stage2_setpoints_sheet_rejects_duplicates(excel_builder):
    setpoints_df = pd.DataFrame(
        [
            {"cluster_id": "1", "timestamp": "2024-01-01 08:00", "p_set_kw": 1.0},
            {"cluster_id": "1", "timestamp": "2024-01-01 08:00", "p_set_kw": 2.0},
        ]
    )
    file_path = excel_builder({"Setpoints": setpoints_df})

    with pytest.raises(ValueError, match="Duplicate setpoint row"):
        parse_stage2_setpoints_sheet(str(file_path))


def test_parse_stage2_setpoints_sheet_normalizes_integral_cluster_id(excel_builder):
    setpoints_df = pd.DataFrame(
        [
            {"cluster_id": 1.0, "timestamp": "2024-01-01 08:00", "p_set_kw": 1.0},
            {"cluster_id": 1.0, "timestamp": "2024-01-01 08:15", "p_set_kw": 1.5},
        ]
    )
    file_path = excel_builder({"Setpoints": setpoints_df})

    commands = parse_stage2_setpoints_sheet(str(file_path))
    assert set(commands.keys()) == {"1"}


def test_parse_stage2_setpoints_sheet_parses_flex_band_payload(excel_builder):
    setpoints_df = pd.DataFrame(
        [
            {
                "cluster_id": "1",
                "timestamp": "2024-01-01 08:00",
                "p_min_kw": -2.0,
                "p_max_kw": 1.0,
            },
            {
                "cluster_id": "1",
                "timestamp": "2024-01-01 08:15",
                "p_min_kw": -1.5,
                "p_max_kw": 2.0,
            },
        ]
    )
    file_path = excel_builder({"Setpoints": setpoints_df})

    commands = parse_stage2_setpoints_sheet(
        str(file_path),
        command_type="flex_band",
    )

    assert set(commands.keys()) == {"1"}
    df = commands["1"]
    assert list(df.columns) == ["p_min_kw", "p_max_kw"]
    assert df.iloc[0]["p_min_kw"] == pytest.approx(-2.0)
    assert df.iloc[1]["p_max_kw"] == pytest.approx(2.0)


def test_parse_stage2_setpoints_sheet_rejects_p_set_for_flex_band(excel_builder):
    setpoints_df = pd.DataFrame(
        [
            {
                "cluster_id": "1",
                "timestamp": "2024-01-01 08:00",
                "p_min_kw": -2.0,
                "p_max_kw": 1.0,
                "p_set_kw": 0.0,
            }
        ]
    )
    file_path = excel_builder({"Setpoints": setpoints_df})

    with pytest.raises(ValueError, match="must not include 'p_set_kw'"):
        parse_stage2_setpoints_sheet(str(file_path), command_type="flex_band")
