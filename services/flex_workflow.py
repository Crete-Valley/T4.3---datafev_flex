"""Service-layer orchestration for Stage-1 and Stage-2 workflows.

Purpose
-------
This module is the central integration layer used by both CLI (`run_local_workflow.py`) and
HTTP API (`api/app.py`). It glues together parsing, legacy datafev objects,
MILP solvers, validation, export and plotting.

Main responsibilities
---------------------
1. Build planning horizon and transform Excel input into solver-ready data.
2. Run Stage-1 envelope estimation (G2V + V2G) per cluster.
3. Run Stage-2 flex-aware scheduling with command acceptance/rejection logic.
4. Persist artifacts for reporting, visualization and API responses.

Dependencies
------------
- `algorithms.capability.*` and `algorithms.scheduling.*` for MILP logic.
- `data_handling.*` adapters from the datafev framework.
- `utils.*` for command validation, parsing, export and plotting.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from pyomo.environ import SolverFactory

from analysis.kpi_analysis import main as run_kpi_analysis
from algorithms.capability.g2v_capability import compute_g2v_capability
from algorithms.capability.v2g_capability import compute_v2g_capability
from algorithms.scheduling.day_ahead_smart_charging import (
    compute_day_ahead_smart_charging_schedule,
)
from algorithms.scheduling.flex_aware_scheduling import (
    TRACKING_MODE_BEST_EFFORT,
    TRACKING_MODE_STRICT,
    compute_flex_aware_schedule,
)
from data_handling.cluster import ChargerCluster
from data_handling.fleet import EVFleet
from data_handling.multi_cluster import MultiClusterSystem
from utils.flex_command_utils import (
    ABSOLUTE_SETPOINT,
    FLEX_BAND,
    generate_midpoint_flex_band_commands,
    generate_midpoint_setpoint_commands,
    validate_stage2_commands,
)
from utils.input_parser import ( # TODO: refactor to move into fetcher module
    fetch_day_ahead_prices_from_database,
    fetch_database_inputs,
)
from utils.input_parser import (
    parse_xlsx_input,
    parse_day_ahead_prices_sheet,
    parse_planning_sheet,
)
from utils.output_utils import (
    export_capability_to_database,
    export_day_ahead_schedule_to_database,
    export_day_ahead_schedule_results,
    export_capability_timeseries,
    export_stage2_results,
    print_capability_summary,
)
from utils.plotting_service import (
    plot_aggregate_capability,
    plot_cluster_capability_bands,
    plot_stage2_ev_soc_schedules,
)

DEFAULT_OUTPUT_ROOT_DIRNAME = "outputs"
STAGE1_OUTPUT_DIRNAME = "flex_potential_estimation"
STAGE2_OUTPUT_DIRNAME = "flex_aware_smart_charge_scheduling"


@dataclass
class FlexPotentialEstimationArtifacts:
    """Immutable Stage-1 outputs shared with downstream components.

    Attributes
    ----------
    planning_start, planning_end, time_step
        Planning time metadata.
    planning_horizon, opt_step, opt_horizon
        Time-index data for optimization and time-series conversion.
    output_dir
        Path used for generated files.
    solver_backend, solver
        Solver identity and initialized solver object.
    mcsystem, fleet
        Legacy datafev objects with enriched capability/schedule state.
    cluster_capability_summary, cluster_capability_ts, connected_evs_ts
        Export-ready Stage-1 summaries and trajectories.
    market_price_ts
        Horizon-aligned day-ahead market prices in EUR/kWh.
    cluster_day_ahead_power_ts, cluster_day_ahead_ev_power_ts, cluster_day_ahead_ev_soc_ts
        Cost-optimal Stage-1 charging baseline at cluster and EV level.
    day_ahead_ev_summary
        EV-level charging cost summary for workbook export.
    """
    planning_start: datetime
    planning_end: datetime
    time_step: timedelta
    planning_horizon: list[datetime]
    opt_step: int
    opt_horizon: list[int]
    output_dir: str
    solver_backend: str
    solver: Any
    mcsystem: MultiClusterSystem
    fleet: EVFleet
    cluster_capability_summary: dict[str, dict[str, float]]
    cluster_capability_ts: dict[str, pd.DataFrame]
    connected_evs_ts: dict[str, pd.Series]
    market_price_ts: pd.Series
    cluster_day_ahead_power_ts: dict[str, pd.Series]
    cluster_day_ahead_ev_power_ts: dict[str, pd.DataFrame]
    cluster_day_ahead_ev_soc_ts: dict[str, pd.DataFrame]
    day_ahead_ev_summary: pd.DataFrame


@dataclass
class FlexAwareSmartChargeSchedulingArtifacts:
    """Container for Stage-2 scheduling results.

    Attributes
    ----------
    output_dir
        Stage-2 export/plot output directory.
    command_status
        Per-cluster acceptance/rejection status table.
    cluster_command_band_ts, cluster_power_ts
        Requested command bands and achieved cluster trajectories.
    cluster_setpoint_ts
        Optional absolute setpoints when command type is `absolute_setpoint`.
    cluster_ev_power_ts, cluster_ev_soc_ts
        EV-level power and SoC schedules per cluster.
    cluster_tracking_report_ts
        Requested-vs-delivered tracking evaluation per cluster.
    cluster_tracking_summary
        Cluster-level tracking KPIs (matched/total, ratio, max/mean error).
    """
    output_dir: str
    command_status: pd.DataFrame
    cluster_command_band_ts: dict[str, pd.DataFrame]
    cluster_setpoint_ts: dict[str, pd.Series]
    cluster_power_ts: dict[str, pd.Series]
    cluster_ev_power_ts: dict[str, pd.DataFrame]
    cluster_ev_soc_ts: dict[str, pd.DataFrame]
    cluster_tracking_report_ts: dict[str, pd.DataFrame]
    cluster_tracking_summary: dict[str, dict[str, float]]


def build_planning_horizon(
    planning_start: datetime,
    planning_end: datetime,
    time_step: timedelta,
) -> list[datetime]:
    """Create time points from planning start to end (exclusive).

    Purpose
    -------
    Convert wall-clock planning metadata into a discrete list used by all
    workflow layers.

    Parameters
    ----------
    planning_start : datetime
        First timestamp in horizon.
    planning_end : datetime
        End timestamp (exclusive).
    time_step : timedelta
        Resolution between steps.

    Returns
    -------
    list[datetime]
        Ordered planning timestamps.

    Side Effects
    ------------
    None.

    Raises
    ------
    ValueError
        May occur indirectly if invalid timedeltas are passed and division fails.

    Example
    -------
    >>> build_planning_horizon(
    ...     datetime(2024, 1, 1, 8, 0),
    ...     datetime(2024, 1, 1, 9, 0),
    ...     timedelta(minutes=15),
    ... )
    """
    planning_length = planning_end - planning_start
    return [
        planning_start + k * time_step
        for k in range(int(planning_length / time_step))
    ]


def _ts_to_index(
    ts: datetime,
    planning_start: datetime,
    time_step: timedelta,
) -> int:
    """Map absolute timestamp to optimization index using ceiling rule.

    Purpose
    -------
    Align arrival/departure timestamps with discrete optimization slots.

    Parameters
    ----------
    ts : datetime
        Timestamp to convert.
    planning_start : datetime
        Horizon start.
    time_step : timedelta
        Resolution.

    Returns
    -------
    int
        Optimization index.

    Side Effects
    ------------
    None.

    Raises
    ------
    OverflowError
        Possible if datetime arithmetic exceeds range.
    """
    return int(math.ceil((ts - planning_start) / time_step))


def _initialize_system_and_fleet(
    input_file_path: str,
    planning_horizon: list[datetime],
) -> tuple[MultiClusterSystem, EVFleet]:
    """Build legacy datafev cluster/fleet objects from simplified Excel input.

    Purpose
    -------
    Bridge modern input schema (`utils.input_parser`) to legacy objects expected
    by capability and scheduling algorithms.

    Parameters
    ----------
    input_file_path : str
        Excel path containing `Planning`, `Fleet` and cluster sheets.
    planning_horizon : list[datetime]
        Discrete planning timestamps.

    Returns
    -------
    tuple[MultiClusterSystem, EVFleet]
        Initialized multicluster system and fleet objects.

    Side Effects
    ------------
    Allocates and mutates legacy datafev objects.

    Raises
    ------
    ValueError
        Propagated from parsing if sheets/columns are invalid.
    """
    clusters_dict, fleet_df = parse_xlsx_input(file_path=input_file_path)

    mcsystem = MultiClusterSystem("multicluster")
    for cid, df_cc in clusters_dict.items():
        df_legacy = df_cc.rename(
            columns={
                "p_max_ch_kW": "cu_p_ch_max (kW)",
                "p_max_ds_kW": "cu_p_ds_max (kW)",
                "efficiency": "cu_eff",
            }
        )
        charger_cluster = ChargerCluster(str(cid), df_legacy)
        mcsystem.add_cc(charger_cluster)

    fleet_df_legacy = fleet_df.copy()
    fleet_df_legacy["estimated_arrival_time"] = fleet_df_legacy["arrival_time"]
    fleet_df_legacy["estimated_departure_time"] = fleet_df_legacy["departure_time"]
    fleet_df_legacy["estimated_arrival_SOC"] = fleet_df_legacy["initial_soc"]
    fleet_df_legacy["target_departure_SOC"] = fleet_df_legacy["target_soc"]
    fleet_df_legacy["min_allowed_SOC"] = fleet_df_legacy["min_allowed_soc"]
    fleet_df_legacy["max_allowed_SOC"] = fleet_df_legacy["max_allowed_soc"]

    fleet = EVFleet("fleet", fleet_df_legacy, planning_horizon)

    for _, row in fleet_df.iterrows():
        ev_id = row["vehicle_id"]
        if ev_id in fleet.objects:
            ev = fleet.objects[ev_id]
            ev.use_target_soc = bool(row["use_target_soc"])
            ev.exact_target_soc = bool(row.get("exact_target_soc", 0))
            ev.cluster_target = str(row["target_cluster"])

    return mcsystem, fleet


def _initialize_system_and_fleet_from_dataframes(
    clusters_dict: dict[str, pd.DataFrame],
    fleet_df: pd.DataFrame,
    planning_horizon: list[datetime],
) -> tuple[MultiClusterSystem, EVFleet]:
    """Build legacy datafev cluster/fleet objects from parsed database dataframes.

    Purpose
    -------
    Bridge database input schema to legacy objects expected by capability
    and scheduling algorithms.

    Parameters
    ----------
    clusters_dict : dict[str, pd.DataFrame]
        Mapping of cluster ID to cluster charger dataframe
    fleet_df : pd.DataFrame
        Fleet dataframe
    planning_horizon : list[datetime]
        Discrete planning timestamps.

    Returns
    -------
    tuple[MultiClusterSystem, EVFleet]
        Initialized multicluster system and fleet objects.

    Side Effects
    ------------
    Allocates and mutates legacy datafev objects.
    """
    mcsystem = MultiClusterSystem("multicluster")
    for cid, df_cc in clusters_dict.items():
        df_legacy = df_cc.rename(
            columns={
                "p_max_ch_kW": "cu_p_ch_max (kW)",
                "p_max_ds_kW": "cu_p_ds_max (kW)",
                "efficiency": "cu_eff",
            }
        )
        charger_cluster = ChargerCluster(str(cid), df_legacy)
        mcsystem.add_cc(charger_cluster)

    fleet_df_legacy = fleet_df.copy()
    fleet_df_legacy["estimated_arrival_time"] = fleet_df_legacy["arrival_time"]
    fleet_df_legacy["estimated_departure_time"] = fleet_df_legacy["departure_time"]
    fleet_df_legacy["estimated_arrival_SOC"] = fleet_df_legacy["initial_soc"]
    fleet_df_legacy["target_departure_SOC"] = fleet_df_legacy["target_soc"]
    fleet_df_legacy["min_allowed_SOC"] = fleet_df_legacy["min_allowed_soc"]
    fleet_df_legacy["max_allowed_SOC"] = fleet_df_legacy["max_allowed_soc"]

    fleet = EVFleet("fleet", fleet_df_legacy, planning_horizon)

    for _, row in fleet_df.iterrows():
        ev_id = row["vehicle_id"]
        if ev_id in fleet.objects:
            ev = fleet.objects[ev_id]
            ev.use_target_soc = bool(row["use_target_soc"])
            ev.exact_target_soc = bool(row.get("exact_target_soc", 0))
            ev.cluster_target = str(row["target_cluster"])

    return mcsystem, fleet


def _build_cluster_milp_inputs(
    cc_id: str,
    cc: ChargerCluster,
    fleet: EVFleet,
    planning_start: datetime,
    time_step: timedelta,
):
    """Assemble per-cluster MILP parameter dictionaries from fleet objects.

    Purpose
    -------
    Create solver input dictionaries with consistent indexing and effective
    power limits (charger cap vs EV cap minimum).

    Parameters
    ----------
    cc_id : str
        Cluster identifier.
    cc : ChargerCluster
        Legacy cluster object with charger definitions.
    fleet : EVFleet
        Fleet object containing EV states and metadata.
    planning_start : datetime
        Planning start used for index conversion.
    time_step : timedelta
        Planning resolution.

    Returns
    -------
    dict | None
        Dictionary of MILP parameters or `None` if no EV belongs to cluster.

    Side Effects
    ------------
    None.

    Raises
    ------
    StopIteration
        If the cluster has no charging unit entries.
    """
    ev_ids_in_cluster = [
        ev_id
        for ev_id, ev in fleet.objects.items()
        if getattr(ev, "cluster_target", None) == str(cc_id)
    ]

    if not ev_ids_in_cluster:
        return None

    bcap: dict[str, float] = {}
    inisoc: dict[str, float] = {}
    arrtime: dict[str, int] = {}
    tarsoc: dict[str, float] = {}
    minsoc: dict[str, float] = {}
    maxsoc: dict[str, float] = {}
    ch_eff: dict[str, float] = {}
    ds_eff: dict[str, float] = {}
    pmax_pos: dict[str, float] = {}
    pmax_neg: dict[str, float] = {}
    deptime: dict[str, int] = {}
    use_tarsoc: dict[str, int] = {}
    use_exact_tarsoc: dict[str, int] = {}

    any_cu = next(iter(cc.chargers.values()))

    for ev_id in ev_ids_in_cluster:
        ev = fleet.objects[ev_id]

        bcap[ev_id] = ev.bCapacity / 3600.0
        inisoc[ev_id] = ev.soc[ev.t_arr]
        arrtime[ev_id] = _ts_to_index(ev.t_arr, planning_start, time_step)
        tarsoc[ev_id] = ev.soc_tar_at_t_dep_est
        minsoc[ev_id] = ev.minSoC
        maxsoc[ev_id] = ev.maxSoC

        ch_eff[ev_id] = any_cu.eff
        ds_eff[ev_id] = any_cu.eff

        ev_p_max_ch = getattr(ev, "p_max_ch", any_cu.p_max_ch)
        ev_p_max_ds = getattr(ev, "p_max_ds", any_cu.p_max_ds)

        pmax_pos[ev_id] = min(any_cu.p_max_ch, ev_p_max_ch)
        pmax_neg[ev_id] = min(any_cu.p_max_ds, ev_p_max_ds)

        deptime[ev_id] = _ts_to_index(ev.t_dep, planning_start, time_step)
        use_tarsoc[ev_id] = 1 if getattr(ev, "use_target_soc", True) else 0
        use_exact_tarsoc[ev_id] = 1 if getattr(ev, "exact_target_soc", False) else 0

    return {
        "ev_ids": ev_ids_in_cluster,
        "bcap": bcap,
        "inisoc": inisoc,
        "arrtime": arrtime,
        "tarsoc": tarsoc,
        "minsoc": minsoc,
        "maxsoc": maxsoc,
        "ch_eff": ch_eff,
        "ds_eff": ds_eff,
        "pmax_pos": pmax_pos,
        "pmax_neg": pmax_neg,
        "deptime": deptime,
        "use_tarsoc": use_tarsoc,
        "use_exact_tarsoc": use_exact_tarsoc,
    }


def _resolve_stage1_output_dir(output_dir: str | None) -> str:
    """Resolve canonical Stage-1 output directory path."""
    output_root = output_dir or os.path.join(os.getcwd(), DEFAULT_OUTPUT_ROOT_DIRNAME)
    output_root = os.path.abspath(output_root)
    if os.path.basename(output_root.rstrip(os.sep)) == STAGE1_OUTPUT_DIRNAME:
        stage1_output_dir = output_root
    else:
        stage1_output_dir = os.path.join(output_root, STAGE1_OUTPUT_DIRNAME)
    os.makedirs(stage1_output_dir, exist_ok=True)
    return stage1_output_dir


def _build_day_ahead_ev_summary(
    ev_power_df: pd.DataFrame,
    ev_soc_df: pd.DataFrame,
    cluster_id: str,
    cluster_inputs: dict[str, Any],
    price_series: pd.Series,
    step_hours: float,
    planning_start: datetime,
    time_step: timedelta,
) -> list[dict[str, Any]]:
    """Summarize EV-level day-ahead charging energy/cost for export."""
    records: list[dict[str, Any]] = []
    aligned_price = price_series.reindex(ev_power_df.index).astype(float)
    ev_costs = ev_power_df.mul(aligned_price, axis=0).sum(axis=0) * step_hours
    ev_energy = ev_power_df.sum(axis=0) * step_hours

    for ev_id in ev_power_df.columns:
        dep_idx = cluster_inputs["deptime"][ev_id]
        arr_idx = cluster_inputs["arrtime"][ev_id]
        scheduled_dep_soc = float(ev_soc_df.iloc[dep_idx][ev_id])
        records.append(
            {
                "vehicle_id": ev_id,
                "cluster_id": str(cluster_id),
                "arrival_time": planning_start + arr_idx * time_step,
                "departure_time": planning_start + dep_idx * time_step,
                "initial_soc": float(cluster_inputs["inisoc"][ev_id]),
                "target_soc": float(cluster_inputs["tarsoc"][ev_id]),
                "scheduled_departure_soc": scheduled_dep_soc,
                "charged_energy_kWh": float(ev_energy.get(ev_id, 0.0)),
                "total_charging_cost_eur": float(ev_costs.get(ev_id, 0.0)),
            }
        )

    return records


def _resolve_stage2_output_dir(
    stage1_output_dir: str,
    output_dir: str | None = None,
) -> str:
    """Resolve canonical Stage-2 output directory path."""
    if output_dir:
        output_root = os.path.abspath(output_dir)
    else:
        stage1_abs = os.path.abspath(stage1_output_dir)
        if os.path.basename(stage1_abs.rstrip(os.sep)) == STAGE1_OUTPUT_DIRNAME:
            output_root = os.path.dirname(stage1_abs)
        else:
            output_root = stage1_abs

    if os.path.basename(output_root.rstrip(os.sep)) == STAGE2_OUTPUT_DIRNAME:
        stage2_output_dir = output_root
    else:
        stage2_output_dir = os.path.join(output_root, STAGE2_OUTPUT_DIRNAME)
    os.makedirs(stage2_output_dir, exist_ok=True)
    return stage2_output_dir


def run_flex_potential_estimation(
    input_file_path: str,
    planning_start: datetime | None = None,
    planning_end: datetime | None = None,
    time_step: timedelta | None = None,
    solver_backend: str = "gurobi_direct",
    output_dir: str | None = None,
    capability_export_enabled: bool = True,
    capability_export_format: str = "xlsx",
    generate_plots: bool = True,
    run_kpi_analysis_enabled: bool = False,
    db_input_enabled: bool = False,
    db_export_enabled: bool = False,
) -> FlexPotentialEstimationArtifacts:
    """Run Stage-1 FLEX POTENTIAL ESTIMATION and return workflow artifacts.

    Purpose
    -------
    Compute downward/upward flexibility envelopes for each cluster, solve a
    day-ahead price-driven smart charging baseline, and export optional
    plots/files.

    Parameters
    ----------
    input_file_path : str
        Path to primary Excel input workbook (ignored if `db_input_enabled=True`).
    planning_start, planning_end, time_step : datetime | timedelta | None
        Optional planning override. When any is `None`, values are parsed from
        workbook `Planning` sheet or database.
    solver_backend : str
        Pyomo solver backend name. Example: ``"gurobi_direct"`` or ``"glpk"``.
    output_dir : str | None
        Output root directory. Stage-1 exports are written under
        ``<output_dir>/flex_potential_estimation``.
    capability_export_enabled : bool
        Enable Stage-1 timeseries export.
    capability_export_format : str
        Export format (`xlsx`, `csv`, `parquet`).
    generate_plots : bool
        Enable Stage-1 plots.
    run_kpi_analysis_enabled : bool
        Run offline KPI script after Stage-1.
    db_input_enabled : bool
        Read input data from database instead of Excel.
    db_export_enabled : bool
        Enable writing estimation results to DB.


    Returns
    -------
    FlexPotentialEstimationArtifacts
        Full in-memory artifact bundle for Stage-2/API.

    Side Effects
    ------------
    - Calls external solver.
    - Writes files in Stage-1 output directory if export/plot flags enabled.
    - Mutates `mcsystem` cluster capability fields.

    Raises
    ------
    ValueError
        For invalid planning/Excel schema values.
    RuntimeError
        Possible from solver backend or downstream routines.

    Example
    -------
    >>> artifacts = run_flex_potential_estimation(
    ...     input_file_path="inputs/stage1_sample_input.xlsx",
    ...     solver_backend="glpk",
    ... )
    """
    if db_input_enabled:
        clusters_dict, fleet_df = fetch_database_inputs()
    else:
        clusters_dict, fleet_df = None, None

    if planning_start is None or planning_end is None or time_step is None:
        planning_cfg = parse_planning_sheet(input_file_path)
        planning_start = planning_cfg["planning_start"]
        planning_end = planning_cfg["planning_end"]
        time_step = planning_cfg["time_step"]

    planning_horizon = build_planning_horizon(planning_start, planning_end, time_step)

    stage1_output_dir = _resolve_stage1_output_dir(output_dir)

    solver = SolverFactory(solver_backend)
    if db_input_enabled and clusters_dict is not None and fleet_df is not None:
        # Use database-loaded data
        mcsystem, fleet = _initialize_system_and_fleet_from_dataframes(
            clusters_dict=clusters_dict,
            fleet_df=fleet_df,
            planning_horizon=planning_horizon,
        )
    else:
        # Use Excel input
        mcsystem, fleet = _initialize_system_and_fleet(
            input_file_path=input_file_path,
            planning_horizon=planning_horizon,
        )

    opt_step = int(time_step.total_seconds())
    opt_horizon = list(range(len(planning_horizon) + 1))
    ts_index = [planning_start + t * time_step for t in range(len(planning_horizon))]
    soc_index = ts_index + [planning_end]

    if db_input_enabled:
        market_price_ts = fetch_day_ahead_prices_from_database(planning_timestamps=ts_index)
    else:
        market_price_ts = parse_day_ahead_prices_sheet(
            file_path=input_file_path,
            planning_timestamps=ts_index,
        )

    market_price_dict = {
        t: float(market_price_ts.iloc[t]) for t in range(len(planning_horizon))
    }
    step_hours = opt_step / 3600.0
    cluster_day_ahead_power_ts: dict[str, pd.Series] = {}
    cluster_day_ahead_ev_power_ts: dict[str, pd.DataFrame] = {}
    cluster_day_ahead_ev_soc_ts: dict[str, pd.DataFrame] = {}
    day_ahead_summary_records: list[dict[str, Any]] = []

    for cc_id, cc in mcsystem.clusters.items():
        cluster_inputs = _build_cluster_milp_inputs(
            cc_id=cc_id,
            cc=cc,
            fleet=fleet,
            planning_start=planning_start,
            time_step=time_step,
        )

        if cluster_inputs is None:
            continue

        _, _, p_max_cluster = compute_g2v_capability(
            solver=solver,
            opt_step=opt_step,
            opt_horizon=opt_horizon,
            bcap=cluster_inputs["bcap"],
            inisoc=cluster_inputs["inisoc"],
            arrtime=cluster_inputs["arrtime"],
            tarsoc=cluster_inputs["tarsoc"],
            minsoc=cluster_inputs["minsoc"],
            maxsoc=cluster_inputs["maxsoc"],
            ch_eff=cluster_inputs["ch_eff"],
            ds_eff=cluster_inputs["ds_eff"],
            pmax_pos=cluster_inputs["pmax_pos"],
            pmax_neg=cluster_inputs["pmax_neg"],
            deptime=cluster_inputs["deptime"],
            use_tarsoc=cluster_inputs["use_tarsoc"],
        )

        _, _, p_min_cluster = compute_v2g_capability(
            solver=solver,
            opt_step=opt_step,
            opt_horizon=opt_horizon,
            bcap=cluster_inputs["bcap"],
            inisoc=cluster_inputs["inisoc"],
            arrtime=cluster_inputs["arrtime"],
            tarsoc=cluster_inputs["tarsoc"],
            minsoc=cluster_inputs["minsoc"],
            maxsoc=cluster_inputs["maxsoc"],
            ch_eff=cluster_inputs["ch_eff"],
            ds_eff=cluster_inputs["ds_eff"],
            pmax_pos=cluster_inputs["pmax_pos"],
            pmax_neg=cluster_inputs["pmax_neg"],
            deptime=cluster_inputs["deptime"],
            use_tarsoc=cluster_inputs["use_tarsoc"],
        )

        p_max_series = pd.Series(
            {ts_index[t]: p_max_cluster[t] for t in range(len(planning_horizon))}
        )
        p_min_series = pd.Series(
            {ts_index[t]: p_min_cluster[t] for t in range(len(planning_horizon))}
        )

        downward_profile_kW = p_max_series.clip(lower=0.0)
        upward_profile_kW = (-p_min_series).clip(lower=0.0)

        forecast_connected_evs = pd.Series(
            {
                ts_index[t]: sum(
                    1
                    for ev_id in cluster_inputs["ev_ids"]
                    if cluster_inputs["arrtime"][ev_id]
                    <= t
                    < cluster_inputs["deptime"][ev_id]
                )
                for t in range(len(planning_horizon))
            }
        )

        downward_kwh = downward_profile_kW.sum() * opt_step / 3600.0
        upward_kwh = upward_profile_kW.sum() * opt_step / 3600.0

        cc.set_capability(
            summary={
                "downward_capability_kWh": downward_kwh,
                "upward_capability_kWh": upward_kwh,
            },
            timeseries=pd.DataFrame(
                {
                    "downward_capability_kW": downward_profile_kW,
                    "upward_capability_kW": upward_profile_kW,
                }
            ),
            forecast_connected_evs_ts=forecast_connected_evs,
        )

        p_ev_day_ahead, s_day_ahead, p_cc_day_ahead = compute_day_ahead_smart_charging_schedule(
            solver=solver,
            opt_step=opt_step,
            opt_horizon=opt_horizon,
            prices=market_price_dict,
            bcap=cluster_inputs["bcap"],
            inisoc=cluster_inputs["inisoc"],
            arrtime=cluster_inputs["arrtime"],
            tarsoc=cluster_inputs["tarsoc"],
            minsoc=cluster_inputs["minsoc"],
            maxsoc=cluster_inputs["maxsoc"],
            ch_eff=cluster_inputs["ch_eff"],
            pmax_pos=cluster_inputs["pmax_pos"],
            deptime=cluster_inputs["deptime"],
            use_tarsoc=cluster_inputs["use_tarsoc"],
            use_exact_tarsoc=cluster_inputs["use_exact_tarsoc"],
        )

        ev_power_df = pd.DataFrame.from_dict(p_ev_day_ahead, orient="index").sort_index()
        ev_power_df = ev_power_df.reindex(
            range(len(planning_horizon)), fill_value=0.0
        )
        ev_power_df.index = ts_index

        ev_soc_df = pd.DataFrame.from_dict(s_day_ahead, orient="index").sort_index()
        ev_soc_df = ev_soc_df.reindex(
            range(len(planning_horizon) + 1), fill_value=0.0
        )
        ev_soc_df.index = soc_index

        cluster_power_series = pd.Series(
            {ts_index[t]: p_cc_day_ahead[t] for t in range(len(planning_horizon))},
            dtype=float,
        )

        cluster_day_ahead_power_ts[cc_id] = cluster_power_series
        cluster_day_ahead_ev_power_ts[cc_id] = ev_power_df
        cluster_day_ahead_ev_soc_ts[cc_id] = ev_soc_df
        day_ahead_summary_records.extend(
            _build_day_ahead_ev_summary(
                ev_power_df=ev_power_df,
                ev_soc_df=ev_soc_df,
                cluster_id=cc_id,
                cluster_inputs=cluster_inputs,
                price_series=market_price_ts,
                step_hours=step_hours,
                planning_start=planning_start,
                time_step=time_step,
            )
        )

    cluster_capability_summary = mcsystem.get_capability_summary()
    cluster_capability_ts = mcsystem.get_capability_timeseries()
    connected_evs_ts = mcsystem.get_connected_evs_timeseries()
    day_ahead_ev_summary = pd.DataFrame(day_ahead_summary_records)
    if not day_ahead_ev_summary.empty:
        day_ahead_ev_summary = day_ahead_ev_summary.sort_values(
            by=["cluster_id", "vehicle_id"], ignore_index=True
        )

    print_capability_summary(cluster_capability_summary)

    if db_input_enabled:
        export_capability_to_database(
            cluster_capability_ts=cluster_capability_ts,
            connected_evs_ts=connected_evs_ts,
            cluster_power_ts=cluster_day_ahead_power_ts,
            enabled=db_export_enabled,
        )
        export_day_ahead_schedule_to_database(
            ev_summary=day_ahead_ev_summary,
    )
    else:
        export_capability_timeseries(
            cluster_capability_ts,
            base_path=stage1_output_dir,
            enabled=capability_export_enabled,
            export_format=capability_export_format,
            forecast_connected_evs_ts=connected_evs_ts,
            cluster_power_ts=cluster_day_ahead_power_ts,
        )
        export_day_ahead_schedule_results(
            market_price_ts=market_price_ts,
            cluster_power_ts=cluster_day_ahead_power_ts,
            cluster_ev_power_ts=cluster_day_ahead_ev_power_ts,
            cluster_ev_soc_ts=cluster_day_ahead_ev_soc_ts,
            ev_summary=day_ahead_ev_summary,
            base_path=stage1_output_dir,
            enabled=capability_export_enabled,
            export_format=capability_export_format,
        )

    if generate_plots:
        plot_cluster_capability_bands(
            cluster_capability_ts,
            output_dir=stage1_output_dir,
            forecast_connected_evs_ts=connected_evs_ts,
        )
        plot_aggregate_capability(cluster_capability_ts, output_dir=stage1_output_dir)

    if run_kpi_analysis_enabled:
        run_kpi_analysis(
            data_path=os.path.join(stage1_output_dir, "cluster_capability_timeseries.xlsx"),
            out_dir=stage1_output_dir,
        )

    return FlexPotentialEstimationArtifacts(
        planning_start=planning_start,
        planning_end=planning_end,
        time_step=time_step,
        planning_horizon=planning_horizon,
        opt_step=opt_step,
        opt_horizon=opt_horizon,
        output_dir=stage1_output_dir,
        solver_backend=solver_backend,
        solver=solver,
        mcsystem=mcsystem,
        fleet=fleet,
        cluster_capability_summary=cluster_capability_summary,
        cluster_capability_ts=cluster_capability_ts,
        connected_evs_ts=connected_evs_ts,
        market_price_ts=market_price_ts,
        cluster_day_ahead_power_ts=cluster_day_ahead_power_ts,
        cluster_day_ahead_ev_power_ts=cluster_day_ahead_ev_power_ts,
        cluster_day_ahead_ev_soc_ts=cluster_day_ahead_ev_soc_ts,
        day_ahead_ev_summary=day_ahead_ev_summary,
    )


def _mark_command_rejected(
    command_status_df: pd.DataFrame,
    cluster_id: str,
    reason: str,
    detail: str,
) -> pd.DataFrame:
    """Upsert rejection status row for a given cluster.

    Purpose
    -------
    Normalize status updates when validation/solve fails at different stages.

    Parameters
    ----------
    command_status_df : pd.DataFrame
        Existing command status table.
    cluster_id : str
        Cluster identifier to update.
    reason : str
        Machine-readable rejection code.
    detail : str
        Human-readable explanation.

    Returns
    -------
    pd.DataFrame
        Updated status table.

    Side Effects
    ------------
    Mutates the provided DataFrame in-place before returning it.
    """
    mask = command_status_df["cluster_id"] == cluster_id
    if mask.any():
        command_status_df.loc[mask, "status"] = "rejected"
        command_status_df.loc[mask, "reason"] = reason
        command_status_df.loc[mask, "detail"] = detail
    else:
        command_status_df.loc[len(command_status_df)] = {
            "cluster_id": cluster_id,
            "status": "rejected",
            "reason": reason,
            "detail": detail,
        }

    return command_status_df


def _normalize_tracking_mode(tracking_mode: str) -> str:
    normalized = (tracking_mode or "").strip().lower()
    if normalized not in {TRACKING_MODE_STRICT, TRACKING_MODE_BEST_EFFORT}:
        raise ValueError("Unsupported tracking_mode. Use 'strict' or 'best_effort'.")
    return normalized


def _build_cluster_tracking_report(
    command_band_df: pd.DataFrame,
    cluster_power_series: pd.Series,
    command_type: str,
    tolerance_kw: float,
) -> pd.DataFrame:
    delivered = cluster_power_series.astype(float)

    if command_type == ABSOLUTE_SETPOINT:
        if "p_set_kw" in command_band_df.columns:
            requested = command_band_df["p_set_kw"].astype(float)
        else:
            requested = ((command_band_df["p_min_kw"] + command_band_df["p_max_kw"]) / 2.0).astype(float)
        abs_error = (delivered - requested).abs()
        tracking_df = pd.DataFrame(
            {
                "requested_setpoint_kw": requested,
                "delivered_p_kw": delivered,
                "abs_error_kw": abs_error,
                "is_met": (abs_error <= tolerance_kw),
            },
            index=command_band_df.index,
        )
    else:
        p_min = command_band_df["p_min_kw"].astype(float)
        p_max = command_band_df["p_max_kw"].astype(float)
        below_violation = (p_min - delivered).clip(lower=0.0)
        above_violation = (delivered - p_max).clip(lower=0.0)
        band_violation = below_violation + above_violation
        tracking_df = pd.DataFrame(
            {
                "requested_p_min_kw": p_min,
                "requested_p_max_kw": p_max,
                "delivered_p_kw": delivered,
                "band_violation_kw": band_violation,
                "is_met": (band_violation <= tolerance_kw),
            },
            index=command_band_df.index,
        )

    tracking_df.index.name = "datetime"
    return tracking_df


def _summarize_tracking_report(tracking_df: pd.DataFrame) -> dict[str, float]:
    total_steps = int(len(tracking_df))
    matched_steps = int(tracking_df["is_met"].astype(bool).sum())
    ratio = (matched_steps / total_steps) if total_steps else 0.0
    error_col = "abs_error_kw" if "abs_error_kw" in tracking_df.columns else "band_violation_kw"
    err = tracking_df[error_col].astype(float)
    return {
        "matched_steps": float(matched_steps),
        "total_steps": float(total_steps),
        "match_ratio": float(ratio),
        "mean_abs_error_kw": float(err.mean()) if total_steps else 0.0,
        "max_abs_error_kw": float(err.max()) if total_steps else 0.0,
    }


def run_flex_aware_smart_charge_scheduling(
    artifacts: FlexPotentialEstimationArtifacts,
    command_type: str = ABSOLUTE_SETPOINT,
    command_strategy: str = "midpoint",
    tracking_mode: str = TRACKING_MODE_STRICT,
    match_tolerance_kw: float = 1e-3,
    commands_by_cluster: dict[str, pd.Series | pd.DataFrame] | None = None,
    output_dir: str | None = None,
    export_enabled: bool = True,
    export_format: str = "xlsx",
    generate_soc_plots: bool = True,
) -> FlexAwareSmartChargeSchedulingArtifacts:
    """Run Stage-2 FLEX-AWARE SMART CHARGE SCHEDULING.

    Purpose
    -------
    Validate incoming commands, solve per-cluster tracking schedules for
    accepted commands, and collect status/output artifacts.

    Parameters
    ----------
    artifacts : FlexPotentialEstimationArtifacts
        Stage-1 outputs and objects used by Stage-2.
    command_type : str
        `absolute_setpoint` or `flex_band`.
    command_strategy : str
        Built-in strategy used when external commands are not provided.
        Currently only ``"midpoint"``.
    tracking_mode : str
        `strict` enforces commands as hard constraints; `best_effort` softens
        command tracking and minimizes mismatch.
    match_tolerance_kw : float
        Absolute tolerance used for per-timestep `is_met` evaluation.
    commands_by_cluster : dict[str, pd.Series | pd.DataFrame] | None
        Optional externally provided command payload.
    output_dir : str | None
        Output root directory. Stage-2 exports are written under
        ``<output_dir>/flex_aware_smart_charge_scheduling``.
        If omitted, the sibling Stage-2 directory of Stage-1 artifacts is used.
    export_enabled : bool
        Enable Stage-2 file exports.
    export_format : str
        Export format (`xlsx` or `csv`).
    generate_soc_plots : bool
        Enable EV SoC plot generation.

    Returns
    -------
    FlexAwareSmartChargeSchedulingArtifacts
        Stage-2 status and trajectories.

    Side Effects
    ------------
    - Calls Stage-2 MILP solver for accepted clusters.
    - Mutates legacy cluster schedules/databanks.
    - Writes export/plot files when enabled.

    Raises
    ------
    ValueError
        If unsupported command strategy is used without external commands.

    Example
    -------
    >>> stage2 = run_flex_aware_smart_charge_scheduling(
    ...     artifacts=artifacts,
    ...     command_strategy="midpoint",
    ... )
    """
    stage2_command_status = pd.DataFrame(
        columns=["cluster_id", "status", "reason", "detail"]
    )
    stage2_command_band_ts: dict[str, pd.DataFrame] = {}
    stage2_setpoint_ts: dict[str, pd.Series] = {}
    stage2_cluster_power_ts: dict[str, pd.Series] = {}
    stage2_ev_power_ts: dict[str, pd.DataFrame] = {}
    stage2_ev_soc_ts: dict[str, pd.DataFrame] = {}
    stage2_tracking_report_ts: dict[str, pd.DataFrame] = {}
    stage2_tracking_summary: dict[str, dict[str, float]] = {}
    stage2_output_dir = _resolve_stage2_output_dir(
        stage1_output_dir=artifacts.output_dir,
        output_dir=output_dir,
    )
    normalized_tracking_mode = _normalize_tracking_mode(tracking_mode)
    if match_tolerance_kw < 0:
        raise ValueError("'match_tolerance_kw' must be non-negative.")

    normalized_command_type = (command_type or "").strip().lower()
    if normalized_command_type not in {ABSOLUTE_SETPOINT, FLEX_BAND}:
        raise ValueError(
            "Unsupported command_type. Use 'absolute_setpoint' or 'flex_band'."
        )

    if commands_by_cluster is None:
        if command_strategy != "midpoint":
            raise ValueError(
                "Unsupported command_strategy. Use 'midpoint' or provide commands_by_cluster."
            )
        if normalized_command_type == ABSOLUTE_SETPOINT:
            commands_by_cluster = generate_midpoint_setpoint_commands(
                artifacts.cluster_capability_ts
            )
        else:
            commands_by_cluster = generate_midpoint_flex_band_commands(
                artifacts.cluster_capability_ts
            )

    accepted_commands, stage2_command_status = validate_stage2_commands(
        commands_by_cluster,
        artifacts.cluster_capability_ts,
        command_type=normalized_command_type,
        enforce_envelope=(normalized_tracking_mode == TRACKING_MODE_STRICT),
    )
    stage2_price_dict = {
        t: float(artifacts.market_price_ts.iloc[t])
        for t in range(len(artifacts.planning_horizon))
    }

    for cc_id, cc in artifacts.mcsystem.clusters.items():
        if cc_id not in artifacts.cluster_capability_ts:
            continue

        if cc_id not in accepted_commands:
            continue

        cluster_inputs = _build_cluster_milp_inputs(
            cc_id=cc_id,
            cc=cc,
            fleet=artifacts.fleet,
            planning_start=artifacts.planning_start,
            time_step=artifacts.time_step,
        )
        if cluster_inputs is None:
            stage2_command_status = _mark_command_rejected(
                stage2_command_status,
                cluster_id=cc_id,
                reason="NO_EV_IN_CLUSTER",
                detail="No EV is assigned to this cluster in the planning horizon.",
            )
            continue

        command_band_df = accepted_commands[cc_id]
        p_min_dict = {
            t: float(command_band_df["p_min_kw"].iloc[t])
            for t in range(len(artifacts.planning_horizon))
        }
        p_max_dict = {
            t: float(command_band_df["p_max_kw"].iloc[t])
            for t in range(len(artifacts.planning_horizon))
        }
        setpoint_dict = None
        if "p_set_kw" in command_band_df.columns:
            setpoint_dict = {
                t: float(command_band_df["p_set_kw"].iloc[t])
                for t in range(len(artifacts.planning_horizon))
            }

        # Critical block:
        # 1) Solve MILP for a single accepted cluster.
        # 2) Convert raw solver output into aligned pandas objects.
        # 3) Persist cluster/system databanks for export and diagnostics.
        try:
            p_ev_stage2, s_stage2, p_cc_stage2 = compute_flex_aware_schedule(
                solver=artifacts.solver,
                opt_step=artifacts.opt_step,
                opt_horizon=artifacts.opt_horizon,
                prices=stage2_price_dict,
                setpoint=setpoint_dict,
                p_min=p_min_dict,
                p_max=p_max_dict,
                bcap=cluster_inputs["bcap"],
                inisoc=cluster_inputs["inisoc"],
                arrtime=cluster_inputs["arrtime"],
                tarsoc=cluster_inputs["tarsoc"],
                minsoc=cluster_inputs["minsoc"],
                maxsoc=cluster_inputs["maxsoc"],
                ch_eff=cluster_inputs["ch_eff"],
                ds_eff=cluster_inputs["ds_eff"],
                pmax_pos=cluster_inputs["pmax_pos"],
                pmax_neg=cluster_inputs["pmax_neg"],
                deptime=cluster_inputs["deptime"],
                use_tarsoc=cluster_inputs["use_tarsoc"],
                use_exact_tarsoc=cluster_inputs["use_exact_tarsoc"],
                tracking_mode=normalized_tracking_mode,
            )
        except RuntimeError as exc:
            stage2_command_status = _mark_command_rejected(
                stage2_command_status,
                cluster_id=cc_id,
                reason="INFEASIBLE_MILP",
                detail=str(exc),
            )
            continue

        ts_index = list(command_band_df.index)
        soc_index = ts_index + [artifacts.planning_end]

        ev_power_df = pd.DataFrame.from_dict(p_ev_stage2, orient="index").sort_index()
        ev_power_df = ev_power_df.reindex(
            range(len(artifacts.planning_horizon)), fill_value=0.0
        )
        ev_power_df.index = ts_index

        ev_soc_df = pd.DataFrame.from_dict(s_stage2, orient="index").sort_index()
        ev_soc_df = ev_soc_df.reindex(
            range(len(artifacts.planning_horizon) + 1), fill_value=0.0
        )
        ev_soc_df.index = soc_index

        cluster_power_series = pd.Series(
            {ts_index[t]: p_cc_stage2[t] for t in range(len(artifacts.planning_horizon))},
            dtype=float,
        )

        command_band_df = command_band_df.astype(float)
        stage2_command_band_ts[cc_id] = command_band_df.copy()
        if "p_set_kw" in command_band_df.columns:
            stage2_setpoint_ts[cc_id] = command_band_df["p_set_kw"].copy()
        stage2_cluster_power_ts[cc_id] = cluster_power_series
        stage2_ev_power_ts[cc_id] = ev_power_df
        stage2_ev_soc_ts[cc_id] = ev_soc_df
        tracking_report_df = _build_cluster_tracking_report(
            command_band_df=command_band_df,
            cluster_power_series=cluster_power_series,
            command_type=normalized_command_type,
            tolerance_kw=match_tolerance_kw,
        )
        stage2_tracking_report_ts[cc_id] = tracking_report_df
        tracking_summary = _summarize_tracking_report(tracking_report_df)
        stage2_tracking_summary[cc_id] = tracking_summary

        mask = (stage2_command_status["cluster_id"] == cc_id) & (
            stage2_command_status["status"] == "accepted"
        )
        if mask.any():
            stage2_command_status.loc[mask, "detail"] = (
                f"Matched {int(tracking_summary['matched_steps'])}/"
                f"{int(tracking_summary['total_steps'])} steps "
                f"(ratio={tracking_summary['match_ratio']:.3f}) within ±{match_tolerance_kw:.4f} kW."
            )
            if (
                normalized_tracking_mode == TRACKING_MODE_BEST_EFFORT
                and tracking_summary["matched_steps"] < tracking_summary["total_steps"]
            ):
                stage2_command_status.loc[mask, "reason"] = "BEST_EFFORT_DEVIATION"

        n_chargers = max(len(cc.chargers), 1)
        per_cu_power = cluster_power_series / n_chargers
        placeholder_soc = pd.Series(float("nan"), index=soc_index)
        for cu in cc.chargers.values():
            cu.set_schedule(
                artifacts.planning_start,
                per_cu_power.copy(),
                placeholder_soc.copy(),
            )

        cc_result_df = pd.DataFrame(
            {
                "timestamp": ts_index,
                "p_min_kw": command_band_df["p_min_kw"].values,
                "p_max_kw": command_band_df["p_max_kw"].values,
                "cluster_power_kW": cluster_power_series.values,
                "margin_to_min_kW": (
                    cluster_power_series - command_band_df["p_min_kw"]
                ).values,
                "margin_to_max_kW": (
                    command_band_df["p_max_kw"] - cluster_power_series
                ).values,
            }
        )
        if cc_id in stage2_setpoint_ts:
            setpoint_series = stage2_setpoint_ts[cc_id]
            cc_result_df["setpoint_kW"] = setpoint_series.values
            cc_result_df["tracking_error_kW"] = (
                cluster_power_series - setpoint_series
            ).values
        cc.databank_df = cc_result_df

    if stage2_cluster_power_ts:
        system_records = []
        for cc_id, p_cc in stage2_cluster_power_ts.items():
            cmd_band = stage2_command_band_ts[cc_id]
            p_set = stage2_setpoint_ts.get(cc_id)
            for ts in p_cc.index:
                row = {
                    "timestamp": ts,
                    "cluster_id": cc_id,
                    "p_min_kw": float(cmd_band.loc[ts, "p_min_kw"]),
                    "p_max_kw": float(cmd_band.loc[ts, "p_max_kw"]),
                    "cluster_power_kW": float(p_cc.loc[ts]),
                    "margin_to_min_kW": float(p_cc.loc[ts] - cmd_band.loc[ts, "p_min_kw"]),
                    "margin_to_max_kW": float(cmd_band.loc[ts, "p_max_kw"] - p_cc.loc[ts]),
                }
                if p_set is not None:
                    row["setpoint_kW"] = float(p_set.loc[ts])
                    row["tracking_error_kW"] = float(p_cc.loc[ts] - p_set.loc[ts])
                report_df = stage2_tracking_report_ts.get(cc_id)
                if report_df is not None and ts in report_df.index:
                    row["is_met"] = bool(report_df.loc[ts, "is_met"])
                system_records.append(row)
        artifacts.mcsystem.databank_df = pd.DataFrame(system_records)
    else:
        artifacts.mcsystem.databank_df = pd.DataFrame()

    stage2_command_status = stage2_command_status.sort_values(
        by="cluster_id", ignore_index=True
    )

    if export_enabled:
        export_stage2_results(
            command_status=stage2_command_status,
            cluster_command_band_ts=stage2_command_band_ts,
            cluster_setpoint_ts=stage2_setpoint_ts,
            cluster_power_ts=stage2_cluster_power_ts,
            cluster_ev_power_ts=stage2_ev_power_ts,
            cluster_ev_soc_ts=stage2_ev_soc_ts,
            cluster_tracking_report_ts=stage2_tracking_report_ts,
            base_path=stage2_output_dir,
            enabled=True,
            export_format=export_format,
        )

    if generate_soc_plots:
        plot_stage2_ev_soc_schedules(
            cluster_ev_soc_ts=stage2_ev_soc_ts,
            output_dir=stage2_output_dir,
        )

    return FlexAwareSmartChargeSchedulingArtifacts(
        output_dir=stage2_output_dir,
        command_status=stage2_command_status,
        cluster_command_band_ts=stage2_command_band_ts,
        cluster_setpoint_ts=stage2_setpoint_ts,
        cluster_power_ts=stage2_cluster_power_ts,
        cluster_ev_power_ts=stage2_ev_power_ts,
        cluster_ev_soc_ts=stage2_ev_soc_ts,
        cluster_tracking_report_ts=stage2_tracking_report_ts,
        cluster_tracking_summary=stage2_tracking_summary,
    )
