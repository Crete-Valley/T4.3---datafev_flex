import pytest

from algorithms.scheduling.day_ahead_smart_charging import (
    _build_day_ahead_model,
    compute_day_ahead_smart_charging_schedule,
)


def _price_profile(opt_horizon):
    return {int(t): 0.1 + 0.01 * int(t) for t in list(opt_horizon)[:-1]}


def test_build_day_ahead_model_sets_price_parameter(sample_vehicle_parameters):
    params = sample_vehicle_parameters
    prices = _price_profile(params["opt_horizon"])

    model = _build_day_ahead_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        prices=prices,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        pmax_pos=params["pmax_pos"],
        deptime=params["deptime"],
    )

    assert float(model.price[0]) == pytest.approx(0.1)
    assert hasattr(model, "dep_soc_ge")
    assert hasattr(model, "obj")


def test_compute_day_ahead_smart_charging_schedule_relays_solver_outputs(
    fake_solver,
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    prices = _price_profile(params["opt_horizon"])
    solver = fake_solver(
        power_assignments={
            0: {"EV1": 2.0, "EV2": 1.0},
            1: {"EV1": 0.5, "EV2": 0.0},
            2: {"EV1": 0.0, "EV2": 0.0},
        },
        soc_assignments={
            0: {"EV1": 0.4, "EV2": 0.5},
            1: {"EV1": 0.43, "EV2": 0.52},
            2: {"EV1": 0.45, "EV2": 0.54},
            3: {"EV1": 0.45, "EV2": 0.54},
        },
    )

    p_ev, s_values, p_cluster = compute_day_ahead_smart_charging_schedule(
        solver=solver,
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        prices=prices,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        pmax_pos=params["pmax_pos"],
        deptime=params["deptime"],
    )

    assert solver.solve_called
    assert p_ev[0]["EV1"] == pytest.approx(2.0)
    assert p_ev[1]["EV1"] == pytest.approx(0.5)
    assert s_values[2]["EV2"] == pytest.approx(0.54)
    assert p_cluster[0] == pytest.approx(3.0)
    assert p_cluster[1] == pytest.approx(0.5)
