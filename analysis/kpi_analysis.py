"""
KPI extraction and plotting for cluster capability time-series.

Reads the generated Stage-1 capability workbook, computes flexibility KPIs,
and emits publication-ready plots next to Stage-1 exports.
Run with:
    venv/bin/python analysis/kpi_analysis.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import matplotlib.pyplot as plt
import pandas as pd


plt.style.use("tableau-colorblind10")


@dataclass
class ClusterKPI:
    """Derived flexibility KPIs for a single cluster over one planning horizon."""

    cluster: str
    downward_energy_kwh: float
    upward_energy_kwh: float
    peak_downward_kw: float
    peak_upward_kw: float
    avg_downward_kw: float
    avg_upward_kw: float
    availability_pct: float
    avg_connected_evs: float
    max_connected_evs: int
    avg_power_per_connected_ev_kw: float
    balance_ratio: float
    max_ramp_kw_per_step: float


def load_cluster_timeseries(path: Path) -> Dict[str, pd.DataFrame]:
    """Load per-cluster capability sheets from Stage-1 Excel export.

    Parameters
    ----------
    path : Path
        Workbook path (typically
        ``outputs/flex_potential_estimation/cluster_capability_timeseries.xlsx`` for CLI
        or ``outputs/jobs/<stage1_id>/flex_potential_estimation/cluster_capability_timeseries.xlsx``
        for API jobs).

    Returns
    -------
    Dict[str, pd.DataFrame]
        Mapping ``cluster_id -> dataframe`` indexed by timestamp.

    Side Effects
    ------------
    Reads data from disk.

    Raises
    ------
    FileNotFoundError
        If workbook path does not exist.
    ValueError
        If expected timestamp column is missing or not parseable.
    """
    xls = pd.ExcelFile(path)
    clusters: Dict[str, pd.DataFrame] = {}
    for name in xls.sheet_names:
        df = pd.read_excel(xls, name).rename(columns={"Unnamed: 0": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
        clusters[name] = df
    return clusters


def infer_step_hours(df: pd.DataFrame) -> float:
    """Infer the time resolution in hours from the timestamp index."""
    step = df.index.to_series().diff().dropna().mode().iloc[0]
    return step.total_seconds() / 3600.0


def compute_cluster_kpi(cluster_id: str, df: pd.DataFrame, step_hours: float) -> ClusterKPI:
    """Compute KPI bundle for one cluster.

    Parameters
    ----------
    cluster_id : str
        Cluster identifier.
    df : pd.DataFrame
        Time-indexed capability dataframe with at least
        ``downward_capability_kW``, ``upward_capability_kW`` and
        ``connected_evs`` columns.
    step_hours : float
        Time-step duration in hours.

    Returns
    -------
    ClusterKPI
        Aggregated KPI metrics for the cluster.

    Side Effects
    ------------
    None.

    Raises
    ------
    KeyError
        If required columns are missing.
    """
    down_energy = (df["downward_capability_kW"] * step_hours).sum()
    up_energy = (df["upward_capability_kW"] * step_hours).sum()

    max_down = df["downward_capability_kW"].max()
    max_up = df["upward_capability_kW"].max()

    availability = (df["downward_capability_kW"] > 0).mean() * 100.0

    total_ev_hours = df["connected_evs"].sum() * step_hours
    avg_power_per_ev = (down_energy / total_ev_hours) if total_ev_hours else 0.0

    balance = (up_energy / down_energy) if down_energy else 0.0
    max_ramp = df["downward_capability_kW"].diff().abs().max()

    return ClusterKPI(
        cluster=cluster_id,
        downward_energy_kwh=down_energy,
        upward_energy_kwh=up_energy,
        peak_downward_kw=max_down,
        peak_upward_kw=max_up,
        avg_downward_kw=df["downward_capability_kW"].mean(),
        avg_upward_kw=df["upward_capability_kW"].mean(),
        availability_pct=availability,
        avg_connected_evs=df["connected_evs"].mean(),
        max_connected_evs=int(df["connected_evs"].max()),
        avg_power_per_connected_ev_kw=avg_power_per_ev,
        balance_ratio=balance,
        max_ramp_kw_per_step=max_ramp,
    )


def aggregate_timeseries(clusters: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Aggregate capability and connected-EV series over all clusters.

    Parameters
    ----------
    clusters : Dict[str, pd.DataFrame]
        Per-cluster time series returned by :func:`load_cluster_timeseries`.

    Returns
    -------
    pd.DataFrame
        System-level aggregate dataframe indexed by planning timestamps.

    Side Effects
    ------------
    None.
    """
    base_index = next(iter(clusters.values())).index
    agg = pd.DataFrame(index=base_index)
    agg["downward_capability_kW"] = 0.0
    agg["upward_capability_kW"] = 0.0
    agg["connected_evs"] = 0.0
    for df in clusters.values():
        agg["downward_capability_kW"] += df["downward_capability_kW"]
        agg["upward_capability_kW"] += df["upward_capability_kW"]
        agg["connected_evs"] += df["connected_evs"]
    return agg


