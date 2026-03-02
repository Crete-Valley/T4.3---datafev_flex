"""Legacy MILP for joint EV routing and charging schedule optimization.

Purpose
-------
Given a candidate set of clusters with cluster-dependent arrival/departure and
price forecasts, choose:
1. which cluster the EV should be assigned to, and
2. a power schedule that satisfies SoC and V2G constraints.

Note
----
This module is currently not in the Stage-1/Stage-2 API critical path, but it
is kept as a reusable optimization primitive for advanced routing scenarios.
"""

# The datafev framework

# Copyright (C) 2022,
# Institute for Automation of Complex Power Systems (ACS),
# E.ON Energy Research Center (E.ON ERC),
# RWTH Aachen University

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


from pyomo.core import *
import pyomo.kernel as pmo


def smart_routing(
    solver,
    opt_horizon,
    opt_step,
    ecap,
    v2gall,
    tarsoc,
    minsoc,
    maxsoc,
    crtsoc,
    crttime,
    arrtime,
    deptime,
    arrsoc,
    p_ch,
    p_ds,
    g2v_dps,
    v2g_dps,
):
    """Solve joint cluster-allocation and charging MILP for one incoming EV.

    Purpose
    -------
    Minimize charging cost (and maximize V2G revenue) while selecting a single
    cluster and feasible charge/discharge trajectory under SoC constraints.

    Parameters
    ----------
    solver : pyomo.opt.base.solvers.OptSolver
        Initialized Pyomo solver instance (e.g., GLPK/Gurobi).
        Example: ``SolverFactory("glpk")``.
    opt_horizon : Iterable[int]
        Discrete optimization indices, typically ``range(N)``.
        Example: ``range(13)``.
    opt_step : float
        Duration of one optimization step in seconds.
        Example: ``300``.
    ecap : float
        Battery energy capacity in kWs.
        Example: ``50 * 3600``.
    v2gall : float
        Maximum cumulative discharged energy (kWs).
        Example: ``10 * 3600``.
    tarsoc : float
        Target terminal SoC in ``[0, 1]``.
        Example: ``0.7``.
    minsoc : float
        Lower SoC bound in ``[0, 1]``.
        Example: ``0.2``.
    maxsoc : float
        Upper SoC bound in ``[0, 1]``.
        Example: ``1.0``.
    crtsoc : float
        Minimum SoC required in confidence period.
        Example: ``0.5``.
    crttime : int
        First optimization index where confidence-period minimum SoC applies.
        Example: ``8``.
    arrtime : dict[str, int]
        Cluster-dependent arrival index.
        Example: ``{"C1": 1, "C2": 3}``.
    deptime : dict[str, int]
        Cluster-dependent departure index.
        Example: ``{"C1": 12, "C2": 12}``.
    arrsoc : dict[str, float]
        Cluster-dependent arrival SoC estimate in ``[0, 1)``.
        Example: ``{"C1": 0.35, "C2": 0.32}``.
    p_ch : dict[str, float]
        Per-cluster max charging power in kW.
        Example: ``{"C1": 22.0, "C2": 11.0}``.
    p_ds : dict[str, float]
        Per-cluster max discharging power in kW.
        Example: ``{"C1": 11.0, "C2": 11.0}``.
    g2v_dps : dict[str, dict[int, float]]
        Cluster/time-indexed G2V prices in currency per kWh.
    v2g_dps : dict[str, dict[int, float]]
        Cluster/time-indexed V2G prices in currency per kWh.

    Returns
    -------
    tuple[dict[int, float], dict[int, float], str]
        - ``p_schedule``: net power schedule (kW) by time index.
        - ``s_schedule``: SoC trajectory by time index.
        - ``target_cc``: selected cluster id.

    Side Effects
    ------------
    - Solves a MILP using the provided solver backend.
    - Prints target SoC for quick CLI diagnostics.

    Raises
    ------
    RuntimeError
        Not explicitly raised here, but downstream solver failures can raise
        backend exceptions depending on solver interface.

    Example
    -------
    >>> from pyomo.environ import SolverFactory
    >>> p, s, c = smart_routing(
    ...     solver=SolverFactory("glpk"),
    ...     opt_horizon=range(13),
    ...     opt_step=300,
    ...     ecap=50 * 3600,
    ...     v2gall=10 * 3600,
    ...     tarsoc=0.6,
    ...     minsoc=0.2,
    ...     maxsoc=1.0,
    ...     crtsoc=0.5,
    ...     crttime=8,
    ...     arrtime={"C1": 0, "C2": 3},
    ...     deptime={"C1": 12, "C2": 12},
    ...     arrsoc={"C1": 0.4, "C2": 0.35},
    ...     p_ch={"C1": 22, "C2": 11},
    ...     p_ds={"C1": 11, "C2": 11},
    ...     g2v_dps={"C1": {t: 0.4 for t in range(12)}, "C2": {t: 0.5 for t in range(12)}},
    ...     v2g_dps={"C1": {t: 0.3 for t in range(12)}, "C2": {t: 0.4 for t in range(12)}},
    ... )
    """

    conf_period = {}
    # Build a binary mask that activates stricter minimum-SoC after `crttime`.
    for t in opt_horizon:
        if t < crttime:
            conf_period[t] = 0
        else:
            conf_period[t] = 1

    candidate_clusters = arrtime.keys()

    ####################Constructing the optimization model####################
    model = ConcreteModel()

    model.T = Set(initialize=opt_horizon, ordered=True)  # Time index set
    model.C = Set(initialize=candidate_clusters, ordered=True)  # Cluster index set
    model.dt = opt_step  # Step size
    model.E = ecap  # Battery capacity in kWs

    model.SoC_F = tarsoc  # SoC to be achieved at the end
    model.SoC_R = crtsoc  # Minimim SOC must be ensured in the confidence period
    model.conf = conf_period  # Confidence period where SOC must be larger than crtsoc
    model.V2G_ALL = v2gall  # Maximum energy that can be discharged V2G

    model.P_CH_Max = max(p_ch.values())  # Maximum available charging power in kW
    model.P_DS_Max = max(p_ds.values())  # Maximum available discharging power in kW
    model.P_CH = p_ch  # Cluster dependent max charging power in kW
    model.P_DS = p_ds  # Cluster dependent max discharging power in kW
    model.W_G2V = g2v_dps  # Time-variant G2V cost coefficients of clusters
    model.W_V2G = v2g_dps  # Time-variant V2G cost coefficients of clusters
    model.t_arr = arrtime  # Cluster dependent arrival time estimation
    model.t_dep = deptime  # Cluster dependent departure time estimation
    model.SoC_I = arrsoc  # Cluster dependent arrival SOCs estimation

    model.xc = Var(
        model.C, within=pmo.Binary
    )  # Binary variable having 1 if v is allocated to c
    model.xp = Var(
        model.T, within=pmo.Binary
    )  # Binary variable having 1/0 if v is charged/discharged at t
    model.p = Var(model.T, within=Reals)  # Net charge power at t
    model.p_pos = Var(model.T, within=NonNegativeReals)  # Charge power at t
    model.p_neg = Var(model.T, within=NonNegativeReals)  # Discharge power at t
    model.pc_pos = Var(
        model.C, model.T, within=NonNegativeReals
    )  # Charge power at t if it is in cluster c
    model.pc_neg = Var(
        model.C, model.T, within=NonNegativeReals
    )  # Discharge power at t if it is in cluster c
    model.SoC = Var(
        model.T, within=NonNegativeReals, bounds=(minsoc, maxsoc)
    )  # SOC to be achieved  at time step t

    # CONSTRAINTS
    def initialsoc(model):
        return model.SoC[0] == sum(model.xc[c] * model.SoC_I[c] for c in model.C)

    model.inisoc = Constraint(rule=initialsoc)

    def storageConservation(
        model, t
    ):  # SOC of EV batteries will change with respect to the charged power and battery energy capacity
        if t < max(model.T):
            return model.SoC[t + 1] == (model.SoC[t] + model.p[t] * model.dt / model.E)
        else:
            return model.SoC[t] == model.SoC_F

    model.socconst = Constraint(model.T, rule=storageConservation)

    def socconfidence(model, t):
        return model.SoC[t] >= model.SoC_R * model.conf[t]

    model.socconfi = Constraint(model.T, rule=socconfidence)

    def supplyrule_end(model):
        return model.p[max(model.T)] == 0.0

    model.supconst = Constraint(rule=supplyrule_end)

    def combinatorics0(model):  # EV can assigned to only one cluster
        return sum(model.xc[c] for c in model.C) == 1

    model.comb0const = Constraint(rule=combinatorics0)

    def combinatorics11(model, c, t):
        if model.t_arr[c] <= t < model.t_dep[c]:
            return model.pc_neg[c, t] <= model.P_DS[c] * model.xc[c]
        else:
            return model.pc_neg[c, t] == 0

    model.comb11const = Constraint(model.C, model.T, rule=combinatorics11)

    def combinatorics12(model, c, t):
        if model.t_arr[c] <= t < model.t_dep[c]:
            return model.pc_pos[c, t] <= model.P_CH[c] * model.xc[c]
        else:
            return model.pc_pos[c, t] == 0

    model.comb12const = Constraint(model.C, model.T, rule=combinatorics12)

    def combinatorics2(model, t):
        return model.p[t] == sum(
            model.pc_pos[c, t] - model.pc_neg[c, t] for c in model.C
        )

    model.comb2const = Constraint(model.T, rule=combinatorics2)

    def netcharging(model, t):
        return model.p[t] == model.p_pos[t] - model.p_neg[t]

    model.netchr = Constraint(model.T, rule=netcharging)

    def combinatorics31_pos(model, t):
        return model.p_pos[t] <= model.xp[t] * model.P_CH_Max

    model.comb31pconst = Constraint(model.T, rule=combinatorics31_pos)

    def combinatorics32_pos(model, t):
        return model.p_pos[t] == sum(model.pc_pos[c, t] for c in model.C)

    model.comb32pconst = Constraint(model.T, rule=combinatorics32_pos)

    def combinatorics31_neg(model, t):
        return model.p_neg[t] <= (1 - model.xp[t]) * model.P_DS_Max

    model.comb31nconst = Constraint(model.T, rule=combinatorics31_neg)

    def combinatorics32_neg(model, t):
        return model.p_neg[t] == sum(model.pc_neg[c, t] for c in model.C)

    model.comb32nconst = Constraint(model.T, rule=combinatorics32_neg)

    def v2g_limit(model):
        return sum(model.p_neg[t] * model.dt for t in model.T) <= model.V2G_ALL

    model.v2gconst = Constraint(rule=v2g_limit)

    # Objective: minimize net energy cost over the horizon.
    # Charging contributes positive cost, discharging contributes negative cost
    # (i.e., revenue), both scaled by step duration.
    def obj_rule(model):
        return (
            sum(
                model.W_G2V[c][t] * model.pc_pos[c, t]
                - model.W_V2G[c][t] * model.pc_neg[c, t]
                for c in model.C
                for t in opt_horizon[:-1]
            )
            * opt_step
            / 3600
        )

    model.obj = Objective(rule=obj_rule, sense=minimize)

    print("Target SOC:",tarsoc)

    # model.pprint()
    result = solver.solve(model)  # ,tee=True)
    # print(result)

    p_schedule = {}
    s_schedule = {}
    for t in model.T:
        p_schedule[t] = model.p[t]()
        s_schedule[t] = model.SoC[t]()

    for c in model.C:
        if abs(model.xc[c]() - 1) <= 0.01:
                target_cc = c

    return p_schedule, s_schedule, target_cc

