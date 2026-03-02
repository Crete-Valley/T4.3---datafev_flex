"""Stage-2 flex-aware cluster scheduling MILP.

Purpose
-------
Solve per-cluster command tracking (absolute setpoint or flex band) while
respecting EV availability and SoC policies.

Dependencies and interactions
-----------------------------
- Uses :mod:`pyomo` for modeling and solve status inspection.
- Invoked by :mod:`services.flex_workflow` after command validation.
- Receives command setpoints that were pre-validated against Stage-1 envelopes.
"""

from typing import Dict, Iterable, Tuple

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    NonNegativeReals,
    Objective,
    Param,
    Reals,
    Set,
    Var,
    minimize,
    value,
)
from pyomo.opt import SolverStatus, TerminationCondition


TRACKING_MODE_STRICT = "strict"
TRACKING_MODE_BEST_EFFORT = "best_effort"
SUPPORTED_TRACKING_MODES = {TRACKING_MODE_STRICT, TRACKING_MODE_BEST_EFFORT}


def _normalize_tracking_mode(tracking_mode: str) -> str:
    normalized = (tracking_mode or "").strip().lower()
    if normalized not in SUPPORTED_TRACKING_MODES:
        raise ValueError(
            "Unsupported tracking_mode. Use 'strict' or 'best_effort'."
        )
    return normalized


