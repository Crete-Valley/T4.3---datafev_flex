"""Setpoint command helpers for Stage-2 scheduling.

Purpose
-------
Provide deterministic fallback command generation and strict schema/envelope
validation for externally provided Stage-2 commands.
"""

from __future__ import annotations

from typing import Dict

import pandas as pd


ABSOLUTE_SETPOINT = "absolute_setpoint"
FLEX_BAND = "flex_band"
SUPPORTED_COMMAND_TYPES = {ABSOLUTE_SETPOINT, FLEX_BAND}


def _normalize_command_type(command_type: str) -> str:
    normalized = (command_type or "").strip().lower()
    if normalized not in SUPPORTED_COMMAND_TYPES:
        raise ValueError(
            "Unsupported command_type. Use 'absolute_setpoint' or 'flex_band'."
        )
    return normalized


def generate_midpoint_flex_band_commands(
    cluster_capability_ts: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """Generate deterministic midpoint commands as degenerate flex bands.

    Parameters
    ----------
    cluster_capability_ts : Dict[str, pd.DataFrame]
        Stage-1 envelope data per cluster with columns
        `downward_capability_kW` and `upward_capability_kW`.

    Returns
    -------
    Dict[str, pd.DataFrame]
        Midpoint command payload per cluster with columns:
        `p_min_kw`, `p_max_kw`.
    """
    commands: Dict[str, pd.DataFrame] = {}
    for cc_id, envelope in cluster_capability_ts.items():
        upper = envelope["downward_capability_kW"].astype(float)
        lower = -envelope["upward_capability_kW"].astype(float)
        midpoint = (upper + lower) / 2.0
        commands[cc_id] = pd.DataFrame(
            {
                "p_min_kw": midpoint,
                "p_max_kw": midpoint,
            },
            index=envelope.index,
        )
    return commands


def generate_midpoint_setpoint_commands(
    cluster_capability_ts: Dict[str, pd.DataFrame],
) -> Dict[str, pd.Series]:
    """Generate default absolute setpoints at envelope midpoint.

    Parameters
    ----------
    cluster_capability_ts : Dict[str, pd.DataFrame]
        Stage-1 envelope data per cluster with columns
        `downward_capability_kW` and `upward_capability_kW`.

    Returns
    -------
    Dict[str, pd.Series]
        Midpoint command series per cluster.
    """
    commands: Dict[str, pd.Series] = {}
    for cc_id, envelope in cluster_capability_ts.items():
        upper = envelope["downward_capability_kW"].astype(float)
        lower = -envelope["upward_capability_kW"].astype(float)
        commands[cc_id] = ((upper + lower) / 2.0).astype(float)
    return commands


def _reject_record(cluster_id: str, reason: str, detail: str) -> dict[str, str]:
    return {
        "cluster_id": cluster_id,
        "status": "rejected",
        "reason": reason,
        "detail": detail,
    }


def validate_stage2_commands(
    commands_by_cluster: Dict[str, pd.Series | pd.DataFrame],
    cluster_capability_ts: Dict[str, pd.DataFrame],
    command_type: str = ABSOLUTE_SETPOINT,
    tolerance_kw: float = 1e-6,
    enforce_envelope: bool = True,
) -> tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """Validate Stage-2 commands and normalize to command-band payload.

    Parameters
    ----------
    commands_by_cluster : Dict[str, pd.Series | pd.DataFrame]
        Candidate commands indexed by timestamp per cluster.
    cluster_capability_ts : Dict[str, pd.DataFrame]
        Stage-1 envelope references.
    command_type : str
        `absolute_setpoint` or `flex_band`.
    tolerance_kw : float
        Numeric tolerance for envelope and bound checks.
    enforce_envelope : bool
        When `True`, reject commands outside Stage-1 envelopes.
        When `False`, skip envelope rejection and keep schema/timestep checks.

    Returns
    -------
    tuple[Dict[str, pd.DataFrame], pd.DataFrame]
        - accepted command bands (`p_min_kw`, `p_max_kw`, optional `p_set_kw`
          only for `absolute_setpoint`)
        - status dataframe (`cluster_id`, `status`, `reason`, `detail`)
    """
    normalized_type = _normalize_command_type(command_type)
    accepted: Dict[str, pd.DataFrame] = {}
    records: list[dict[str, str]] = []

    known_clusters = set(cluster_capability_ts.keys())
    provided_clusters = set(commands_by_cluster.keys())

    for cc_id in sorted(provided_clusters - known_clusters):
        records.append(
            _reject_record(
                cluster_id=cc_id,
                reason="UNKNOWN_CLUSTER",
                detail="Command references a cluster missing in Stage-1 results.",
            )
        )

    for cc_id, envelope in cluster_capability_ts.items():
        raw_command = commands_by_cluster.get(cc_id)
        if raw_command is None:
            records.append(
                _reject_record(
                    cluster_id=cc_id,
                    reason="MISSING_COMMAND",
                    detail="No command provided for this cluster.",
                )
            )
            continue

        expected_index = envelope.index
        lower = -envelope["upward_capability_kW"].astype(float)
        upper = envelope["downward_capability_kW"].astype(float)

        if normalized_type == ABSOLUTE_SETPOINT:
            if isinstance(raw_command, pd.DataFrame):
                if "p_set_kw" not in raw_command.columns:
                    records.append(
                        _reject_record(
                            cluster_id=cc_id,
                            reason="INVALID_COMMAND_SCHEMA",
                            detail="absolute_setpoint commands require 'p_set_kw'.",
                        )
                    )
                    continue
                command_series = raw_command["p_set_kw"]
            elif isinstance(raw_command, pd.Series):
                command_series = raw_command
            else:
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="INVALID_COMMAND_SCHEMA",
                        detail="absolute_setpoint commands must be a pandas Series.",
                    )
                )
                continue

            if command_series.index.duplicated().any():
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="DUPLICATE_TIMESTAMPS",
                        detail="Setpoint command contains duplicated timestamps.",
                    )
                )
                continue

            command_series = command_series.sort_index()
            if not command_series.index.equals(expected_index):
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="TIMESTEP_MISMATCH",
                        detail="Command timestamps do not match the planning horizon.",
                    )
                )
                continue

            command_series = pd.to_numeric(command_series, errors="coerce")
            if command_series.isna().any():
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="NAN_SETPOINT",
                        detail="Setpoint command contains NaN values.",
                    )
                )
                continue

            if enforce_envelope:
                violation_mask = (command_series < (lower - tolerance_kw)) | (
                    command_series > (upper + tolerance_kw)
                )
                if violation_mask.any():
                    first_bad_ts = command_series.index[violation_mask][0]
                    bad_val = float(command_series.loc[first_bad_ts])
                    lb = float(lower.loc[first_bad_ts])
                    ub = float(upper.loc[first_bad_ts])
                    records.append(
                        _reject_record(
                            cluster_id=cc_id,
                            reason="OUT_OF_ENVELOPE",
                            detail=(
                                f"t={first_bad_ts}: p_set={bad_val:.4f} kW outside "
                                f"[{lb:.4f}, {ub:.4f}] kW."
                            ),
                        )
                    )
                    continue

            accepted[cc_id] = pd.DataFrame(
                {
                    "p_min_kw": command_series.astype(float),
                    "p_max_kw": command_series.astype(float),
                    "p_set_kw": command_series.astype(float),
                },
                index=expected_index,
            )
        else:
            if not isinstance(raw_command, pd.DataFrame):
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="INVALID_COMMAND_SCHEMA",
                        detail="flex_band commands must be a pandas DataFrame.",
                    )
                )
                continue

            required_cols = {"p_min_kw", "p_max_kw"}
            missing_cols = required_cols - set(raw_command.columns)
            if missing_cols:
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="INVALID_COMMAND_SCHEMA",
                        detail=(
                            "flex_band commands are missing required columns: "
                            f"{sorted(missing_cols)}."
                        ),
                    )
                )
                continue

            command_df = raw_command.sort_index().copy()
            if "p_set_kw" in command_df.columns:
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="INVALID_COMMAND_SCHEMA",
                        detail="flex_band commands must not include 'p_set_kw'.",
                    )
                )
                continue

            if command_df.index.duplicated().any():
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="DUPLICATE_TIMESTAMPS",
                        detail="Flex-band command contains duplicated timestamps.",
                    )
                )
                continue

            if not command_df.index.equals(expected_index):
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="TIMESTEP_MISMATCH",
                        detail="Command timestamps do not match the planning horizon.",
                    )
                )
                continue

            command_df["p_min_kw"] = pd.to_numeric(command_df["p_min_kw"], errors="coerce")
            command_df["p_max_kw"] = pd.to_numeric(command_df["p_max_kw"], errors="coerce")

            if command_df[["p_min_kw", "p_max_kw"]].isna().any().any():
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="NAN_BAND",
                        detail="Flex-band command contains NaN values.",
                    )
                )
                continue

            invalid_range_mask = command_df["p_min_kw"] > (
                command_df["p_max_kw"] + tolerance_kw
            )
            if invalid_range_mask.any():
                ts_bad = command_df.index[invalid_range_mask][0]
                p_min_bad = float(command_df.loc[ts_bad, "p_min_kw"])
                p_max_bad = float(command_df.loc[ts_bad, "p_max_kw"])
                records.append(
                    _reject_record(
                        cluster_id=cc_id,
                        reason="INVALID_BAND_RANGE",
                        detail=(
                            f"t={ts_bad}: p_min={p_min_bad:.4f} kW exceeds "
                            f"p_max={p_max_bad:.4f} kW."
                        ),
                    )
                )
                continue

            if enforce_envelope:
                violation_mask = (command_df["p_min_kw"] < (lower - tolerance_kw)) | (
                    command_df["p_max_kw"] > (upper + tolerance_kw)
                )
                if violation_mask.any():
                    ts_bad = command_df.index[violation_mask][0]
                    p_min_bad = float(command_df.loc[ts_bad, "p_min_kw"])
                    p_max_bad = float(command_df.loc[ts_bad, "p_max_kw"])
                    lb = float(lower.loc[ts_bad])
                    ub = float(upper.loc[ts_bad])
                    records.append(
                        _reject_record(
                            cluster_id=cc_id,
                            reason="OUT_OF_ENVELOPE",
                            detail=(
                                f"t={ts_bad}: band=[{p_min_bad:.4f}, {p_max_bad:.4f}] kW "
                                f"outside [{lb:.4f}, {ub:.4f}] kW."
                            ),
                        )
                    )
                    continue

            accepted[cc_id] = command_df[["p_min_kw", "p_max_kw"]].astype(float)

        records.append(
            {
                "cluster_id": cc_id,
                "status": "accepted",
                "reason": "",
                "detail": "Command passed schema and envelope checks.",
            }
        )

    status_df = pd.DataFrame(records).sort_values(
        by=["cluster_id", "status"], ignore_index=True
    )
    return accepted, status_df


def validate_setpoint_commands(
    commands_by_cluster: Dict[str, pd.Series],
    cluster_capability_ts: Dict[str, pd.DataFrame],
    tolerance_kw: float = 1e-6,
) -> tuple[Dict[str, pd.Series], pd.DataFrame]:
    """Backward-compatible wrapper for absolute setpoint validation.

    Returns accepted commands as ``dict[str, pd.Series]`` to preserve legacy
    callers.
    """
    accepted_bands, status_df = validate_stage2_commands(
        commands_by_cluster=commands_by_cluster,
        cluster_capability_ts=cluster_capability_ts,
        command_type=ABSOLUTE_SETPOINT,
        tolerance_kw=tolerance_kw,
    )
    accepted_series = {
        cc_id: payload["p_set_kw"].copy() for cc_id, payload in accepted_bands.items()
    }
    return accepted_series, status_df
