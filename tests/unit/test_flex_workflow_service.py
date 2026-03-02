from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from services.flex_workflow import (
    FlexPotentialEstimationArtifacts,
    run_flex_aware_smart_charge_scheduling,
    run_flex_potential_estimation,
)


class _FakeCluster:
    def __init__(self):
        self.chargers = {"cu1": SimpleNamespace(set_schedule=lambda *args, **kwargs: None)}
        self.databank_df = pd.DataFrame()
        self._summary = {}
        self._ts = None
        self._connected = None

    def set_capability(self, summary, timeseries, forecast_connected_evs_ts):
        self._summary = summary
        self._ts = timeseries
        self._connected = forecast_connected_evs_ts


class _FakeMultiClusterSystem:
    def __init__(self):
        self.clusters = {"1": _FakeCluster()}
        self.databank_df = pd.DataFrame()

    def get_capability_summary(self):
        return {"1": self.clusters["1"]._summary}

    def get_capability_timeseries(self):
        return {"1": self.clusters["1"]._ts}

    def get_connected_evs_timeseries(self):
        return {"1": self.clusters["1"]._connected}


def _make_stage1_artifacts() -> FlexPotentialEstimationArtifacts:
    planning_start = datetime(2024, 1, 1, 8, 0)
    planning_end = datetime(2024, 1, 1, 8, 30)
    time_step = timedelta(minutes=15)
    planning_horizon = [planning_start, planning_start + time_step]
    idx = pd.DatetimeIndex(planning_horizon)
    capability_df = pd.DataFrame(
        {
            "downward_capability_kW": [5.0, 5.0],
            "upward_capability_kW": [5.0, 5.0],
        },
        index=idx,
    )

    return FlexPotentialEstimationArtifacts(
        planning_start=planning_start,
        planning_end=planning_end,
        time_step=time_step,
        planning_horizon=planning_horizon,
        opt_step=900,
        opt_horizon=[0, 1, 2],
        output_dir="outputs",
        solver_backend="gurobi_direct",
        solver=object(),
        mcsystem=SimpleNamespace(clusters={"1": _FakeCluster()}, databank_df=pd.DataFrame()),
        fleet=SimpleNamespace(objects={}),
        cluster_capability_summary={"1": {"downward_capability_kWh": 1.0, "upward_capability_kWh": 1.0}},
        cluster_capability_ts={"1": capability_df},
        connected_evs_ts={"1": pd.Series([1, 1], index=idx)},
    )


def test_run_flex_potential_estimation_reads_planning_sheet(monkeypatch, tmp_path):
    planning_start = datetime(2024, 1, 1, 8, 0)
    planning_end = datetime(2024, 1, 1, 8, 30)
    time_step = timedelta(minutes=15)

    monkeypatch.setattr(
        "services.flex_workflow.parse_planning_sheet",
        lambda _: {
            "planning_start": planning_start,
            "planning_end": planning_end,
            "time_step": time_step,
            "time_step_minutes": 15,
        },
    )

    fake_mcs = _FakeMultiClusterSystem()
    monkeypatch.setattr(
        "services.flex_workflow._initialize_system_and_fleet",
        lambda input_file_path, planning_horizon: (fake_mcs, SimpleNamespace(objects={})),
    )
    monkeypatch.setattr("services.flex_workflow.SolverFactory", lambda backend: object())

    monkeypatch.setattr(
        "services.flex_workflow.compute_g2v_capability",
        lambda **kwargs: ({}, {}, {0: 4.0, 1: 2.0}),
    )
    monkeypatch.setattr(
        "services.flex_workflow.compute_v2g_capability",
        lambda **kwargs: ({}, {}, {0: -1.0, 1: -3.0}),
    )

    monkeypatch.setattr("services.flex_workflow.print_capability_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.export_capability_timeseries", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.plot_cluster_capability_bands", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.plot_aggregate_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.run_kpi_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "services.flex_workflow._build_cluster_milp_inputs",
        lambda **kwargs: {
            "ev_ids": [],
            "bcap": {},
            "inisoc": {},
            "arrtime": {},
            "tarsoc": {},
            "minsoc": {},
            "maxsoc": {},
            "ch_eff": {},
            "ds_eff": {},
            "pmax_pos": {},
            "pmax_neg": {},
            "deptime": {},
            "use_tarsoc": {},
            "use_exact_tarsoc": {},
        },
    )

    artifacts = run_flex_potential_estimation(
        input_file_path=str(tmp_path / "dummy.xlsx"),
        solver_backend="gurobi_direct",
        output_dir=str(tmp_path),
        capability_export_enabled=False,
        generate_plots=False,
        run_kpi_analysis_enabled=False,
    )

    assert artifacts.planning_start == planning_start
    assert artifacts.planning_end == planning_end
    assert list(artifacts.cluster_capability_ts.keys()) == ["1"]
    assert artifacts.cluster_capability_ts["1"]["downward_capability_kW"].tolist() == [4.0, 2.0]
    assert artifacts.cluster_capability_ts["1"]["upward_capability_kW"].tolist() == [1.0, 3.0]


