import httpx
from database.db_models import ClusterForecast, ChargingSchedule


EXPECTED_CLUSTER_FORECASTS = [
    ClusterForecast(
        cluster_id=1,
        timestamp="2022-01-08 07:00:00",
        downward_capability_kW=0.0,
        upward_capability_kW=11.0,
        connected_evs=1,
        cluster_power_kW=0.0,
    ),
    ClusterForecast(
        cluster_id=1,
        timestamp="2022-01-08 07:15:00",
        downward_capability_kW=0.0,
        upward_capability_kW=22,
        connected_evs=2,
        cluster_power_kW=0.0,
    ),
    ClusterForecast(
        cluster_id=1,
        timestamp="2022-01-08 07:30:00",
        downward_capability_kW=0.0,
        upward_capability_kW=33,
        connected_evs=3,
        cluster_power_kW=0.0,
    ),
    ClusterForecast(
        cluster_id=1,
        timestamp="2022-01-08 07:45:00",
        downward_capability_kW=0.0,
        upward_capability_kW=33,
        connected_evs=3,
        cluster_power_kW=0.0,
    ),
    ClusterForecast(
        cluster_id=1,
        timestamp="2022-01-08 08:00:00",
        downward_capability_kW=11.0,
        upward_capability_kW=33,
        connected_evs=3,
        cluster_power_kW=0.0,
    )
]

EXPECTED_CHARGING_SCHEDULES = [
    ChargingSchedule(
        vehicle_id="v01",
        cluster_id="1",
        arrival_time_ts="2022-01-08 07:00:00",
        departure_time_ts="2022-01-08 11:45:00",
        initial_soc=0.4,
        target_soc=0.5,
        scheduled_departure_soc=0.5,
        charged_energy_kWh=5.5,
        total_charging_cost_eur=0.5775,
    ),
    ChargingSchedule(
        vehicle_id="v02",
        cluster_id="1",
        arrival_time_ts="2022-01-08 07:15:00",
        departure_time_ts="2022-01-08 11:45:00",
        initial_soc=0.4,
        target_soc=0.5,
        scheduled_departure_soc=0.5,
        charged_energy_kWh=5.5,
        total_charging_cost_eur=0.5775,
    ),
    ChargingSchedule(
        vehicle_id="v03",
        cluster_id="1",
        arrival_time_ts="2022-01-08 07:30:00",
        departure_time_ts="2022-01-08 11:00:00",
        initial_soc=0.4,
        target_soc=0.5,
        scheduled_departure_soc=0.5,
        charged_energy_kWh=5.5,
        total_charging_cost_eur=0.5775,
    ),
    ChargingSchedule(
        vehicle_id="v04",
        cluster_id="2",
        arrival_time_ts="2022-01-08 07:30:00",
        departure_time_ts="2022-01-08 11:45:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11,
        total_charging_cost_eur=1.21,
    ),
    ChargingSchedule(
        vehicle_id="v05",
        cluster_id="2",
        arrival_time_ts="2022-01-08 07:45:00",
        departure_time_ts="2022-01-08 11:45:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11.0,
        total_charging_cost_eur=1.21,
    ),
    ChargingSchedule(
        vehicle_id="v06",
        cluster_id="2",
        arrival_time_ts="2022-01-08 08:00:00",
        departure_time_ts="2022-01-08 11:45:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11.0,
        total_charging_cost_eur=1.21,
    ),
    ChargingSchedule(
        vehicle_id="v07",
        cluster_id="3",
        arrival_time_ts="2022-01-08 08:00:00",
        departure_time_ts="2022-01-08 12:00:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11.0,
        total_charging_cost_eur=1.21,
    ),
    ChargingSchedule(
        vehicle_id="v08",
        cluster_id="3",
        arrival_time_ts="2022-01-08 08:15:00",
        departure_time_ts="2022-01-08 12:00:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11.0,
        total_charging_cost_eur=1.21,
    ),
    ChargingSchedule(
        vehicle_id="v09",
        cluster_id="3",
        arrival_time_ts="2022-01-08 08:15:00",
        departure_time_ts="2022-01-08 12:00:00",
        initial_soc=0.4,
        target_soc=0.6,
        scheduled_departure_soc=0.6,
        charged_energy_kWh=11.0,
        total_charging_cost_eur=1.21,
    )
]


