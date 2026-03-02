import pandas as pd
from pandas.testing import assert_frame_equal

from data_handling.cluster import ChargerCluster
from data_handling.multi_cluster import MultiClusterSystem


def _make_cluster(cluster_id="1"):
    topology = pd.DataFrame(
        {
            "cu_id": ["CU1"],
            "cu_p_ch_max (kW)": [11],
            "cu_p_ds_max (kW)": [11],
            "cu_eff": [0.95],
        }
    )
    return ChargerCluster(cluster_id, topology)


def test_set_capability_persists_summary_timeseries_and_forecast():
    cluster = _make_cluster("10")
    summary = {"downward_capability_kWh": 12.5, "upward_capability_kWh": 3.2}
    timeseries = pd.DataFrame(
        {
            "downward_capability_kW": [5.0, 4.0],
            "upward_capability_kW": [1.0, 2.0],
        },
        index=pd.date_range("2024-01-01 08:00", periods=2, freq="15min"),
    )
    forecast = pd.Series([2, 1], index=timeseries.index)

    cluster.set_capability(summary=summary, timeseries=timeseries, forecast_connected_evs_ts=forecast)

    assert cluster.capability_summary == summary
    assert_frame_equal(cluster.capability_timeseries, timeseries)
    assert cluster.forecast_connected_evs_ts.equals(forecast)


def test_multicluster_getters_only_return_populated_clusters():
    cluster_with_data = _make_cluster("1")
    cluster_without_data = _make_cluster("2")

    ts = pd.DataFrame(
        {"downward_capability_kW": [1], "upward_capability_kW": [0.5]},
        index=pd.date_range("2024-01-01 00:00", periods=1, freq="h"),
    )
    forecast = pd.Series([1], index=ts.index)
    summary = {"downward_capability_kWh": 0.25, "upward_capability_kWh": 0.1}
    cluster_with_data.set_capability(summary, ts, forecast_connected_evs_ts=forecast)

    system = MultiClusterSystem("mc")
    system.add_cc(cluster_with_data)
    system.add_cc(cluster_without_data)

    summaries = system.get_capability_summary()
    timeseries = system.get_capability_timeseries()
    forecasts = system.get_connected_evs_timeseries()

    assert summaries == {"1": summary}
    assert list(timeseries.keys()) == ["1"]
    assert_frame_equal(timeseries["1"], ts)
    assert forecasts["1"].equals(forecast)
    assert "2" not in forecasts
