"""Output/export helpers for Stage-1 and Stage-2 artifacts.

Purpose
-------
Centralize file writing and console summary behavior so service modules can
focus on orchestration and optimization.
"""

import os

import pandas as pd


def print_capability_summary(cluster_capability_summary: dict[str, dict[str, float]]) -> None:
    """Print aggregate cluster flexibility to stdout.

    Parameters
    ----------
    cluster_capability_summary : dict[str, dict[str, float]]
        Per-cluster summary values with keys:
        `downward_capability_kWh`, `upward_capability_kWh`.

    Returns
    -------
    None

    Side Effects
    ------------
    Writes formatted text to stdout.

    Example
    -------
    >>> print_capability_summary({"1": {"downward_capability_kWh": 10.0, "upward_capability_kWh": 4.0}})
    """
    print("\nSummary of cluster flexibility capabilities (aggregated over horizon):")
    for cc_id, vals in cluster_capability_summary.items():
        print(
            f"Cluster {cc_id}: "
            f"Downward ≈ {vals['downward_capability_kWh']:.2f} kWh, "
            f"Upward ≈ {vals['upward_capability_kWh']:.2f} kWh"
        )


def export_capability_timeseries(
    cluster_capability_ts: dict[str, pd.DataFrame],
    base_path: str,
    enabled: bool = False,
    export_format: str = "parquet",
    forecast_connected_evs_ts: dict[str, pd.Series] | None = None,
) -> None:
    """Persist Stage-1 capability time-series in the requested format.

    Parameters
    ----------
    cluster_capability_ts : dict[str, pd.DataFrame]
        Per-cluster capability bands.
    base_path : str
        Output directory.
    enabled : bool
        If `False`, function returns immediately.
    export_format : str
        One of `csv`, `xlsx`, `parquet` (fallback/default).
    forecast_connected_evs_ts : dict[str, pd.Series] | None
        Optional connected-EV timeseries to be merged into export output.

    Returns
    -------
    None

    Side Effects
    ------------
    Creates output files under `base_path`.

    Example
    -------
    >>> export_capability_timeseries(cluster_capability_ts, "outputs", enabled=True, export_format="xlsx")
    """
    if not enabled:
        return

    fmt = export_format.lower()
    if fmt == "csv":
        for cc_id, df in cluster_capability_ts.items():
            df_out = df.copy()
            desired_cols = ["downward_capability_kW", "upward_capability_kW"]
            if forecast_connected_evs_ts and cc_id in forecast_connected_evs_ts:
                df_out["connected_evs"] = forecast_connected_evs_ts[cc_id]
                desired_cols.append("connected_evs")
            df_out = df_out.reindex(columns=desired_cols)
            csv_path = os.path.join(base_path, f"cluster_capability_{cc_id}.csv")
            df_out.to_csv(csv_path)
            print(f"Cluster {cc_id} time-series written to: {csv_path}")
    elif fmt == "xlsx":
        output_path = os.path.join(base_path, "cluster_capability_timeseries.xlsx")
        with pd.ExcelWriter(output_path) as writer:
            for cc_id, df in cluster_capability_ts.items():
                df_out = df.copy()
                desired_cols = ["downward_capability_kW", "upward_capability_kW"]
                if forecast_connected_evs_ts and cc_id in forecast_connected_evs_ts:
                    df_out["connected_evs"] = forecast_connected_evs_ts[cc_id]
                    desired_cols.append("connected_evs")
                df_out = df_out.reindex(columns=desired_cols)
                sheet_name = f"Cluster_{cc_id}"
                df_out.to_excel(writer, sheet_name=sheet_name)
        print(f"\nCluster capability time-series written to: {output_path}")
    else:
        # Default to parquet for compact and efficient IO in data pipelines.
        for cc_id, df in cluster_capability_ts.items():
            df_out = df.copy()
            desired_cols = ["downward_capability_kW", "upward_capability_kW"]
            if forecast_connected_evs_ts and cc_id in forecast_connected_evs_ts:
                df_out["connected_evs"] = forecast_connected_evs_ts[cc_id]
                desired_cols.append("connected_evs")
            df_out = df_out.reindex(columns=desired_cols)
            pq_path = os.path.join(base_path, f"cluster_capability_{cc_id}.parquet")
            df_out.to_parquet(pq_path)
            print(f"Cluster {cc_id} time-series written to: {pq_path}")


def _safe_sheet_name(prefix: str, cluster_id: str) -> str:
    """Build Excel-safe sheet names by truncating to 31 chars."""
    raw = f"{prefix}_{cluster_id}"
    return raw[:31]


