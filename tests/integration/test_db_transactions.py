import pytest

from database.db_models import ClusterForecast
from database.db_transactions import fetch_cluster_forecast, insert_cluster_forecast



@pytest.mark.integration
def test_create_cluster_forecast():
    forecast = ClusterForecast(
        cluster_id=1,
        ts="2024-01-01 08:00:00",
        downward_capability_kW=3.0,
        upward_capability_kW=2.0,
        connected_evs=5,
        cluster_power_kW=1.5
    )
    insert_cluster_forecast(forecast)


@pytest.mark.integration
def test_fetch_cluster_forecast():
    forecast = ClusterForecast(
        cluster_id=1,
        ts="2024-01-01 08:00:00",
        downward_capability_kW=3.0,
        upward_capability_kW=2.0,
        connected_evs=5,
        cluster_power_kW=1.5
    )
    insert_cluster_forecast(forecast)

    rows = fetch_cluster_forecast()
    assert len(rows) > 0


