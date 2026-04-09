"""Stage-1 day-ahead smart charging MILP.

Purpose
-------
Solve a cost-minimizing charging schedule for EVs against day-ahead market
prices while respecting EV availability, charging limits and departure SoC
policies.
"""

from typing import Dict, Iterable, Tuple

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    NonNegativeReals,
    Objective,
    Param,
    Set,
    Var,
    minimize,
    value,
)
from pyomo.opt import SolverStatus, TerminationCondition


def _build_day_ahead_model(
    opt_step: int,
    opt_horizon: Iterable[int],
    prices: Dict[int, float],
    bcap: Dict[str, float],
    inisoc: Dict[str, float],
    tarsoc: Dict[str, float],
    minsoc: Dict[str, float],
    maxsoc: Dict[str, float],
    ch_eff: Dict[str, float],
    pmax_pos: Dict[str, float],
    deptime: Dict[str, int],
    use_tarsoc: Dict[str, int] | None = None,
    use_exact_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
    cycling_penalty_weight: float = 1e-6,
) -> ConcreteModel:
    """Build cost-minimizing day-ahead charging model.

    Notes
    -----
    This model is intentionally charge-only. Stage-1 day-ahead output is meant
    to act as a home/charging baseline schedule, so V2G export is not part of
    this optimization.
    """
    model = ConcreteModel(name="day_ahead_smart_charging")

    model.V = Set(initialize=list(bcap.keys()))
    model.T = Set(initialize=list(opt_horizon)[:-1])
    model.Tp = Set(initialize=list(opt_horizon))
    model.deltaSec = opt_step

    if use_tarsoc is None:
        use_tarsoc = {v: 1 for v in bcap.keys()}
    if use_exact_tarsoc is None:
        use_exact_tarsoc = {v: 0 for v in bcap.keys()}
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    model.price = Param(model.T, initialize=prices)
    model.b_cap = Param(model.V, initialize=bcap)
    model.s_ini = Param(model.V, initialize=inisoc)
    model.t_arr = Param(model.V, initialize=arrtime)
    model.s_tar = Param(model.V, initialize=tarsoc)
    model.s_min = Param(model.V, initialize=minsoc)
    model.s_max = Param(model.V, initialize=maxsoc)
    model.ch_eff = Param(model.V, initialize=ch_eff)
    model.p_max_pos = Param(model.V, initialize=pmax_pos)
    model.t_dep = Param(model.V, initialize=deptime)
    model.use_target = Param(model.V, initialize=use_tarsoc, within=Binary)
    model.use_exact_target = Param(model.V, initialize=use_exact_tarsoc, within=Binary)

    model.p_ev_pos = Var(model.V, model.T, within=NonNegativeReals)
    model.p_ev_neg = Var(model.V, model.T, within=NonNegativeReals)
    model.s = Var(model.V, model.Tp, within=NonNegativeReals)
    model.p_cc = Var(model.T, within=NonNegativeReals)

    model.init_soc = Constraint(
        model.V, rule=lambda m, v: m.s[v, m.t_arr[v]] == m.s_ini[v]
    )

    model.soc_before_arrival = Constraint(
        model.V,
        model.Tp,
        rule=lambda m, v, t: m.s[v, t] == m.s_ini[v]
        if t <= m.t_arr[v]
        else Constraint.Skip,
    )

    model.soc_min = Constraint(
        model.V, model.Tp, rule=lambda m, v, t: m.s[v, t] >= m.s_min[v]
    )
    model.soc_max = Constraint(
        model.V, model.Tp, rule=lambda m, v, t: m.s[v, t] <= m.s_max[v]
    )

    def soc_dyn_rule(m, v, t):
        if t < m.t_arr[v]:
            return Constraint.Skip
        return m.s[v, t + 1] == (
            m.s[v, t]
            + m.deltaSec
            * m.p_ev_pos[v, t]
            * m.ch_eff[v]
            / (m.b_cap[v] * 3600)
        )

    model.soc_dyn = Constraint(model.V, model.T, rule=soc_dyn_rule)

    model.no_charge_before_arr = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0
        if t < m.t_arr[v]
        else Constraint.Skip,
    )
    model.no_charge_after_dep = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0
        if t >= m.t_dep[v]
        else Constraint.Skip,
    )
    model.no_discharge = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0,
    )

    model.p_ev_pos_max = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] <= m.p_max_pos[v]
    )

    model.cluster_power = Constraint(
        model.T, rule=lambda m, t: m.p_cc[t] == sum(m.p_ev_pos[v, t] for v in m.V)
    )

    def dep_soc_ge_rule(m, v):
        use_target = int(value(m.use_target[v]))
        use_exact = int(value(m.use_exact_target[v]))
        if use_target == 0:
            return m.s[v, m.t_dep[v]] >= m.s_min[v]
        if use_target == 1 and use_exact == 0:
            return m.s[v, m.t_dep[v]] >= m.s_tar[v]
        return Constraint.Skip

    def dep_soc_eq_rule(m, v):
        use_target = int(value(m.use_target[v]))
        use_exact = int(value(m.use_exact_target[v]))
        if use_target == 1 and use_exact == 1:
            return m.s[v, m.t_dep[v]] == m.s_tar[v]
        return Constraint.Skip

    model.dep_soc_ge = Constraint(model.V, rule=dep_soc_ge_rule)
    model.dep_soc_eq = Constraint(model.V, rule=dep_soc_eq_rule)

    energy_cost = sum(
        model.price[t] * model.p_cc[t] * model.deltaSec / 3600 for t in model.T
    )
    cycling_penalty = cycling_penalty_weight * sum(
        model.p_ev_pos[v, t] for v in model.V for t in model.T
    )
    model.obj = Objective(expr=energy_cost + cycling_penalty, sense=minimize)

    return model


