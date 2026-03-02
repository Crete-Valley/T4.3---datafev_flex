"""Stage-1 V2G capability optimization model.

Purpose
-------
This module builds and solves the Pyomo MILP used to compute *upward*
flexibility, i.e. the minimum feasible cluster net power profile
(most exporting/discharging trajectory) over the planning horizon.

Dependencies and interactions
-----------------------------
- Uses :mod:`pyomo.environ` for model creation.
- Called from :mod:`services.flex_workflow` alongside G2V capability to build
  an envelope pair per cluster.
"""

from typing import Dict, Iterable, Tuple

from pyomo.environ import (
    ConcreteModel,
    Set,
    Param,
    Var,
    NonNegativeReals,
    Reals,
    Objective,
    Constraint,
    Binary,
    summation,
    value,
)


def _build_v2g_model(
    opt_step: int,
    opt_horizon: Iterable[int],
    bcap: Dict[str, float],
    inisoc: Dict[str, float],
    tarsoc: Dict[str, float],
    minsoc: Dict[str, float],
    maxsoc: Dict[str, float],
    ch_eff: Dict[str, float],
    ds_eff: Dict[str, float],
    pmax_pos: Dict[str, float],
    pmax_neg: Dict[str, float],
    deptime: Dict[str, int],
    use_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
) -> ConcreteModel:
    """Construct the V2G MILP model used for upward envelope extraction.

    Purpose
    -------
    Define the optimization problem that minimizes cluster net power while
    preserving EV limits, availability windows and departure SoC constraints.

    Parameters
    ----------
    opt_step : int
        Optimization step size in seconds. Example: ``900``.
    opt_horizon : Iterable[int]
        Discrete optimization indices. Example: ``range(0, 25)``.
    bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg : Dict[str, float]
        Per-EV parameters keyed by vehicle id.
    deptime : Dict[str, int]
        Departure index per EV.
    use_tarsoc : Dict[str, int] | None
        Optional target enforcement flag (0/1) per EV.
    arrtime : Dict[str, int] | None
        Optional arrival index per EV. Defaults to 0 when not provided.

    Returns
    -------
    ConcreteModel
        Pyomo model ready for objective assignment and solve.

    Side Effects
    ------------
    None.

    Raises
    ------
    KeyError
        If any parameter dictionary is missing required EV keys.

    Example
    -------
    >>> model = _build_v2g_model(
    ...     opt_step=900,
    ...     opt_horizon=range(0, 5),
    ...     bcap={"EV1": 50.0},
    ...     inisoc={"EV1": 0.5},
    ...     tarsoc={"EV1": 0.8},
    ...     minsoc={"EV1": 0.2},
    ...     maxsoc={"EV1": 1.0},
    ...     ch_eff={"EV1": 0.95},
    ...     ds_eff={"EV1": 0.95},
    ...     pmax_pos={"EV1": 7.2},
    ...     pmax_neg={"EV1": 3.0},
    ...     deptime={"EV1": 4},
    ... )
    """
    model = ConcreteModel(name="V2G_capability")

    model.V = Set(initialize=list(bcap.keys()))
    model.T = Set(initialize=list(opt_horizon)[:-1])
    model.Tp = Set(initialize=list(opt_horizon))

    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}
    if use_tarsoc is None:
        use_tarsoc = {v: 1 for v in bcap.keys()}

    model.deltaSec = opt_step
    model.b_cap = Param(model.V, initialize=bcap)
    model.s_ini = Param(model.V, initialize=inisoc)
    model.t_arr = Param(model.V, initialize=arrtime)
    model.s_tar = Param(model.V, initialize=tarsoc)
    model.s_min = Param(model.V, initialize=minsoc)
    model.s_max = Param(model.V, initialize=maxsoc)
    model.ch_eff = Param(model.V, initialize=ch_eff)
    model.ds_eff = Param(model.V, initialize=ds_eff)
    model.p_max_pos = Param(model.V, initialize=pmax_pos)
    model.p_max_neg = Param(model.V, initialize=pmax_neg)
    model.t_dep = Param(model.V, initialize=deptime)

    model.use_target = Param(model.V, initialize=use_tarsoc, within=Binary)

    # Vars
    model.p_ev_pos = Var(model.V, model.T, within=NonNegativeReals)
    model.p_ev_neg = Var(model.V, model.T, within=NonNegativeReals)
    model.s = Var(model.V, model.Tp, within=NonNegativeReals)
    model.p_cc = Var(model.T, within=Reals)

    # Initial SOC
    model.init_soc = Constraint(model.V, rule=lambda m, v: m.s[v, m.t_arr[v]] == m.s_ini[v])

    # Hold SOC constant prior to arrival
    model.soc_before_arrival = Constraint(
        model.V,
        model.Tp,
        rule=lambda m, v, t: m.s[v, t] == m.s_ini[v] if t <= m.t_arr[v] else Constraint.Skip,
    )

    # SOC bounds
    model.soc_min = Constraint(model.V, model.Tp, rule=lambda m, v, t: m.s[v, t] >= m.s_min[v])
    model.soc_max = Constraint(model.V, model.Tp, rule=lambda m, v, t: m.s[v, t] <= m.s_max[v])

    # SOC dynamics
    def soc_dyn_rule(m, v, t):
        if t < m.t_arr[v]:
            return Constraint.Skip
        return m.s[v, t + 1] == (
            m.s[v, t]
            + m.deltaSec * (m.p_ev_pos[v, t] * m.ch_eff[v] - m.p_ev_neg[v, t] / m.ds_eff[v]) / (m.b_cap[v] * 3600)
        )
    model.soc_dyn = Constraint(model.V, model.T, rule=soc_dyn_rule)

    # No power after departure
    model.no_chg_after_dep = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0 if t >= m.t_dep[v] else Constraint.Skip
    )
    model.no_dsc_after_dep = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0 if t >= m.t_dep[v] else Constraint.Skip
    )

    # No power before arrival
    model.no_chg_before_arr = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0 if t < m.t_arr[v] else Constraint.Skip
    )
    model.no_dsc_before_arr = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0 if t < m.t_arr[v] else Constraint.Skip
    )

    # Cluster power
    model.cluster_power = Constraint(
        model.T, rule=lambda m, t: m.p_cc[t] == sum(m.p_ev_pos[v, t] - m.p_ev_neg[v, t] for v in model.V)
    )

    # Power bounds
    model.p_ev_pos_max = Constraint(model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] <= m.p_max_pos[v])
    model.p_ev_neg_max = Constraint(model.V, model.T, rule=lambda m, v, t: m.p_ev_neg[v, t] <= m.p_max_neg[v])

    # Departure SOC constraint
    def dep_soc_rule(m, v):
        return m.s[v, m.t_dep[v]] >= (
            m.use_target[v] * m.s_tar[v]
            + (1 - m.use_target[v]) * m.s_min[v]
        )
    model.dep_soc = Constraint(model.V, rule=dep_soc_rule)

    return model


