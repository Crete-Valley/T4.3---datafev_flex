#!/usr/bin/env python3
"""Generate deterministic Stage-1/Stage-2 acceptance fixtures.

The Stage-1 workbooks mirror the current canonical input schema:
- `Planning`
- `Fleet`
- `Clusters`
- `DayAheadMarketPrices` with `timestamp` and `price_eur_per_kwh`

The generated Stage-2 workbooks exercise both absolute-setpoint and flex-band
validation paths against those Stage-1 fixtures.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
INPUTS_DIR = ROOT_DIR / "inputs"
DEFAULT_OUTPUT_DIR = INPUTS_DIR / "acceptance_cases"
SAMPLE_STAGE1_FILE = INPUTS_DIR / "stage1_sample_input.xlsx"
SAMPLE_STAGE2_ABSOLUTE_FILE = INPUTS_DIR / "stage2_sample_absolute_setpoints.xlsx"
SAMPLE_STAGE2_FLEX_BAND_FILE = INPUTS_DIR / "stage2_sample_flex_band_commands.xlsx"


def _write_workbook(file_path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(file_path) as writer:
        for sheet_name, dataframe in sheets.items():
            dataframe.to_excel(writer, sheet_name=sheet_name, index=False)


def _build_stage1_cases() -> dict[str, dict[str, pd.DataFrame]]:
    """Build Stage-1 workbooks aligned with the current sample input schema."""
    planning = pd.read_excel(SAMPLE_STAGE1_FILE, sheet_name="Planning")
    fleet = pd.read_excel(SAMPLE_STAGE1_FILE, sheet_name="Fleet")
    clusters = pd.read_excel(SAMPLE_STAGE1_FILE, sheet_name="Clusters")
    day_ahead_prices = pd.read_excel(
        SAMPLE_STAGE1_FILE,
        sheet_name="DayAheadMarketPrices",
    )

    cases: dict[str, dict[str, pd.DataFrame]] = {}

    cases["stage1_baseline_multi_cluster.xlsx"] = {
        "Planning": planning.copy(),
        "Fleet": fleet.copy(),
        "Clusters": clusters.copy(),
        "DayAheadMarketPrices": day_ahead_prices.copy(),
    }

    planning_short = planning.copy()
    planning_start = pd.to_datetime(planning_short.loc[0, "planning_start"])
    planning_short.loc[0, "planning_end"] = planning_start + pd.Timedelta(hours=5)
    fleet_single_cluster = fleet[fleet["target_cluster"].astype(str) == "1"].copy()
    clusters_single_cluster = clusters[clusters["cluster_id"].astype(str) == "1"].copy()

    cases["stage1_single_cluster_short_horizon.xlsx"] = {
        "Planning": planning_short,
        "Fleet": fleet_single_cluster,
        "Clusters": clusters_single_cluster,
        "DayAheadMarketPrices": day_ahead_prices[
            pd.to_datetime(day_ahead_prices["timestamp"]) < pd.to_datetime(planning_short.loc[0, "planning_end"])
        ].copy(),
    }

    clusters_no_v2g = clusters.copy()
    clusters_no_v2g.loc[
        clusters_no_v2g["cluster_id"].astype(str) == "2", "p_max_ds_kW"
    ] = 0.0
    fleet_no_v2g = fleet.copy()
    fleet_no_v2g.loc[
        fleet_no_v2g["target_cluster"].astype(str) == "2", "p_max_discharge_kW"
    ] = 0.0

    cases["stage1_no_v2g_cluster2.xlsx"] = {
        "Planning": planning.copy(),
        "Fleet": fleet_no_v2g,
        "Clusters": clusters_no_v2g,
        "DayAheadMarketPrices": day_ahead_prices.copy(),
    }

    return cases


def _drop_last_timestep_per_cluster(df: pd.DataFrame) -> pd.DataFrame:
    trimmed_parts: list[pd.DataFrame] = []
    for _, group in df.groupby("cluster_id", sort=False):
        group_sorted = group.sort_values("timestamp")
        if len(group_sorted) > 1:
            trimmed_parts.append(group_sorted.iloc[:-1].copy())
        else:
            trimmed_parts.append(group_sorted.copy())
    return pd.concat(trimmed_parts, ignore_index=True)


def _build_stage2_absolute_cases() -> dict[str, pd.DataFrame]:
    baseline = pd.read_excel(SAMPLE_STAGE2_ABSOLUTE_FILE, sheet_name="Setpoints")
    cases: dict[str, pd.DataFrame] = {}

    cases["stage2_absolute_accept_baseline.xlsx"] = baseline.copy()

    out_of_envelope = baseline.copy()
    out_of_envelope["p_set_kw"] = (
        pd.to_numeric(out_of_envelope["p_set_kw"], errors="coerce").fillna(0.0) + 500.0
    )
    cases["stage2_absolute_out_of_envelope.xlsx"] = out_of_envelope

    timestep_mismatch = _drop_last_timestep_per_cluster(baseline)
    cases["stage2_absolute_timestep_mismatch.xlsx"] = timestep_mismatch

    unknown_cluster = baseline[baseline["cluster_id"].astype(str) == "1"].copy()
    if unknown_cluster.empty:
        unknown_cluster = baseline.copy()
    unknown_cluster["cluster_id"] = 999
    cases["stage2_absolute_unknown_cluster.xlsx"] = unknown_cluster

    return cases


def _build_stage2_flex_band_cases() -> dict[str, pd.DataFrame]:
    baseline = pd.read_excel(SAMPLE_STAGE2_FLEX_BAND_FILE, sheet_name="Setpoints")
    cases: dict[str, pd.DataFrame] = {}

    cases["stage2_flex_band_accept_baseline.xlsx"] = baseline.copy()

    out_of_envelope = baseline.copy()
    out_of_envelope["p_max_kw"] = (
        pd.to_numeric(out_of_envelope["p_max_kw"], errors="coerce").fillna(0.0) + 500.0
    )
    cases["stage2_flex_band_out_of_envelope.xlsx"] = out_of_envelope

    invalid_range = baseline.copy()
    first_idx = invalid_range.index[0]
    invalid_range.loc[first_idx, "p_min_kw"] = (
        float(invalid_range.loc[first_idx, "p_max_kw"]) + 5.0
    )
    cases["stage2_flex_band_invalid_range.xlsx"] = invalid_range

    return cases


def _build_manifest_rows() -> list[dict[str, str]]:
    return [
        {
            "file_name": "stage1_baseline_multi_cluster.xlsx",
            "stage": "stage1",
            "command_type": "-",
            "expected_result": "SUCCESS",
            "description": "Baseline multi-cluster input mirrored from sample file.",
        },
        {
            "file_name": "stage1_single_cluster_short_horizon.xlsx",
            "stage": "stage1",
            "command_type": "-",
            "expected_result": "SUCCESS",
            "description": "Single-cluster and short horizon variant.",
        },
        {
            "file_name": "stage1_no_v2g_cluster2.xlsx",
            "stage": "stage1",
            "command_type": "-",
            "expected_result": "SUCCESS",
            "description": "Cluster 2 has zero discharge capability (no V2G).",
        },
        {
            "file_name": "stage2_absolute_accept_baseline.xlsx",
            "stage": "stage2",
            "command_type": "absolute_setpoint",
            "expected_result": "ACCEPT_OR_EXPLICIT_REJECTION",
            "description": "Baseline absolute commands for normal path testing.",
        },
        {
            "file_name": "stage2_absolute_out_of_envelope.xlsx",
            "stage": "stage2",
            "command_type": "absolute_setpoint",
            "expected_result": "OUT_OF_ENVELOPE",
            "description": "Absolute setpoints shifted above feasible envelope.",
        },
        {
            "file_name": "stage2_absolute_timestep_mismatch.xlsx",
            "stage": "stage2",
            "command_type": "absolute_setpoint",
            "expected_result": "TIMESTEP_MISMATCH",
            "description": "Last timestep removed per cluster.",
        },
        {
            "file_name": "stage2_absolute_unknown_cluster.xlsx",
            "stage": "stage2",
            "command_type": "absolute_setpoint",
            "expected_result": "UNKNOWN_CLUSTER",
            "description": "Cluster ids replaced with an unknown value (999).",
        },
        {
            "file_name": "stage2_flex_band_accept_baseline.xlsx",
            "stage": "stage2",
            "command_type": "flex_band",
            "expected_result": "ACCEPT_OR_EXPLICIT_REJECTION",
            "description": "Baseline flex-band commands for normal path testing.",
        },
        {
            "file_name": "stage2_flex_band_out_of_envelope.xlsx",
            "stage": "stage2",
            "command_type": "flex_band",
            "expected_result": "OUT_OF_ENVELOPE",
            "description": "Band upper bound shifted above feasible envelope.",
        },
        {
            "file_name": "stage2_flex_band_invalid_range.xlsx",
            "stage": "stage2",
            "command_type": "flex_band",
            "expected_result": "INVALID_BAND_RANGE",
            "description": "First row has p_min_kw > p_max_kw.",
        },
    ]


def generate_cases(output_dir: Path) -> None:
    """Write the complete acceptance matrix under `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, sheets in _build_stage1_cases().items():
        _write_workbook(output_dir / filename, sheets)

    for filename, dataframe in _build_stage2_absolute_cases().items():
        _write_workbook(output_dir / filename, {"Setpoints": dataframe})

    for filename, dataframe in _build_stage2_flex_band_cases().items():
        _write_workbook(output_dir / filename, {"Setpoints": dataframe})

    manifest_df = pd.DataFrame(_build_manifest_rows())
    manifest_df.to_csv(output_dir / "manifest.csv", index=False)


def clean_cases(output_dir: Path) -> None:
    if not output_dir.exists():
        print(f"Nothing to clean. Directory does not exist: {output_dir}")
        return

    removed_files: list[Path] = []
    for pattern in ("stage1_*.xlsx", "stage2_*.xlsx"):
        for file_path in sorted(output_dir.glob(pattern)):
            file_path.unlink()
            removed_files.append(file_path)

    manifest_path = output_dir / "manifest.csv"
    if manifest_path.exists():
        manifest_path.unlink()
        removed_files.append(manifest_path)

    if removed_files:
        print(f"Removed {len(removed_files)} generated files from: {output_dir}")
    else:
        print(f"Nothing to clean. No generated fixture files found in: {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic Stage-1/Stage-2 acceptance input files under "
            "inputs/acceptance_cases."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Target directory for generated files.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove generated fixture files instead of generating them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()

    if args.clean:
        clean_cases(output_dir=output_dir)
        return

    generate_cases(output_dir=output_dir)
    print(f"Generated acceptance input matrix in: {output_dir}")


if __name__ == "__main__":
    main()
