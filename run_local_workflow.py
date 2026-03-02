"""CLI entrypoint for local end-to-end workflow execution.

Purpose
-------
Provide a scriptable local runner for:
1. Stage-1 flex potential estimation.
2. Stage-2 flex-aware smart charge scheduling.

Stage-2 profile selection
-------------------------
`stage2_test_profile` selects a single Stage-2 command mode:
- `absolute_from_file`: read absolute setpoints from Excel (`p_set_kw`).
- `flex_band_from_file`: read flex bands from Excel (`p_min_kw`, `p_max_kw`).
- `absolute_midpoint`: generate midpoint absolute setpoints from Stage-1 envelope.
- `flex_band_midpoint`: generate midpoint flex bands from Stage-1 envelope.

Design note
-----------
In production/API mode these steps are split into two HTTP requests; this file
keeps a single-process convenience flow for developers and offline analysis.
"""

import os
import pandas as pd

from services.flex_workflow import (
    run_flex_aware_smart_charge_scheduling,
    run_flex_potential_estimation,
)
from utils.input_parser import parse_stage2_setpoints_sheet


# ---------------------------------------------------------------------------
# 1) RUNTIME CONFIGURATION
# ---------------------------------------------------------------------------

file_path = os.path.join("inputs", "stage1_sample_input.xlsx")

# Stage-1
solver_backend = "gurobi_direct"  # e.g. "gurobi_direct", "glpk"
capability_export_enabled = True
capability_export_format = "xlsx"  # "parquet", "csv", "xlsx"
generate_stage1_plots = True
run_kpi_analysis_enabled = True

# Stage-2
stage2_enabled = True
# Select exactly one profile from the list below.
stage2_test_profile = "flex_band_from_file"
# Available profiles:
# - absolute_from_file
# - flex_band_from_file
# - absolute_midpoint
# - flex_band_midpoint

stage2_profiles = {
    "absolute_from_file": {
        "command_type": "absolute_setpoint",
        "setpoints_file": os.path.join("inputs", "stage2_sample_absolute_setpoints.xlsx"),
    },
    "flex_band_from_file": {
        "command_type": "flex_band",
        "setpoints_file": os.path.join("inputs", "stage2_sample_flex_band_commands.xlsx"),
    },
    "absolute_midpoint": {
        "command_type": "absolute_setpoint",
        "setpoints_file": None,
    },
    "flex_band_midpoint": {
        "command_type": "flex_band",
        "setpoints_file": None,
    },
}

# When True, flex-band file profiles are regenerated from current Stage-1
# envelopes before Stage-2 parsing. This avoids stale-file rejections.
stage2_refresh_flex_band_file_from_stage1 = False

stage2_command_strategy = "midpoint"  # used when selected profile does not provide a file
stage2_tracking_mode = "best_effort"  # "strict" | "best_effort"
stage2_match_tolerance_kw = 1e-3
stage2_export_enabled = True
stage2_export_format = "xlsx"  # "csv", "xlsx"
generate_stage2_soc_plots = True


def _resolve_stage2_profile(selected_profile: str) -> dict:
    """Resolve a single Stage-2 profile by name."""
    if selected_profile in stage2_profiles:
        profile_cfg = stage2_profiles[selected_profile].copy()
        profile_cfg["name"] = selected_profile
        return profile_cfg

    supported = sorted(stage2_profiles.keys())
    raise ValueError(
        f"Unknown stage2_test_profile='{selected_profile}'. Supported values: {supported}"
    )


