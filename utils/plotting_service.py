"""Lightweight plotting helpers for capability and Stage-2 outputs.

Purpose
-------
Generate deterministic headless PNGs for reporting and debugging.
This module uses Matplotlib's Agg backend so it works in CI/container runs.
"""

from __future__ import annotations

import os
from typing import Dict

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")
plt.style.use("tableau-colorblind10")


def _ensure_output_dir(output_dir: str) -> None:
    """Create output directory if missing."""
    os.makedirs(output_dir, exist_ok=True)


def plot_cluster_capability_bands(
    cluster_capability_ts: Dict[str, pd.DataFrame],
    output_dir: str,
    forecast_connected_evs_ts: Dict[str, pd.Series] | None = None,
) -> None:
    """Save per-cluster Stage-1 capability plots.

    Parameters
    ----------
    cluster_capability_ts : Dict[str, pd.DataFrame]
        Stage-1 capability data per cluster.
    output_dir : str
        Destination folder.
    forecast_connected_evs_ts : Dict[str, pd.Series] | None
        Optional connected-EV profile to overlay on secondary axis.
    """
    _ensure_output_dir(output_dir)

    for cc_id, df in cluster_capability_ts.items():
        fig, ax1 = plt.subplots(figsize=(7.2, 4.2), dpi=220)
        ax1.fill_between(
            df.index,
            0,
            df["downward_capability_kW"],
            color="#4c72b0",
            alpha=0.25,
            label="Downward",
        )
        ax1.fill_between(
            df.index,
            0,
            -df["upward_capability_kW"],
            color="#dd8452",
            alpha=0.25,
            label="Upward",
        )
        ax1.plot(df.index, df["downward_capability_kW"], color="#4c72b0")
        ax1.plot(df.index, -df["upward_capability_kW"], color="#dd8452")
        ax1.set_ylabel("Net power (kW)")
        ax1.set_xlabel("Time")
        ax1.set_title(f"Cluster {cc_id} capability band")
        ax1.grid(True, alpha=0.3)

        if forecast_connected_evs_ts and cc_id in forecast_connected_evs_ts:
            ax2 = ax1.twinx()
            ax2.plot(
                forecast_connected_evs_ts[cc_id].index,
                forecast_connected_evs_ts[cc_id].values,
                color="#55a868",
                linestyle="--",
                marker="o",
                label="Connected EVs",
            )
            ax2.set_ylabel("Connected EVs (count)")
            ax2.grid(False)
            ax1.legend(loc="upper left")
            ax2.legend(loc="upper right")
        else:
            ax1.legend(loc="upper right")

        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(os.path.join(output_dir, f"cluster_{cc_id}_capability.png"))
        plt.close(fig)


def plot_aggregate_capability(
    cluster_capability_ts: Dict[str, pd.DataFrame],
    output_dir: str,
) -> None:
    """Save aggregate capability band by summing all clusters."""
    _ensure_output_dir(output_dir)

    # Assume aligned indices; reindex to union if needed
    all_index = next(iter(cluster_capability_ts.values())).index
    agg = pd.DataFrame(index=all_index)
    agg["downward_capability_kW"] = 0.0
    agg["upward_capability_kW"] = 0.0
    for df in cluster_capability_ts.values():
        agg["downward_capability_kW"] = agg["downward_capability_kW"].add(
            df["downward_capability_kW"], fill_value=0
        )
        agg["upward_capability_kW"] = agg["upward_capability_kW"].add(
            df["upward_capability_kW"], fill_value=0
        )

    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=220)
    ax.fill_between(
        agg.index,
        0,
        agg["downward_capability_kW"],
        color="#4c72b0",
        alpha=0.25,
        label="Downward",
    )
    ax.fill_between(
        agg.index,
        0,
        -agg["upward_capability_kW"],
        color="#dd8452",
        alpha=0.25,
        label="Upward",
    )
    ax.plot(agg.index, agg["downward_capability_kW"], color="#4c72b0")
    ax.plot(agg.index, -agg["upward_capability_kW"], color="#dd8452")
    ax.set_ylabel("Net power (kW)")
    ax.set_xlabel("Time")
    ax.set_title("Aggregate capability band (all clusters)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "aggregate_capability.png"))
    plt.close(fig)


def plot_stage2_ev_soc_schedules(
    cluster_ev_soc_ts: Dict[str, pd.DataFrame],
    output_dir: str,
) -> None:
    """Save per-cluster EV SoC trajectories from Stage-2 results."""
    _ensure_output_dir(output_dir)

    for cc_id, df in cluster_ev_soc_ts.items():
        if df.empty:
            continue

        fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=220)
        for ev_id in df.columns:
            ax.plot(df.index, df[ev_id], marker="o", linewidth=1.6, label=str(ev_id))

        ax.set_ylabel("SoC (-)")
        ax.set_xlabel("Time")
        ax.set_title(f"Cluster {cc_id} EV SoC schedules (Stage-2)")
        ax.set_ylim(0.0, 1.05)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", ncol=2, fontsize=8)

        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(os.path.join(output_dir, f"stage2_cluster_{cc_id}_ev_soc.png"))
        plt.close(fig)
