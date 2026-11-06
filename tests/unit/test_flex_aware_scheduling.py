import pytest
from pyomo.environ import value

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


def test_build_flex_aware_model_flex_band_strict_uses_price_aware_objective(
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    prices = {0: 0.10, 1: 0.20, 2: 0.30}

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        prices=prices,
        p_min={0: 0.0, 1: 0.0, 2: 0.0},
        p_max={0: 10.0, 1: 10.0, 2: 10.0},
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
        tracking_mode="strict",
        cycling_penalty_weight=1e-6,
    )

    for t in model.T:
        for vehicle in model.V:
            model.p_ev_pos[vehicle, t].set_value(1.0)
            model.p_ev_neg[vehicle, t].set_value(0.0)

    expected_cost = 2 * 0.25 * (0.10 + 0.20 + 0.30)
    expected_cycling = 1e-6 * 6.0
    assert value(model.obj.expr) == pytest.approx(expected_cost + expected_cycling)


def test_build_flex_aware_model_absolute_strict_keeps_cycling_objective(
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    prices = {0: 10.0, 1: 20.0, 2: 30.0}
    setpoint = _setpoint_from_horizon(params["opt_horizon"])

    model = _build_flex_aware_model(
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        prices=prices,
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
        tracking_mode="strict",
    )

    for t in model.T:
        for vehicle in model.V:
            model.p_ev_pos[vehicle, t].set_value(1.0)
            model.p_ev_neg[vehicle, t].set_value(0.25)

    assert value(model.obj.expr) == pytest.approx(7.5)


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


def test_compute_flex_aware_schedule_best_effort_solves_lexicographically(
    sample_vehicle_parameters,
):
    params = sample_vehicle_parameters
    setpoint = _setpoint_from_horizon(params["opt_horizon"])
    prices = {0: 0.10, 1: 0.05, 2: 0.20}

    class TwoPhaseSolver:
        def __init__(self):
            self.solve_calls = 0

        def solve(self, model, tee=False):
            del tee
            self.solve_calls += 1
            phase_power = 0.5 if self.solve_calls == 1 else 1.0

            for t in model.T:
                for vehicle in model.V:
                    model.p_ev_pos[vehicle, t].set_value(
                        phase_power if vehicle == "EV1" else 0.0
                    )
                    model.p_ev_neg[vehicle, t].set_value(0.0)
                model.p_cc[t].set_value(phase_power)

            for vehicle in model.V:
                base_soc = float(model.s_ini[vehicle])
                for tp in model.Tp:
                    model.s[vehicle, tp].set_value(base_soc)

            return None

    solver = TwoPhaseSolver()

    p_ev, s_values, p_cluster = compute_flex_aware_schedule(
        solver=solver,
        opt_step=params["opt_step"],
        opt_horizon=params["opt_horizon"],
        prices=prices,
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

    assert solver.solve_calls == 2
    assert p_ev[0]["EV1"] == pytest.approx(1.0)
    assert s_values[3]["EV2"] == pytest.approx(0.5)
    assert p_cluster[2] == pytest.approx(1.0)


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