def export_stage2_results(
    command_status: pd.DataFrame,
    cluster_command_band_ts: dict[str, pd.DataFrame],
    cluster_setpoint_ts: dict[str, pd.Series],
    cluster_power_ts: dict[str, pd.Series],
    cluster_ev_power_ts: dict[str, pd.DataFrame],
    cluster_ev_soc_ts: dict[str, pd.DataFrame],
    cluster_tracking_report_ts: dict[str, pd.DataFrame] | None,
    base_path: str,
    enabled: bool = False,
    export_format: str = "xlsx",
) -> None:
    """Persist Stage-2 command/scheduling outputs.

    Parameters
    ----------
    command_status : pd.DataFrame
        Cluster acceptance/rejection rows.
    cluster_command_band_ts : dict[str, pd.DataFrame]
        Requested command bands per cluster (`p_min_kw`, `p_max_kw`, optional
        `p_set_kw`).
    cluster_setpoint_ts, cluster_power_ts : dict[str, pd.Series]
        Optional absolute setpoint and achieved cluster power profiles.
    cluster_ev_power_ts, cluster_ev_soc_ts : dict[str, pd.DataFrame]
        EV-level Stage-2 trajectories per cluster.
    cluster_tracking_report_ts : dict[str, pd.DataFrame] | None
        Per-cluster tracking evaluation with requested/delivered power and
        `is_met` flags.
    base_path : str
        Output directory.
    enabled : bool
        If `False`, function returns without writing files.
    export_format : str
        Supported values: `xlsx`, `csv`.

    Returns
    -------
    None

    Side Effects
    ------------
    Writes result files to `base_path`.

    Raises
    ------
    ValueError
        For unsupported `export_format`.
    """
    if not enabled:
        return

    fmt = export_format.lower()
    if fmt == "csv":
        status_path = os.path.join(base_path, "stage2_command_status.csv")
        command_status.to_csv(status_path, index=False)

        for cc_id, band_df in cluster_command_band_ts.items():
            band_df.to_csv(
                os.path.join(base_path, f"stage2_cluster_{cc_id}_command_band.csv")
            )
        for cc_id, p_set in cluster_setpoint_ts.items():
            p_set.rename("setpoint_kW").to_frame().to_csv(
                os.path.join(base_path, f"stage2_cluster_{cc_id}_setpoint.csv")
            )
        for cc_id, p_cc in cluster_power_ts.items():
            p_cc.rename("cluster_power_kW").to_frame().to_csv(
                os.path.join(base_path, f"stage2_cluster_{cc_id}_power.csv")
            )
        for cc_id, df in cluster_ev_power_ts.items():
            df.to_csv(os.path.join(base_path, f"stage2_cluster_{cc_id}_ev_power.csv"))
        for cc_id, df in cluster_ev_soc_ts.items():
            df.to_csv(os.path.join(base_path, f"stage2_cluster_{cc_id}_ev_soc.csv"))
        if cluster_tracking_report_ts:
            for cc_id, report_df in cluster_tracking_report_ts.items():
                report_df.to_csv(
                    os.path.join(base_path, f"stage2_cluster_{cc_id}_tracking_report.csv")
                )

        print(f"Stage-2 results written to CSV files under: {base_path}")
        return

    # Backward-compatible fallback: unknown values are treated as xlsx.
    if fmt != "xlsx":
        fmt = "xlsx"

    output_path = os.path.join(base_path, "stage2_flex_scheduling_results.xlsx")
    with pd.ExcelWriter(output_path) as writer:
        command_status.to_excel(writer, sheet_name="command_status", index=False)

        for cc_id, band_df in cluster_command_band_ts.items():
            band_df.to_excel(
                writer, sheet_name=_safe_sheet_name("command_band", cc_id)
            )

        for cc_id, p_set in cluster_setpoint_ts.items():
            p_set.rename("setpoint_kW").to_frame().to_excel(
                writer, sheet_name=_safe_sheet_name("setpoint", cc_id)
            )

        for cc_id, p_cc in cluster_power_ts.items():
            p_cc.rename("cluster_power_kW").to_frame().to_excel(
                writer, sheet_name=_safe_sheet_name("cluster_power", cc_id)
            )

        for cc_id, df in cluster_ev_power_ts.items():
            df.to_excel(writer, sheet_name=_safe_sheet_name("ev_power", cc_id))

        for cc_id, df in cluster_ev_soc_ts.items():
            df.to_excel(writer, sheet_name=_safe_sheet_name("ev_soc", cc_id))
        if cluster_tracking_report_ts:
            for cc_id, report_df in cluster_tracking_report_ts.items():
                report_df.to_excel(
                    writer, sheet_name=_safe_sheet_name("tracking_report", cc_id)
                )

    print(f"Stage-2 scheduling results written to: {output_path}")