def test_compute_forecasts_assert_outputs_exist():
    response = httpx.post(
        "http://localhost:8000/v1/compute_forecasts",
        json={"planning_start": "2022-01-08 07:00:00", "planning_end": "2022-01-08 13:00:00", "time_step_minutes": 15},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["cluster_forecasts"]
    assert body["charging_schedules"]
    assert {
        "cluster_id",
        "timestamp",
        "downward_capability_kW",
        "upward_capability_kW",
        "connected_evs",
        "cluster_power_kW",
    } <= body["cluster_forecasts"][0].keys()
    assert {
        "vehicle_id",
        "cluster_id",
        "arrival_time",
        "departure_time",
        "initial_soc",
        "target_soc",
        "scheduled_departure_soc",
        "charged_energy_kWh",
        "total_charging_cost_eur",
    } <= body["charging_schedules"][0].keys()


def compare_timestamps(timestamp, expected_timestamp):
    from datetime import datetime

    fmt = "%Y-%m-%dT%H:%M:%S"
    fmt_expected = "%Y-%m-%d %H:%M:%S"
    dt1 = datetime.strptime(timestamp, fmt)
    dt2 = datetime.strptime(expected_timestamp, fmt_expected)
    return dt1 == dt2


def test_compute_forecasts_check_response():
    response = httpx.post(
        "http://localhost:8000/v1/compute_forecasts",
        json={"planning_start": "2022-01-08 07:00:00", "planning_end": "2022-01-08 13:00:00", "time_step_minutes": 15},
    )

    assert response.status_code == 200
    body = response.json()

    forecasts = body["cluster_forecasts"]
    assert forecasts is not None

    epsilon = 1e-6
    counter = 0
    for expected_forecast in EXPECTED_CLUSTER_FORECASTS:
        assert forecasts[counter]["cluster_id"] == expected_forecast.cluster_id
        assert compare_timestamps(forecasts[counter]["timestamp"], expected_forecast.timestamp)
        assert abs(forecasts[counter]["downward_capability_kW"] - expected_forecast.downward_capability_kW) < epsilon
        assert abs(forecasts[counter]["upward_capability_kW"] - expected_forecast.upward_capability_kW) < epsilon
        assert forecasts[counter]["connected_evs"] == expected_forecast.connected_evs
        assert abs(forecasts[counter]["cluster_power_kW"] - expected_forecast.cluster_power_kW) < epsilon
        counter += 1

    schedules = body["charging_schedules"]
    assert schedules is not None

    counter = 0
    for expected_schedule in EXPECTED_CHARGING_SCHEDULES:
        assert schedules[counter]["vehicle_id"] == expected_schedule.vehicle_id
        assert schedules[counter]["cluster_id"] == expected_schedule.cluster_id
        assert compare_timestamps(schedules[counter]["arrival_time"], expected_schedule.arrival_time_ts)
        assert compare_timestamps(schedules[counter]["departure_time"], expected_schedule.departure_time_ts)
        assert schedules[counter]["initial_soc"] == expected_schedule.initial_soc
        assert schedules[counter]["target_soc"] == expected_schedule.target_soc
        assert schedules[counter]["scheduled_departure_soc"] == expected_schedule.scheduled_departure_soc
        assert abs(schedules[counter]["charged_energy_kWh"] - expected_schedule.charged_energy_kWh) < epsilon
        abs(schedules[counter]["total_charging_cost_eur"] - expected_schedule.total_charging_cost_eur) < epsilon
        counter += 1


#def test_compute_forecasts_shorter_schedule():
#
#    response = httpx.post(
#        "http://localhost:8000/v1/compute_forecasts",
#        json={"planning_start": "2022-01-08 08:00:00", "planning_end": "2022-01-08 10:00:00", "time_step_minutes": 15},
#    )
#    assert response.status_code == 200
