import pytest

from algorithms.scheduling.flex_aware_scheduling import (
    _build_flex_aware_model,
    compute_flex_aware_schedule,
)


def _setpoint_from_horizon(opt_horizon):
    return {int(t): 1.0 for t in list(opt_horizon)[:-1]}


def test_build_flex_aware_model_defaults_to_target_soc(sample_vehicle_parameters):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        use_tarsoc=None,
    )

    for vehicle in params["bcap"]:
        assert int(model.use_target[vehicle]) == 1


def test_build_flex_aware_model_accepts_custom_target_flags(sample_vehicle_parameters):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])
    flags = {"EV1": 0, "EV2": 1}

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        use_tarsoc=flags,
    )

    assert int(model.use_target["EV1"]) == 0
    assert int(model.use_target["EV2"]) == 1


def test_build_flex_aware_model_exact_target_flag_creates_eq_constraint(sample_vehicle_parameters):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])
    use_tarsoc = {"EV1": 1, "EV2": 1}
    use_exact_tarsoc = {"EV1": 1, "EV2": 0}

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        use_tarsoc=use_tarsoc,
        use_exact_tarsoc=use_exact_tarsoc,
    )

    # EV1 uses equality, EV2 uses lower-bound target.
    assert "EV1" in model.dep_soc_eq
    assert "EV2" not in model.dep_soc_eq
    assert "EV2" in model.dep_soc_ge


def test_build_flex_aware_model_best_effort_adds_deviation_variables(sample_vehicle_parameters):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        tracking_mode="best_effort",
    )

    assert hasattr(model, "dev_pos")
    assert hasattr(model, "dev_neg")
    assert hasattr(model, "track_abs_error")
    assert not hasattr(model, "track_lower_bound")
    assert not hasattr(model, "track_upper_bound")


def test_compute_flex_aware_schedule_relays_solver_outputs(
    fake_solver,
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])
    power_assignments = {
        0: {"EV1": 2.0, "EV2": 1.0},
        1: {"EV1": 1.5, "EV2": 0.5},
        2: {"EV1": 0.0, "EV2": 0.0},
    }
    soc_assignments = {
        0: {"EV1": 0.4, "EV2": 0.5},
        1: {"EV1": 0.45, "EV2": 0.55},
        2: {"EV1": 0.5, "EV2": 0.6},
        3: {"EV1": 0.55, "EV2": 0.65},
    }
    solver = fake_solver(power_assignments=power_assignments, soc_assignments=soc_assignments)

    p_ev, s_values, p_cluster = compute_flex_aware_schedule(
        solver=solver,
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        use_exact_tarsoc={"EV1": 0, "EV2": 0},
    )

    assert solver.solve_called
    assert p_ev[0]["EV1"] == pytest.approx(2.0)
    assert p_ev[1]["EV2"] == pytest.approx(0.5)
    assert s_values[3]["EV2"] == pytest.approx(0.65)
    assert p_cluster[0] == pytest.approx(3.0)
    assert p_cluster[1] == pytest.approx(2.0)


def test_compute_flex_aware_schedule_supports_best_effort_mode(
    fake_solver,
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])
    solver = fake_solver(
        power_assignments={0: {"EV1": 1.5, "EV2": 0.5}, 1: {"EV1": 1.0, "EV2": 0.5}},
        soc_assignments={
            0: {"EV1": 0.4, "EV2": 0.5},
            1: {"EV1": 0.45, "EV2": 0.55},
            2: {"EV1": 0.5, "EV2": 0.6},
            3: {"EV1": 0.55, "EV2": 0.65},
        },
    )

    p_ev, s_values, p_cluster = compute_flex_aware_schedule(
        solver=solver,
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        setpoint=setpoint,
        bcap=params["bcap"],
        inisoc=params["inisoc"],
        tarsoc=params["tarsoc"],
        minsoc=params["minsoc"],
        maxsoc=params["maxsoc"],
        ch_eff=params["ch_eff"],
        ds_eff=params["ds_eff"],
        pmax_pos=params["pmax_pos"],
        pmax_neg=params["pmax_neg"],
        deptime=params["deptime"],
        tracking_mode="best_effort",
    )

    assert solver.solve_called
    assert p_ev[0]["EV1"] == pytest.approx(1.5)
    assert s_values[3]["EV2"] == pytest.approx(0.65)
    assert p_cluster[0] == pytest.approx(2.0)
