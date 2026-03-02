import pandas as pd

from utils.plotting_service import (
    plot_aggregate_capability,
    plot_cluster_capability_bands,
    plot_stage2_ev_soc_schedules,
)


def _sample_timeseries():
    index = pd.date_range("2024-01-01 08:00", periods=3, freq="15min")
    df = pd.DataFrame(
        {
            "downward_capability_kW": [1.0, 2.0, 3.0],
            "upward_capability_kW": [0.5, 1.0, 1.5],
        },
        index=index,
    )
    forecast = pd.Series([1, 2, 1], index=index, name="connected_evs")
    return df, forecast


def test_plot_cluster_capability_bands_creates_png_with_forecast(tmp_path):
    df, forecast = _sample_timeseries()

    plot_cluster_capability_bands(
        {"A": df},
        output_dir=tmp_path,
        forecast_connected_evs_ts={"A": forecast},
    )

    png_path = tmp_path / "cluster_A_capability.png"
    assert png_path.exists()
    assert png_path.stat().st_size > 0


def test_plot_aggregate_capability_sums_clusters(tmp_path):
    df, _ = _sample_timeseries()
    other = df * 2
    plot_aggregate_capability({"A": df, "B": other}, output_dir=tmp_path)

    png_path = tmp_path / "aggregate_capability.png"
    assert png_path.exists()
    assert png_path.stat().st_size > 0


def test_plot_stage2_ev_soc_schedules_creates_png(tmp_path):
    index = pd.date_range("2024-01-01 08:00", periods=4, freq="15min")
    soc_df = pd.DataFrame(
        {
            "EV_1": [0.30, 0.40, 0.50, 0.60],
            "EV_2": [0.45, 0.48, 0.52, 0.58],
        },
        index=index,
    )

    plot_stage2_ev_soc_schedules({"1": soc_df}, output_dir=tmp_path)

    png_path = tmp_path / "stage2_cluster_1_ev_soc.png"
    assert png_path.exists()
    assert png_path.stat().st_size > 0