def test_run_flex_aware_smart_charge_scheduling_marks_infeasible_cluster(monkeypatch):
    artifacts = _make_stage1_artifacts()

    monkeypatch.setattr(
        "services.flex_workflow.validate_stage2_commands",
        lambda commands, envelopes, **_kwargs: (
            {
                cc_id: pd.DataFrame(
                    {
                        "p_min_kw": series.astype(float),
                        "p_max_kw": series.astype(float),
                        "p_set_kw": series.astype(float),
                    },
                    index=series.index,
                )
                for cc_id, series in commands.items()
            },
            pd.DataFrame(
                [
                    {
                        "cluster_id": "1",
                        "status": "accepted",
                        "reason": "",
                        "detail": "ok",
                    }
                ]
            ),
        ),
    )
    monkeypatch.setattr(
        "services.flex_workflow._build_cluster_milp_inputs",
        lambda **kwargs: {
            "bcap": {"EV1": 50.0},
            "inisoc": {"EV1": 0.5},
            "arrtime": {"EV1": 0},
            "tarsoc": {"EV1": 0.8},
            "minsoc": {"EV1": 0.2},
            "maxsoc": {"EV1": 1.0},
            "ch_eff": {"EV1": 0.95},
            "ds_eff": {"EV1": 0.95},
            "pmax_pos": {"EV1": 7.2},
            "pmax_neg": {"EV1": 3.0},
            "deptime": {"EV1": 2},
            "use_tarsoc": {"EV1": 1},
            "use_exact_tarsoc": {"EV1": 0},
        },
    )

    def _raise_infeasible(**kwargs):
        raise RuntimeError("Stage-2 solve was not optimal (status=warning, termination=infeasible).")

    monkeypatch.setattr("services.flex_workflow.compute_flex_aware_schedule", _raise_infeasible)
    monkeypatch.setattr("services.flex_workflow.export_stage2_results", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.plot_stage2_ev_soc_schedules", lambda *_args, **_kwargs: None)

    idx = artifacts.cluster_capability_ts["1"].index
    commands = {"1": pd.Series([0.0, 0.0], index=idx, dtype=float)}
    out = run_flex_aware_smart_charge_scheduling(
        artifacts=artifacts,
        commands_by_cluster=commands,
        export_enabled=False,
        generate_soc_plots=False,
    )

    assert "1" in out.command_status["cluster_id"].tolist()
    row = out.command_status.loc[out.command_status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "rejected"
    assert row["reason"] == "INFEASIBLE_MILP"


def test_run_flex_aware_smart_charge_scheduling_best_effort_reports_tracking(monkeypatch):
    artifacts = _make_stage1_artifacts()

    monkeypatch.setattr(
        "services.flex_workflow.validate_stage2_commands",
        lambda commands, envelopes, **_kwargs: (
            {
                cc_id: pd.DataFrame(
                    {
                        "p_min_kw": series.astype(float),
                        "p_max_kw": series.astype(float),
                        "p_set_kw": series.astype(float),
                    },
                    index=series.index,
                )
                for cc_id, series in commands.items()
            },
            pd.DataFrame(
                [
                    {
                        "cluster_id": "1",
                        "status": "accepted",
                        "reason": "",
                        "detail": "ok",
                    }
                ]
            ),
        ),
    )
    monkeypatch.setattr(
        "services.flex_workflow._build_cluster_milp_inputs",
        lambda **kwargs: {
            "bcap": {"EV1": 50.0},
            "inisoc": {"EV1": 0.5},
            "arrtime": {"EV1": 0},
            "tarsoc": {"EV1": 0.8},
            "minsoc": {"EV1": 0.2},
            "maxsoc": {"EV1": 1.0},
            "ch_eff": {"EV1": 0.95},
            "ds_eff": {"EV1": 0.95},
            "pmax_pos": {"EV1": 7.2},
            "pmax_neg": {"EV1": 3.0},
            "deptime": {"EV1": 2},
            "use_tarsoc": {"EV1": 1},
            "use_exact_tarsoc": {"EV1": 0},
        },
    )
    monkeypatch.setattr(
        "services.flex_workflow.compute_flex_aware_schedule",
        lambda **kwargs: (
            {0: {"EV1": 1.0}, 1: {"EV1": 0.0}},
            {0: {"EV1": 0.5}, 1: {"EV1": 0.55}, 2: {"EV1": 0.6}},
            {0: 1.0, 1: 0.0},
        ),
    )
    monkeypatch.setattr("services.flex_workflow.export_stage2_results", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.flex_workflow.plot_stage2_ev_soc_schedules", lambda *_args, **_kwargs: None)

    idx = artifacts.cluster_capability_ts["1"].index
    commands = {"1": pd.Series([0.0, 0.0], index=idx, dtype=float)}
    out = run_flex_aware_smart_charge_scheduling(
        artifacts=artifacts,
        commands_by_cluster=commands,
        tracking_mode="best_effort",
        match_tolerance_kw=0.05,
        export_enabled=False,
        generate_soc_plots=False,
    )

    report = out.cluster_tracking_report_ts["1"]
    assert set(report.columns) == {
        "requested_setpoint_kw",
        "delivered_p_kw",
        "abs_error_kw",
        "is_met",
    }
    assert bool(report.iloc[0]["is_met"]) is False
    summary = out.cluster_tracking_summary["1"]
    assert summary["match_ratio"] < 1.0

    row = out.command_status.loc[out.command_status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "accepted"
    assert row["reason"] == "BEST_EFFORT_DEVIATION"