def plot_aggregate_capability(agg_df: pd.DataFrame, path: Path) -> None:
    """Generate aggregate capability-band figure and save to disk.

    Parameters
    ----------
    agg_df : pd.DataFrame
        System-level aggregate series.
    path : Path
        Output image path (e.g., PNG).

    Returns
    -------
    None

    Side Effects
    ------------
    Writes image file to disk.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=320)
    ax.fill_between(
        agg_df.index,
        0,
        agg_df["downward_capability_kW"],
        color="#4c72b0",
        alpha=0.25,
        label="Downward capability",
    )
    ax.fill_between(
        agg_df.index,
        0,
        -agg_df["upward_capability_kW"],
        color="#dd8452",
        alpha=0.25,
        label="Upward capability",
    )
    ax.plot(agg_df.index, agg_df["downward_capability_kW"], color="#4c72b0")
    ax.plot(agg_df.index, -agg_df["upward_capability_kW"], color="#dd8452")
    ax.set_ylabel("Net power (kW)")
    ax.set_xlabel("Time")
    ax.set_title("Aggregate flexibility band (all clusters)")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_energy_by_cluster(kpi_df: pd.DataFrame, path: Path) -> None:
    """Plot downward/upward energy potential bars per cluster."""
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=320)
    ax.bar(kpi_df["cluster"], kpi_df["downward_energy_kwh"], label="Downward (kWh)", color="#4c72b0")
    ax.bar(kpi_df["cluster"], -kpi_df["upward_energy_kwh"], label="Upward (kWh)", color="#dd8452")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Energy (kWh) over horizon")
    ax.set_title("Energy potential by cluster")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_connected_evs(clusters: Dict[str, pd.DataFrame], path: Path) -> None:
    """Plot connected-EV trajectories per cluster and save figure."""
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=320)
    for cid, df in clusters.items():
        ax.plot(df.index, df["connected_evs"], marker="o", label=cid)
    ax.set_ylabel("Connected EVs (count)")
    ax.set_xlabel("Time")
    ax.set_title("Forecast connected EVs by cluster")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _resolve_default_data_path() -> Path:
    """Resolve default capability workbook path with backward compatibility."""
    new_path = Path("outputs/flex_potential_estimation/cluster_capability_timeseries.xlsx")
    old_path = Path("outputs/cluster_capability_timeseries.xlsx")
    if new_path.exists():
        return new_path
    if old_path.exists():
        return old_path
    return new_path


def main(data_path: str | Path | None = None, out_dir: str | Path | None = None) -> None:
    """Run end-to-end KPI extraction and plot generation.

    Workflow
    --------
    1. Load cluster capability workbook from Stage-1 exports.
    2. Compute per-cluster and aggregate KPIs.
    3. Export CSV summaries and PNG figures.
    4. Print concise KPI summary to stdout.

    Returns
    -------
    None
    """
    resolved_data_path = Path(data_path) if data_path is not None else _resolve_default_data_path()
    resolved_out_dir = Path(out_dir) if out_dir is not None else resolved_data_path.parent
    resolved_out_dir.mkdir(parents=True, exist_ok=True)

    clusters = load_cluster_timeseries(resolved_data_path)
    step_hours = infer_step_hours(next(iter(clusters.values())))

    kpis = [compute_cluster_kpi(cid, df, step_hours) for cid, df in clusters.items()]
    kpi_df = pd.DataFrame([k.__dict__ for k in kpis])
    kpi_df.to_csv(resolved_out_dir / "kpi_summary.csv", index=False)

    agg_df = aggregate_timeseries(clusters)
    agg_df.to_csv(resolved_out_dir / "aggregate_timeseries.csv")

    # Plots
    plot_aggregate_capability(agg_df, resolved_out_dir / "plot_aggregate_capability.png")
    plot_energy_by_cluster(kpi_df, resolved_out_dir / "plot_energy_by_cluster.png")
    plot_connected_evs(clusters, resolved_out_dir / "plot_connected_evs.png")

    print("Per-cluster KPI summary:")
    print(kpi_df.round(2))
    print("\nAggregate (all clusters):")
    down_energy = (agg_df["downward_capability_kW"] * step_hours).sum()
    up_energy = (agg_df["upward_capability_kW"] * step_hours).sum()
    print(
        pd.Series(
            {
                "downward_energy_kwh": down_energy,
                "upward_energy_kwh": up_energy,
                "peak_downward_kw": agg_df["downward_capability_kW"].max(),
                "peak_upward_kw": agg_df["upward_capability_kW"].max(),
                "avg_connected_evs": agg_df["connected_evs"].mean(),
            }
        ).round(2)
    )


if __name__ == "__main__":
    main()
