import pytest

from database.db_models import Cluster, ClusterForecast, Fleet
from database.db_transactions import (
    fetch_cluster_forecast,
    insert_cluster,
    insert_cluster_forecast,
    insert_fleet,
)


@pytest.mark.integration
def test_create_cluster_forecast():
    forecast = ClusterForecast(
        cluster_id=1,
        timestamp="2024-01-01 08:00:00",
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
        timestamp="2024-01-01 08:00:00",
        downward_capability_kW=3.0,
        upward_capability_kW=2.0,
        connected_evs=5,
        cluster_power_kW=1.5
    )
    insert_cluster_forecast(forecast)

    rows = fetch_cluster_forecast()
    assert len(rows) > 0


@pytest.mark.integration
def test_insert_cluster_record():
    cluster = Cluster(
        cluster_id=2,
        cu_id="cu-2",
        p_max_ch_kW=22.0,
        p_max_ds_kW=18.0,
        efficiency=0.95,
    )
    insert_cluster(cluster)


@pytest.mark.integration
def test_insert_fleet_record():
    vehicle = Fleet(
        vehicle_id="veh-1",
        battery_capacity_kWh=60.0,
        arrival_time="2024-01-01 08:00:00",
        departure_time="2024-01-01 17:00:00",
        initial_soc=0.2,
        target_soc=0.8,
        use_target_soc=True,
        min_allowed_soc=0.1,
        max_allowed_soc=0.95,
        target_cluster="cluster-2",
        p_max_charge_kW=11.0,
        p_max_discharge_kW=10.0,
        exact_target_soc=False,
    )
    insert_fleet(vehicle)


