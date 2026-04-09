import pandas as pd
from pandas.testing import assert_frame_equal

from utils.output_utils import (
    export_capability_timeseries,
    export_day_ahead_schedule_results,
    export_stage2_results,
)


def test_export_capability_timeseries_injects_forecast_into_csv(tmp_path):
    df = pd.DataFrame(
        {
            "downward_capability_kW": [2.0, 1.5],
            "upward_capability_kW": [0.5, 0.2],
        },
        index=pd.date_range("2024-01-01 08:00", periods=2, freq="15min"),
    )
    forecast = pd.Series([1, 0], index=df.index)

    export_capability_timeseries(
        {"1": df},
        base_path=tmp_path,
        enabled=True,
        export_format="csv",
        forecast_connected_evs_ts={"1": forecast},
    )

    exported = pd.read_csv(tmp_path / "cluster_capability_1.csv", index_col=0, parse_dates=True)
    expected = df.copy()
    expected["connected_evs"] = forecast
    assert_frame_equal(exported, expected, check_freq=False)


def test_export_capability_timeseries_writes_xlsx_with_forecast(tmp_path):
    df = pd.DataFrame(
        {
            "downward_capability_kW": [3.0],
            "upward_capability_kW": [0.0],
        },
        index=pd.date_range("2024-01-01 09:00", periods=1, freq="15min"),
    )
    forecast = pd.Series([2], index=df.index)

    export_capability_timeseries(
        {"7": df},
        base_path=tmp_path,
        enabled=True,
        export_format="xlsx",
        forecast_connected_evs_ts={"7": forecast},
    )

    workbook_path = tmp_path / "cluster_capability_timeseries.xlsx"
    assert workbook_path.exists()

    exported = pd.read_excel(workbook_path, sheet_name="Cluster_7", index_col=0)
    expected = df.copy()
    expected["connected_evs"] = forecast
    # Excel export loses freq info; align columns only
    assert_frame_equal(exported, expected, check_freq=False, check_dtype=False)


def test_export_capability_timeseries_writes_cluster_power_column(tmp_path):
    idx = pd.date_range("2024-01-01 09:00", periods=2, freq="15min")
    df = pd.DataFrame(
        {
            "downward_capability_kW": [3.0, 2.0],
            "upward_capability_kW": [0.0, 1.0],
        },
        index=idx,
    )
    cluster_power = pd.Series([1.5, 0.5], index=idx, dtype=float)

    export_capability_timeseries(
        {"7": df},
        base_path=tmp_path,
        enabled=True,
        export_format="xlsx",
        cluster_power_ts={"7": cluster_power},
    )

    exported = pd.read_excel(
        tmp_path / "cluster_capability_timeseries.xlsx",
        sheet_name="Cluster_7",
        index_col=0,
    )
    assert exported["cluster_power_kW"].tolist() == [1.5, 0.5]


def test_export_day_ahead_schedule_results_writes_xlsx(tmp_path):
    idx = pd.date_range("2024-01-01 08:00", periods=2, freq="15min")
    soc_idx = list(idx) + [idx[-1] + (idx[1] - idx[0])]
    price_ts = pd.Series([0.1, 0.2], index=idx, dtype=float)
    cluster_power = pd.Series([1.0, 0.5], index=idx, dtype=float)
    ev_summary = pd.DataFrame(
        [
            {
                "vehicle_id": "EV1",
                "cluster_id": "1",
                "charged_energy_kWh": 0.375,
                "total_charging_cost_eur": 0.0875,
            }
        ]
    )

    export_day_ahead_schedule_results(
        market_price_ts=price_ts,
        cluster_power_ts={"1": cluster_power},
        cluster_ev_power_ts={"1": pd.DataFrame({"EV1": [1.0, 0.5]}, index=idx)},
        cluster_ev_soc_ts={"1": pd.DataFrame({"EV1": [0.5, 0.55, 0.6]}, index=soc_idx)},
        ev_summary=ev_summary,
        base_path=tmp_path,
        enabled=True,
        export_format="xlsx",
    )

    workbook_path = tmp_path / "day_ahead_smart_charging_schedule.xlsx"
    assert workbook_path.exists()

    summary_df = pd.read_excel(workbook_path, sheet_name="total_charging_price")
    ev_power_df = pd.read_excel(workbook_path, sheet_name="EV1_power", index_col=0)
    ev_soc_df = pd.read_excel(workbook_path, sheet_name="EV1_soc", index_col=0)

    assert summary_df.loc[0, "vehicle_id"] == "EV1"
    assert str(summary_df.loc[0, "cluster_id"]) == "1"
    assert ev_power_df["power_kW"].tolist() == [1.0, 0.5]
    assert ev_soc_df["soc"].tolist() == [0.5, 0.55, 0.6]


def test_export_stage2_results_writes_tracking_report_csv(tmp_path):
    idx = pd.date_range("2024-01-01 08:00", periods=2, freq="15min")
    command_status = pd.DataFrame(
        [{"cluster_id": "1", "status": "accepted", "reason": "", "detail": "ok"}]
    )
    command_band = pd.DataFrame(
        {"p_min_kw": [0.0, 0.0], "p_max_kw": [2.0, 2.0], "p_set_kw": [1.0, 1.0]},
        index=idx,
    )
    cluster_power = pd.Series([1.0, 0.5], index=idx, dtype=float)
    tracking_report = pd.DataFrame(
        {
            "requested_setpoint_kw": [1.0, 1.0],
            "delivered_p_kw": [1.0, 0.5],
            "abs_error_kw": [0.0, 0.5],
            "is_met": [True, False],
        },
        index=idx,
    )

    export_stage2_results(
        command_status=command_status,
        cluster_command_band_ts={"1": command_band},
        cluster_setpoint_ts={"1": command_band["p_set_kw"]},
        cluster_power_ts={"1": cluster_power},
        cluster_ev_power_ts={"1": pd.DataFrame({"EV1": [1.0, 0.5]}, index=idx)},
        cluster_ev_soc_ts={"1": pd.DataFrame({"EV1": [0.5, 0.55, 0.6]}, index=list(idx) + [idx[-1] + (idx[1] - idx[0])])},
        cluster_tracking_report_ts={"1": tracking_report},
        base_path=tmp_path,
        enabled=True,
        export_format="csv",
    )

    exported = pd.read_csv(
        tmp_path / "stage2_cluster_1_tracking_report.csv",
        index_col=0,
    )
    assert list(exported.columns) == [
        "requested_setpoint_kw",
        "delivered_p_kw",
        "abs_error_kw",
        "is_met",
    ]