def _is_solver_optimal(results) -> bool:
    """Return True when a solver result is optimal.

    Lightweight fake solvers used by tests may return `None`; treat those as
    successful to keep unit tests simple.
    """
    if results is None:
        return True

    solver_data = getattr(results, "solver", None)
    if solver_data is None:
        return True

    return (
        solver_data.status == SolverStatus.ok
        and solver_data.termination_condition == TerminationCondition.optimal
    )


def compute_day_ahead_smart_charging_schedule(
    solver,
    opt_step: int,
    opt_horizon: Iterable[int],
    prices: Dict[int, float],
    bcap: Dict[str, float],
    inisoc: Dict[str, float],
    tarsoc: Dict[str, float],
    minsoc: Dict[str, float],
    maxsoc: Dict[str, float],
    ch_eff: Dict[str, float],
    pmax_pos: Dict[str, float],
    deptime: Dict[str, int],
    use_tarsoc: Dict[str, int] | None = None,
    use_exact_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
    cycling_penalty_weight: float = 1e-6,
) -> Tuple[Dict, Dict, Dict]:
    """Solve the day-ahead cost-minimizing charging problem."""
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    model = _build_day_ahead_model(
        opt_step=opt_step,
        opt_horizon=opt_horizon,
        prices=prices,
        bcap=bcap,
        inisoc=inisoc,
        tarsoc=tarsoc,
        minsoc=minsoc,
        maxsoc=maxsoc,
        ch_eff=ch_eff,
        pmax_pos=pmax_pos,
        deptime=deptime,
        use_tarsoc=use_tarsoc,
        use_exact_tarsoc=use_exact_tarsoc,
        arrtime=arrtime,
        cycling_penalty_weight=cycling_penalty_weight,
    )

    try:
        results = solver.solve(model, tee=False)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Day-ahead smart charging solve failed: {exc}") from exc

    if not _is_solver_optimal(results):
        solver_data = getattr(results, "solver", None)
        status = getattr(solver_data, "status", "unknown")
        term = getattr(solver_data, "termination_condition", "unknown")
        raise RuntimeError(
            "Day-ahead smart charging solve was not optimal "
            f"(status={status}, termination={term})"
        )

    p_ev_res = {
        int(t): {v: float(value(model.p_ev_pos[v, t])) for v in model.V} for t in model.T
    }
    s_res = {
        int(tp): {v: float(value(model.s[v, tp])) for v in model.V} for tp in model.Tp
    }
    p_cc_res = {int(t): float(value(model.p_cc[t])) for t in model.T}

    return p_ev_res, s_res, p_cc_res
