"""Stage-1 G2V capability optimization model.

Purpose
-------
This module builds and solves the Pyomo MILP used to compute *downward*
flexibility, i.e. the maximum feasible charging (grid consumption) profile
for a cluster over the planning horizon.

Dependencies and interactions
-----------------------------
- Uses :mod:`pyomo.environ` to define and solve MILP primitives.
- Is orchestrated by :mod:`services.flex_workflow` during
  ``run_flex_potential_estimation``.
- Shares the same input schema as ``v2g_capability.py`` so both envelopes are
  directly comparable.
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
    maximize,
)


def _build_g2v_model(
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
    """Construct the G2V MILP model used for downward envelope extraction.

    Purpose
    -------
    Create a Pyomo model whose objective is to maximize cluster net charging
    power while respecting EV energy, power and availability constraints.

    Parameters
    ----------
    opt_step : int
        Optimization step size in seconds. Example: ``900`` (15 minutes).
    opt_horizon : Iterable[int]
        Ordered optimization index set including terminal SoC point.
        Example: ``range(0, 25)`` for 24 planning steps.
    bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg : Dict[str, float]
        Per-EV technical parameters indexed by EV id (e.g. ``"EV1"``).
    deptime : Dict[str, int]
        Departure index per EV in the optimization horizon.
    use_tarsoc : Dict[str, int] | None
        Optional flag (0/1) per EV.
        ``1`` => enforce target SoC at departure, ``0`` => enforce min SoC.
    arrtime : Dict[str, int] | None
        Optional arrival index per EV. Missing values default to 0.

    Returns
    -------
    ConcreteModel
        Fully defined Pyomo model instance ready to solve.

    Side Effects
    ------------
    None. Only allocates in-memory model objects.

    Raises
    ------
    KeyError
        If required EV dictionaries are missing keys for some vehicles.

    Example
    -------
    >>> model = _build_g2v_model(
    ...     opt_step=900,
    ...     opt_horizon=range(0, 5),
    ...     bcap={"EV1": 50.0},
    ...     inisoc={"EV1": 0.4},
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
    model = ConcreteModel(name="G2V_capability")

    # Sets
    model.V = Set(initialize=list(bcap.keys()))
    model.T = Set(initialize=list(opt_horizon)[:-1])
    model.Tp = Set(initialize=list(opt_horizon))

    # Global parameters
    model.deltaSec = opt_step

    # Optional flags and timing defaults
    if use_tarsoc is None:
        use_tarsoc = {v: 1 for v in bcap.keys()}
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    # Per-vehicle parameters
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
    def initial_soc_rule(m, v):
        return m.s[v, m.t_arr[v]] == m.s_ini[v]
    model.init_soc = Constraint(model.V, rule=initial_soc_rule)

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
    def soc_dynamics_rule(m, v, t):
        if t < m.t_arr[v]:
            return Constraint.Skip
        return m.s[v, t + 1] == (
            m.s[v, t]
            + m.deltaSec
            * (m.p_ev_pos[v, t] * m.ch_eff[v] - m.p_ev_neg[v, t] / m.ds_eff[v])
            / (m.b_cap[v] * 3600)
        )
    model.soc_dyn = Constraint(model.V, model.T, rule=soc_dynamics_rule)

    # No charging/discharging after departure
    model.no_charge_after_dep = Constraint(
        model.V, model.T,
        rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0 if t >= m.t_dep[v] else Constraint.Skip
    )
    model.no_discharge_after_dep = Constraint(
        model.V, model.T,
        rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0 if t >= m.t_dep[v] else Constraint.Skip
    )

    # No charging/discharging before arrival
    model.no_charge_before_arr = Constraint(
        model.V, model.T,
        rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0 if t < m.t_arr[v] else Constraint.Skip
    )
    model.no_discharge_before_arr = Constraint(
        model.V, model.T,
        rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0 if t < m.t_arr[v] else Constraint.Skip
    )

    # Cluster power = sum of EVs
    model.cluster_power = Constraint(
        model.T,
        rule=lambda m, t: m.p_cc[t] == sum(m.p_ev_pos[v, t] - m.p_ev_neg[v, t] for v in m.V),
    )

    # Power bounds
    model.p_ev_pos_max = Constraint(model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] <= m.p_max_pos[v])
    model.p_ev_neg_max = Constraint(model.V, model.T, rule=lambda m, v, t: m.p_ev_neg[v, t] <= m.p_max_neg[v])

    # Departure SOC
    def dep_soc_rule(m, v):
        return m.s[v, m.t_dep[v]] >= (
            m.use_target[v] * m.s_tar[v]
            + (1 - m.use_target[v]) * m.s_min[v]
        )
    model.dep_soc = Constraint(model.V, rule=dep_soc_rule)

    return model


def compute_g2v_capability(
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
    """Solve the G2V model and return downward-flexibility schedules.

    Purpose
    -------
    Solve the Stage-1 maximum-consumption MILP and export extracted EV/cluster
    trajectories in dict form for downstream envelope processing.

    Parameters
    ----------
    solver : Any
        Pyomo-compatible solver object, e.g. ``SolverFactory("glpk")``.
    opt_step, opt_horizon, bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg, deptime, use_tarsoc, arrtime
        Same semantics as :func:`_build_g2v_model`.

    Returns
    -------
    Tuple[Dict, Dict, Dict]
        ``(p_ev_pos_res, s_res, p_cc_res)`` where:
        - ``p_ev_pos_res[t][ev]`` is charging power [kW]
        - ``s_res[t][ev]`` is SoC [-]
        - ``p_cc_res[t]`` is aggregate cluster power [kW]

    Side Effects
    ------------
    Calls the external MILP solver process through Pyomo.

    Raises
    ------
    Any solver exception raised by ``solver.solve`` is propagated.

    Example
    -------
    >>> # result = compute_g2v_capability(solver, 900, range(0, 5), ...)
    >>> # p_cluster = result[2]
    """
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    model = _build_g2v_model(
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

    # Objective: maximize cluster consumption
    model.obj = Objective(rule=lambda m: summation(m.p_cc), sense=maximize)

    solver.solve(model, tee=False)

    # Extract results
    p_ev_pos_res = {int(t): {v: float(value(model.p_ev_pos[v, t])) for v in model.V} for t in model.T}
    s_res = {int(tp): {v: float(value(model.s[v, tp])) for v in model.V} for tp in model.Tp}
    p_cc_res = {int(t): float(value(model.p_cc[t])) for t in model.T}

    return p_ev_pos_res, s_res, p_cc_res