def _write_flex_band_input_from_stage1(
    stage1_artifacts,
    destination_path: str,
) -> None:
    """Write a conservative, feasible flex-band Excel from Stage-1 outputs.

    Design choice:
    - `p_min_kw = 0` avoids forcing discharge and keeps command permissive.
    - `p_max_kw = downward_capability_kW` preserves enough charging headroom.
    """
    rows: list[dict] = []
    for cc_id, envelope in stage1_artifacts.cluster_capability_ts.items():
        upper = envelope["downward_capability_kW"].astype(float).clip(lower=0.0)
        lower = pd.Series(0.0, index=envelope.index, dtype=float)
        for ts in envelope.index:
            rows.append(
                {
                    "cluster_id": str(cc_id),
                    "timestamp": ts,
                    "p_min_kw": float(lower.loc[ts]),
                    "p_max_kw": float(upper.loc[ts]),
                }
            )

    out_df = pd.DataFrame(rows).sort_values(["cluster_id", "timestamp"]).reset_index(
        drop=True
    )
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    with pd.ExcelWriter(destination_path) as writer:
        out_df.to_excel(writer, sheet_name="Setpoints", index=False)

    width = out_df["p_max_kw"] - out_df["p_min_kw"]
    print(
        "Regenerated feasible flex-band input from Stage-1 envelopes:",
        destination_path,
    )
    print(
        "  -> rows:",
        len(out_df),
        "| min width:",
        f"{float(width.min()):.4f}",
        "| max width:",
        f"{float(width.max()):.4f}",
        "| zero-width rows:",
        int((width.abs() <= 1e-9).sum()),
    )


if __name__ == "__main__":
    # Resolve paths relative to repository root.
    abs_path = os.path.dirname(os.path.abspath(__file__))
    abs_input_file_path = os.path.join(abs_path, file_path)
    output_root_dir = os.path.join(abs_path, "outputs")
    stage1_output_dir = os.path.join(output_root_dir, "flex_potential_estimation")
    stage2_output_dir = os.path.join(output_root_dir, "flex_aware_smart_charge_scheduling")
    os.makedirs(output_root_dir, exist_ok=True)

    stage1_artifacts = run_flex_potential_estimation(
        input_file_path=abs_input_file_path,
        solver_backend=solver_backend,
        output_dir=stage1_output_dir,
        capability_export_enabled=capability_export_enabled,
        capability_export_format=capability_export_format,
        generate_plots=generate_stage1_plots,
        run_kpi_analysis_enabled=run_kpi_analysis_enabled,
    )

    print("Planning window starts at:", stage1_artifacts.planning_start)
    print("Planning window ends at:", stage1_artifacts.planning_end)
    print("Length of one planning step:", stage1_artifacts.time_step)

    if stage2_enabled:
        run_cfg = _resolve_stage2_profile(stage2_test_profile)
        run_name = run_cfg["name"]
        command_type = run_cfg["command_type"]
        setpoints_file = run_cfg["setpoints_file"]

        # Optional external command file can be injected for Stage-2 replay.
        stage2_commands_by_cluster = None
        command_source = "midpoint"
        if setpoints_file:
            abs_stage2_setpoints_path = os.path.join(abs_path, setpoints_file)
            if (
                command_type == "flex_band"
                and stage2_refresh_flex_band_file_from_stage1
            ):
                _write_flex_band_input_from_stage1(
                    stage1_artifacts=stage1_artifacts,
                    destination_path=abs_stage2_setpoints_path,
                )
            elif not os.path.exists(abs_stage2_setpoints_path):
                raise FileNotFoundError(
                    "Stage-2 setpoints file not found for profile "
                    f"'{run_name}': {abs_stage2_setpoints_path}"
                )
            stage2_commands_by_cluster = parse_stage2_setpoints_sheet(
                abs_stage2_setpoints_path,
                command_type=command_type,
            )
            command_source = abs_stage2_setpoints_path

        print(
            "\nRunning Stage-2 flex-aware scheduling ... "
            f"profile={run_name}, command_type={command_type}, source={command_source}"
        )
        stage2_artifacts = run_flex_aware_smart_charge_scheduling(
            artifacts=stage1_artifacts,
            command_type=command_type,
            command_strategy=stage2_command_strategy,
            tracking_mode=stage2_tracking_mode,
            match_tolerance_kw=stage2_match_tolerance_kw,
            commands_by_cluster=stage2_commands_by_cluster,
            output_dir=stage2_output_dir,
            export_enabled=stage2_export_enabled,
            export_format=stage2_export_format,
            generate_soc_plots=generate_stage2_soc_plots,
        )

        for _, row in stage2_artifacts.command_status.iterrows():
            cc_id = row["cluster_id"]
            status = row["status"]
            reason = row["reason"]
            detail = row["detail"]
            if status == "accepted":
                print(f"  -> [{run_name}] Cluster {cc_id}: command accepted")
            else:
                print(
                    f"  -> [{run_name}] Cluster {cc_id}: "
                    f"command rejected ({reason}) {detail}"
                )
