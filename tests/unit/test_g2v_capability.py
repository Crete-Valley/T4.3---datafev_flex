import pytest

from algorithms.capability.g2v_capability import _build_g2v_model, compute_g2v_capability


def test_build_g2v_model_defaults_to_using_target_soc(sample_vehicle_parameters):
    """When no instructions are provided all vehicles should enforce their target SOC."""
    params = sample_vehicle_parameters

    model = _build_g2v_model(
        params["opt_step"],
        params["opt_horizon"],
        params["bcap"],
        params["inisoc"],
        params["tarsoc"],
        params["minsoc"],
        params["maxsoc"],
        params["ch_eff"],
        params["ds_eff"],
        params["pmax_pos"],
        params["pmax_neg"],
        params["deptime"],
        None,
    )

    assert sorted(model.V) == sorted(params["bcap"].keys()), "Vehicle set should match input keys"
    assert model.deltaSec == params["opt_step"], "Time step parameter must be propagated"
    for vehicle in params["bcap"]:
        assert int(model.use_target[vehicle]) == 1, "Default target usage should be enabled"


def test_build_g2v_model_accepts_custom_use_target_flags(sample_vehicle_parameters):
    """Custom use_target instructions should be stored exactly as provided."""
    params = sample_vehicle_parameters
    flags = {"EV1": 0, "EV2": 1}

    model = _build_g2v_model(
        params["opt_step"],
        params["opt_horizon"],
        params["bcap"],
        params["inisoc"],
        params["tarsoc"],
        params["minsoc"],
        params["maxsoc"],
        params["ch_eff"],
        params["ds_eff"],
        params["pmax_pos"],
        params["pmax_neg"],
        params["deptime"],
        flags,
    )

    assert int(model.use_target["EV1"]) == 0
    assert int(model.use_target["EV2"]) == 1


def test_compute_g2v_capability_relays_solver_outputs(fake_solver, sample_vehicle_parameters):
    """The compute helper should simply expose the values returned by the solver."""
    params = sample_vehicle_parameters
    power_assignments = {
        0: {"EV1": 2.0, "EV2": 1.5},
        1: {"EV1": 1.0, "EV2": 0.5},
        2: {"EV1": 0.0, "EV2": 0.0},
    }
    soc_assignments = {
        0: {"EV1": 0.4, "EV2": 0.5},
        1: {"EV1": 0.5, "EV2": 0.55},
        2: {"EV1": 0.6, "EV2": 0.65},
        3: {"EV1": 0.7, "EV2": 0.75},
    }
    solver = fake_solver(power_assignments=power_assignments, soc_assignments=soc_assignments)

    p_ev_pos, s_values, p_cluster = compute_g2v_capability(
        solver,
        params["opt_step"],
        params["opt_horizon"],
        params["bcap"],
        params["inisoc"],
        params["tarsoc"],
        params["minsoc"],
        params["maxsoc"],
        params["ch_eff"],
        params["ds_eff"],
        params["pmax_pos"],
        params["pmax_neg"],
        params["deptime"],
    )

    assert solver.solve_called, "The provided solver must be invoked"
    assert p_ev_pos[0]["EV1"] == pytest.approx(2.0)
    assert p_ev_pos[1]["EV2"] == pytest.approx(0.5)
    assert s_values[3]["EV2"] == pytest.approx(0.75)
    assert p_cluster[0] == pytest.approx(3.5), "Cluster power should sum EV contributions"
    assert p_cluster[1] == pytest.approx(1.5)