"""
if __name__ == "__main__":

    import pandas as pd
    import numpy as np
    from pyomo.environ import *
    from pyomo.opt import SolverFactory

    ###########################################################################
    # Input parameters
    solver = SolverFactory("glpk")
    opt_step = 300  # seconds
    opt_horizon = range(13)  # [0 1 2 3 4 .. 12]  == 1 hour for opt_step=300 seconds
    ecap = 50 * 3600  # kWs
    v2gall = 10 * 3600  # kWs
    tarsoc = 0.6
    minsoc = 0.4
    maxsoc = 1.0
    crtsoc = tarsoc
    crttime = 12
    arrtime = {"C1": 0, "C2": 5, "C3": 5}
    deptime = {"C1": 13, "C2": 13, "C3": 13}
    arrsoc = {"C1": 0.5, "C2": 0.49, "C3": 0.49}
    p_ch = {"C1": 50, "C2": 50, "C3": 50}
    p_ds = {"C1": 50, "C2": 50, "C3": 50}

    np.random.seed(0)
    g2v_dps = {}
    v2g_dps = {}
    for c in ["C1", "C2", "C3"]:
        g2v_tariff = np.random.uniform(low=0.4, high=0.8, size=12)
        g2v_dps[c] = dict(enumerate(g2v_tariff))
        v2g_dps[c] = dict(enumerate(g2v_tariff * 0.9))

    dps = {}
    for c in ["C1", "C2", "C3"]:
        dps[c] = pd.DataFrame(columns=["G2V", "V2G"])
        dps[c]["G2V"] = pd.Series(g2v_dps[c])
        dps[c]["V2G"] = pd.Series(v2g_dps[c])
    ###########################################################################

    print("Reservation request of an EV with")
    print("Battery capacity   :", ecap / 3600, "kWh")
    print("Target SOC         :", tarsoc)
    print("V2G allowance      :", v2gall / 3600, "kWh")
    print()
    print(
        "Since the available clusters are at different distances, some parameters are cluster dependent"
    )
    print("Estimated arrival SOCs        :", arrsoc)
    print("Estimated arrival time steps  :", arrtime)
    print("Estimated departure time steps:", deptime)
    print("Dynamic price signals of the clusters:")
    print(pd.concat(dps, axis=1))
    print()

    print("Smart routing optimization problem is solved...")
    p, s, c = smart_routing(
        solver,
        opt_horizon,
        opt_step,
        ecap,
        v2gall,
        tarsoc,
        minsoc,
        maxsoc,
        crtsoc,
        crttime,
        arrtime,
        deptime,
        arrsoc,
        p_ch,
        p_ds,
        g2v_dps,
        v2g_dps,
    )
    print()
    print("The result:")
    print(
        "Under the given price signals, the optimal decision is to go to the cluster", c
    )
    print()
    print("And charge with the profile is printed in table")
    print("SOC (%): SOC trajectory in optimized schedule")
    print("P (kW): Power supply to the EV in optimized schedule")
    results = pd.DataFrame(columns=["P", "SOC (%)"], index=sorted(s.keys()))
    results["P (kW)"] = pd.Series(p)
    results["SOC (%)"] = pd.Series(s) * 100
    print(results)
    print()
"""