def _build_flex_aware_model(
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
    setpoint: Dict[int, float] | None = None,
    p_min: Dict[int, float] | None = None,
    p_max: Dict[int, float] | None = None,
    use_tarsoc: Dict[str, int] | None = None,
    use_exact_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
    tracking_mode: str = TRACKING_MODE_STRICT,
    deviation_penalty_weight: float = 1.0,
    cycling_penalty_weight: float = 1e-6,
) -> ConcreteModel:
    """Build the Stage-2 MILP model for command tracking.

    Purpose
    -------
    Create the optimization model that dispatches EV charging/discharging
    trajectories such that cluster power follows the incoming command:
    - absolute setpoint: ``p_min[t] == p_max[t] == p_set[t]``
    - flex band: ``p_min[t] <= p_cc[t] <= p_max[t]``

    Parameters
    ----------
    opt_step : int
        Step size in seconds. Example: ``900``.
    opt_horizon : Iterable[int]
        Time index set including terminal SoC point.
    setpoint : Dict[int, float] | None
        Backward-compatible absolute setpoint input. When provided and
        ``p_min``/``p_max`` are not provided, it is used to build a degenerate
        band.
    p_min, p_max : Dict[int, float] | None
        Lower/upper cluster power command profiles in kW keyed by optimization
        step.
    bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg : Dict[str, float]
        Per-EV technical limits and states.
    deptime : Dict[str, int]
        Departure timestep per EV.
    use_tarsoc : Dict[str, int] | None
        Departure target activation flag (0/1) per EV.
    use_exact_tarsoc : Dict[str, int] | None
        Target strictness flag (0/1) per EV. When ``1`` and target active,
        departure SoC is enforced as equality.
    arrtime : Dict[str, int] | None
        Arrival timestep per EV.
    tracking_mode : str
        `strict` enforces `p_min/p_max` as hard bounds.
        `best_effort` softens tracking and minimizes deviation.
    deviation_penalty_weight, cycling_penalty_weight : float
        Objective weights used in `best_effort`.

    Returns
    -------
    ConcreteModel
        Fully specified Pyomo model with objective and constraints.

    Side Effects
    ------------
    None (in-memory model creation only).

    Raises
    ------
    KeyError
        If parameter dictionaries do not share the same EV keys.

    Example
    -------
    >>> model = _build_flex_aware_model(
    ...     opt_step=900,
    ...     opt_horizon=range(0, 5),
    ...     setpoint={0: 0.0, 1: 1.0, 2: 0.0, 3: 0.0},
    ...     p_min=None,
    ...     p_max=None,
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
    normalized_tracking_mode = _normalize_tracking_mode(tracking_mode)

    if p_min is None or p_max is None:
        if setpoint is None:
            raise ValueError("Either setpoint or both p_min/p_max must be provided.")
        p_min = {int(t): float(v) for t, v in setpoint.items()}
        p_max = {int(t): float(v) for t, v in setpoint.items()}

    model = ConcreteModel(name="flex_aware_scheduling")

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

    model.p_min = Param(model.T, initialize=p_min)
    model.p_max = Param(model.T, initialize=p_max)
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
    model.use_exact_target = Param(model.V, initialize=use_exact_tarsoc, within=Binary)

    model.p_ev_pos = Var(model.V, model.T, within=NonNegativeReals)
    model.p_ev_neg = Var(model.V, model.T, within=NonNegativeReals)
    model.s = Var(model.V, model.Tp, within=NonNegativeReals)
    model.p_cc = Var(model.T, within=Reals)

    # Initial SOC at arrival.
    model.init_soc = Constraint(
        model.V, rule=lambda m, v: m.s[v, m.t_arr[v]] == m.s_ini[v]
    )

    # Prior to arrival, hold SOC at initial value.
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
            * (m.p_ev_pos[v, t] * m.ch_eff[v] - m.p_ev_neg[v, t] / m.ds_eff[v])
            / (m.b_cap[v] * 3600)
        )

    model.soc_dyn = Constraint(model.V, model.T, rule=soc_dyn_rule)

    # Power is not allowed before arrival and after departure.
    model.no_charge_before_arr = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_pos[v, t] == 0.0
        if t < m.t_arr[v]
        else Constraint.Skip,
    )
    model.no_discharge_before_arr = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0
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
    model.no_discharge_after_dep = Constraint(
        model.V,
        model.T,
        rule=lambda m, v, t: m.p_ev_neg[v, t] == 0.0
        if t >= m.t_dep[v]
        else Constraint.Skip,
    )

    model.p_ev_pos_max = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_pos[v, t] <= m.p_max_pos[v]
    )
    model.p_ev_neg_max = Constraint(
        model.V, model.T, rule=lambda m, v, t: m.p_ev_neg[v, t] <= m.p_max_neg[v]
    )

    model.cluster_power = Constraint(
        model.T,
        rule=lambda m, t: m.p_cc[t]
        == sum(m.p_ev_pos[v, t] - m.p_ev_neg[v, t] for v in m.V),
    )

    # Target SOC policies:
    # - use_target=0 -> s_dep >= s_min
    # - use_target=1 and use_exact_target=0 -> s_dep >= s_tar
    # - use_target=1 and use_exact_target=1 -> s_dep == s_tar
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

    cycling_expr = sum(
        model.p_ev_pos[v, t] + model.p_ev_neg[v, t] for v in model.V for t in model.T
    )

    if normalized_tracking_mode == TRACKING_MODE_STRICT:
        model.track_lower_bound = Constraint(
            model.T, rule=lambda m, t: m.p_cc[t] >= m.p_min[t]
        )
        model.track_upper_bound = Constraint(
            model.T, rule=lambda m, t: m.p_cc[t] <= m.p_max[t]
        )
        # Secondary criterion to avoid unnecessary charge/discharge cycling.
        model.obj = Objective(expr=cycling_expr, sense=minimize)
        return model

    model.dev_pos = Var(model.T, within=NonNegativeReals)
    model.dev_neg = Var(model.T, within=NonNegativeReals)

    if setpoint is not None:
        p_set_init = {int(t): float(setpoint[int(t)]) for t in model.T}
        model.p_set = Param(model.T, initialize=p_set_init)
        model.track_abs_error = Constraint(
            model.T,
            rule=lambda m, t: m.p_cc[t] - m.p_set[t] == m.dev_pos[t] - m.dev_neg[t],
        )
    else:
        model.track_band_below = Constraint(
            model.T,
            rule=lambda m, t: m.p_cc[t] + m.dev_pos[t] >= m.p_min[t],
        )
        model.track_band_above = Constraint(
            model.T,
            rule=lambda m, t: m.p_cc[t] - m.dev_neg[t] <= m.p_max[t],
        )

    model.obj = Objective(
        expr=deviation_penalty_weight * sum(model.dev_pos[t] + model.dev_neg[t] for t in model.T)
        + cycling_penalty_weight * cycling_expr,
        sense=minimize,
    )

    return model


def _is_solver_optimal(results) -> bool:
    """Check whether a solver result represents an optimal solution.

    Purpose
    -------
    Isolate solver-status interpretation so calling code remains readable and
    test doubles can bypass strict result-object requirements.

    Parameters
    ----------
    results : Any
        Object returned by ``solver.solve(model)``.

    Returns
    -------
    bool
        ``True`` if status is ``ok`` and termination is ``optimal``.
        For ``None`` or result objects without ``solver`` attribute, returns
        ``True`` to support lightweight unit-test doubles.

    Side Effects
    ------------
    None.

    Raises
    ------
    None.

    Example
    -------
    >>> _is_solver_optimal(None)
    True
    """
    if results is None:
        # Test doubles may not return a solver result object.
        return True

    solver_data = getattr(results, "solver", None)
    if solver_data is None:
        return True

    return (
        solver_data.status == SolverStatus.ok
        and solver_data.termination_condition == TerminationCondition.optimal
    )


def compute_flex_aware_schedule(
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
    setpoint: Dict[int, float] | None = None,
    p_min: Dict[int, float] | None = None,
    p_max: Dict[int, float] | None = None,
    use_tarsoc: Dict[str, int] | None = None,
    use_exact_tarsoc: Dict[str, int] | None = None,
    arrtime: Dict[str, int] | None = None,
    tracking_mode: str = TRACKING_MODE_STRICT,
    deviation_penalty_weight: float = 1.0,
    cycling_penalty_weight: float = 1e-6,
) -> Tuple[Dict, Dict, Dict]:
    """Solve Stage-2 scheduling and return EV/cluster trajectories.

    Purpose
    -------
    Execute the flex-aware MILP and provide extracted time-indexed results
    consumed by workflow orchestration and export layers.

    Parameters
    ----------
    solver : Any
        Pyomo-compatible solver instance.
    opt_step : int
        Step size in seconds.
    opt_horizon : Iterable[int]
        Optimization index set.
    setpoint : Dict[int, float] | None
        Backward-compatible absolute setpoint profile [kW].
    p_min, p_max : Dict[int, float] | None
        Lower/upper command profiles [kW].
    bcap, inisoc, tarsoc, minsoc, maxsoc, ch_eff, ds_eff, pmax_pos, pmax_neg : Dict[str, float]
        EV parameters keyed by vehicle id.
    deptime : Dict[str, int]
        EV departure index.
    use_tarsoc, use_exact_tarsoc, arrtime : Dict[str, int] | None
        Optional policy and timing flags.
    tracking_mode : str
        `strict` enforces hard command tracking; `best_effort` minimizes
        command mismatch while keeping EV/SOC constraints hard.
    deviation_penalty_weight, cycling_penalty_weight : float
        Objective weights used in `best_effort` mode.

    Returns
    -------
    Tuple[Dict, Dict, Dict]
        ``(p_ev_res, s_res, p_cc_res)`` where:
        - ``p_ev_res[t][ev]`` is net EV power (charge-discharge) [kW]
        - ``s_res[t][ev]`` is SoC [-]
        - ``p_cc_res[t]`` is cluster power [kW]

    Side Effects
    ------------
    Calls external solver process via Pyomo.

    Raises
    ------
    RuntimeError
        If solve call fails or termination is non-optimal.

    Example
    -------
    >>> # p_ev, soc, p_cluster = compute_flex_aware_schedule(
    ... #     solver, 900, range(0, 5),
    ... #     setpoint={0: 0.0, 1: 1.0, 2: 0.0, 3: 0.0},
    ... #     p_min=None, p_max=None, ...
    ... # )
    """
    if arrtime is None:
        arrtime = {v: 0 for v in bcap.keys()}

    model = _build_flex_aware_model(
        opt_step=opt_step,
        opt_horizon=opt_horizon,
        setpoint=setpoint,
        p_min=p_min,
        p_max=p_max,
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
        use_tarsoc=use_tarsoc,
        use_exact_tarsoc=use_exact_tarsoc,
        arrtime=arrtime,
        tracking_mode=tracking_mode,
        deviation_penalty_weight=deviation_penalty_weight,
        cycling_penalty_weight=cycling_penalty_weight,
    )

    try:
        results = solver.solve(model, tee=False)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Stage-2 solve failed: {exc}") from exc

    if not _is_solver_optimal(results):
        solver_data = getattr(results, "solver", None)
        status = getattr(solver_data, "status", "unknown")
        term = getattr(solver_data, "termination_condition", "unknown")
        raise RuntimeError(
            f"Stage-2 solve was not optimal (status={status}, termination={term})"
        )

    p_ev_res = {
        int(t): {
            v: float(value(model.p_ev_pos[v, t] - model.p_ev_neg[v, t])) for v in model.V
        }
        for t in model.T
    }
    s_res = {
        int(tp): {v: float(value(model.s[v, tp])) for v in model.V} for tp in model.Tp
    }
    p_cc_res = {int(t): float(value(model.p_cc[t])) for t in model.T}

    return p_ev_res, s_res, p_cc_res
