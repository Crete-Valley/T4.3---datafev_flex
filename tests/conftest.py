import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class DummySolver:
    """Deterministic stand-in for a Pyomo solver used in tests."""

    def __init__(self, power_assignments=None, soc_assignments=None):
        self.power_assignments = power_assignments or {}
        self.soc_assignments = soc_assignments or {}
        self.solve_called = False

    def solve(self, model, tee=False):
        self.solve_called = True

        times = sorted(int(t) for t in model.T)
        vehicles = sorted(str(v) for v in model.V)
        arrivals = {v: int(model.t_arr[v]) if hasattr(model, "t_arr") else 0 for v in vehicles}
        departures = {v: int(model.t_dep[v]) if hasattr(model, "t_dep") else max(times) + 1 for v in vehicles}

        for t in times:
            assigned = self.power_assignments.get(t, {})
            for v in vehicles:
                if t < arrivals[v] or t >= departures[v]:
                    power = 0.0
                else:
                    power = assigned.get(v, 0.0)
                model.p_ev_pos[v, t].set_value(power)
                model.p_ev_neg[v, t].set_value(0.0)

        for v in vehicles:
            base_soc = float(model.s_ini[v])
            for tp in sorted(int(tp) for tp in model.Tp):
                if tp <= arrivals[v]:
                    soc_value = base_soc
                else:
                    override = self.soc_assignments.get(tp, {}).get(v)
                    soc_value = override if override is not None else base_soc + 0.01 * tp
                model.s[v, tp].set_value(soc_value)

        for t in times:
            total = sum(model.p_ev_pos[v, t].value - model.p_ev_neg[v, t].value for v in vehicles)
            model.p_cc[t].set_value(total)


@pytest.fixture
def fake_solver():
    """Factory returning a deterministic fake solver."""

    def _factory(power_assignments=None, soc_assignments=None):
        return DummySolver(power_assignments, soc_assignments)

    return _factory


@pytest.fixture
def excel_builder(tmp_path):
    import pandas as pd

    """Helper to create Excel files with arbitrary sheet definitions."""

    def _builder(sheet_map):
        path = tmp_path / f"input_{len(list(tmp_path.iterdir()))}.xlsx"
        with pd.ExcelWriter(path) as writer:
            for sheet, df in sheet_map.items():
                df.to_excel(writer, sheet_name=sheet, index=False)
        return path

    return _builder


@pytest.fixture
def sample_vehicle_parameters():
    """Provide a coherent set of EV parameters for model construction."""

    vehicles = ["EV1", "EV2"]
    return {
        "opt_step": 900,
        "opt_horizon": list(range(0, 4)),
        "bcap": {"EV1": 50.0, "EV2": 60.0},
        "inisoc": {"EV1": 0.4, "EV2": 0.5},
        "tarsoc": {"EV1": 0.9, "EV2": 0.85},
        "minsoc": {"EV1": 0.2, "EV2": 0.25},
        "maxsoc": {"EV1": 1.0, "EV2": 1.0},
        "ch_eff": {"EV1": 0.95, "EV2": 0.96},
        "ds_eff": {"EV1": 0.9, "EV2": 0.92},
        "pmax_pos": {"EV1": 7.2, "EV2": 11.0},
        "pmax_neg": {"EV1": 3.0, "EV2": 3.0},
        "deptime": {"EV1": 2, "EV2": 3},
    }
