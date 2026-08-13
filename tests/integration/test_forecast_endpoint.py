import httpx


def test_compute_forecasts():

    response = httpx.post(
        "http://localhost:8000/v1/compute_forecasts",
        json={"planning_start": "2022-01-08 07:00:00", "planning_end": "2022-01-08 13:00:00", "time_step_minutes": 15},
    )

    assert response.status_code == 200