def compute_v2g_capability(
    solver,
    opt_step: int,
    opt_horizon: Iterable[int],
    bcap: Dict[str, float],
    inisoc: Dict[str, float],
    tarsoc: Dict[str, float],
    minsoc: Dict[str, float],
    maxsoc: Dict[str, float],
    ch_eff: Dict[str, float],
    ds_eff: Dict[str, float],
    pmax_pos: Dict[str, float],
    pmax_neg: Dict[str, float],
    deptime: Dict[str, int],
    use_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
) -> Tuple[Dict, Dict, Dict]:
    """Solve the V2G model and return upward-flexibility schedules.

    Purpose
    -------
    Execute the Stage-1 minimum-power MILP and extract EV-level discharging,
    SoC trajectories and cluster net power used for envelope generation.

    Parameters
    ----------
    solver : Any
        Pyomo-compatible solver instance.
    opt_step, opt_horizon, bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg, deptime, use_tarsoc, arrtime
        Same semantics as :func:`_build_v2g_model`.

    Returns
    -------
    Tuple[Dict, Dict, Dict]
        ``(p_ev_neg_res, s_res, p_cc_res)`` keyed by time index.

    Side Effects
    ------------
    Calls the external solver backend.

    Raises
    ------
    Any exception raised by ``solver.solve`` is propagated.

    Example
    -------
    >>> # p_ev_neg, soc, p_cluster = compute_v2g_capability(solver, 900, range(0, 5), ...)
    """
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    model = _build_v2g_model(
        opt_step=opt_step,
        opt_horizon=opt_horizon,
        bcap=bcap,
        inisoc=inisoc,
        tarsoc=tarsoc,
        minsoc=minsoc,
        maxsoc=maxsoc,
        ch_eff=ch_eff,
        ds_eff=ds_eff,
        pmax_pos=pmax_pos,
        pmax_neg=pmax_neg,
        deptime=deptime,
        arrtime=arrtime,
        use_tarsoc=use_tarsoc,
    )

    model.obj = Objective(rule=lambda m: summation(m.p_cc))  # minimize

    solver.solve(model, tee=False)

    # Extract
    p_ev_neg_res = {int(t): {v: float(value(model.p_ev_neg[v, t])) for v in model.V} for t in model.T}
    s_res = {int(tp): {v: float(value(model.s[v, tp])) for v in model.V} for tp in model.Tp}
    p_cc_res = {int(t): float(value(model.p_cc[t])) for t in model.T}

    return p_ev_neg_res, s_res, p_cc_res
