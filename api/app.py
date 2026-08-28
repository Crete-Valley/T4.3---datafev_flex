"""HTTP API surface for the flex workflow service.

Purpose
-------
Expose Stage-1 and Stage-2 orchestration over FastAPI with short-lived
in-memory caching between the two calls.

API contract
------------
- ``POST /v1/flex-potential-estimation``: receives primary Excel workbook and
  returns ``stage1_id``.
- ``POST /v1/flex-aware-smart-charge-scheduling``: consumes ``stage1_id`` and
  optional setpoint payload (JSON or uploaded Excel).
- ``GET /healthz`` and ``GET /readyz``: liveness/readiness checks.

Dependencies
------------
- `services.flex_workflow` for business logic.
- `api.cache.TTLObjectCache` for temporary Stage-1 artifact persistence.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timedelta
from typing import Annotated, Dict, List
from uuid import uuid4

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from api.cache import TTLObjectCache
from algorithms.scheduling.flex_aware_scheduling import (
    TRACKING_MODE_STRICT,
)
from services.flex_workflow import (
    run_flex_aware_smart_charge_scheduling,
    run_flex_potential_estimation,
)
from utils.flex_command_utils import ABSOLUTE_SETPOINT, FLEX_BAND
from utils.input_parser import parse_stage2_setpoints_sheet


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read integer environment variable with safe fallback.

    Parameters
    ----------
    name : str
        Environment variable name.
    default : int
        Fallback value when variable is missing/invalid.
    minimum : int
        Lower bound for accepted values.

    Returns
    -------
    int
        Parsed integer or fallback default.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return default
    return value


_solver_env = os.getenv("DEFAULT_SOLVER_BACKEND")
DEFAULT_SOLVER_BACKEND = (_solver_env.strip() if _solver_env is not None else "") or "gurobi_direct"
DEFAULT_OUTPUT_DIR = os.getenv("DEFAULT_OUTPUT_DIR")
STAGE1_CACHE_TTL_SECONDS = _env_int("STAGE1_CACHE_TTL_SECONDS", default=900, minimum=1)
STAGE1_CACHE_MAX_ITEMS = _env_int("STAGE1_CACHE_MAX_ITEMS", default=128, minimum=1)
DEFAULT_OUTPUT_ROOT_DIRNAME = "outputs"
JOB_OUTPUT_DIRNAME = "jobs"

app = FastAPI(title="datafev_flex API", version="0.2.0")
stage1_cache = TTLObjectCache(
    ttl_seconds=STAGE1_CACHE_TTL_SECONDS,
    max_items=STAGE1_CACHE_MAX_ITEMS,
)
latest_planning_data: dict[str, object] | None = None


def _serialize_forecast_artifacts(artifacts) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Convert workflow artifacts into JSON-compatible API records."""
    cluster_forecasts: list[dict[str, object]] = []
    capability_ts = getattr(artifacts, "cluster_capability_ts", None)
    connected_ts = getattr(artifacts, "connected_evs_ts", None) or {}
    power_ts = getattr(artifacts, "cluster_day_ahead_power_ts", None) or {}

    if capability_ts is not None:
        for cluster_id, frame in capability_ts.items():
            connected = connected_ts.get(cluster_id, pd.Series(dtype=float))
            power = power_ts.get(cluster_id, pd.Series(dtype=float))
            for timestamp, row in frame.iterrows():
                cluster_forecasts.append(
                    {
                        "cluster_id": int(cluster_id),
                        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                        "downward_capability_kW": float(row.get("downward_capability_kW", 0.0)),
                        "upward_capability_kW": float(row.get("upward_capability_kW", 0.0)),
                        "connected_evs": int(connected.get(timestamp, 0)),
                        "cluster_power_kW": float(power.get(timestamp, 0.0)),
                    }
                )

    schedules: list[dict[str, object]] = []
    schedule_df = getattr(artifacts, "day_ahead_ev_summary", None)
    if schedule_df is not None and not schedule_df.empty:
        for record in schedule_df.to_dict(orient="records"):
            for key, value in record.items():
                if hasattr(value, "isoformat"):
                    record[key] = value.isoformat()
                elif hasattr(value, "item"):
                    record[key] = value.item()
            schedules.append(record)

    return cluster_forecasts, schedules


class CommandPoint(BaseModel):
    """Single timestamped command sample used in JSON Stage-2 requests."""

    timestamp: datetime
    p_set_kw: float | None = None
    p_min_kw: float | None = None
    p_max_kw: float | None = None


class FlexPotentialEstimationOptions(BaseModel):
    """Form options for Stage-1 endpoint."""

    solver_backend: str = DEFAULT_SOLVER_BACKEND
    capability_export_enabled: bool = True
    capability_export_format: str = "xlsx"
    generate_plots: bool = True
    run_kpi_analysis_enabled: bool = False
    output_dir: str | None = DEFAULT_OUTPUT_DIR

    @classmethod
    def as_form(
        cls,
        solver_backend: Annotated[str, Form()] = DEFAULT_SOLVER_BACKEND,
        capability_export_enabled: Annotated[bool, Form()] = True,
        capability_export_format: Annotated[str, Form()] = "xlsx",
        generate_plots: Annotated[bool, Form()] = True,
        run_kpi_analysis_enabled: Annotated[bool, Form()] = False,
        output_dir: Annotated[str | None, Form()] = DEFAULT_OUTPUT_DIR,
    ) -> "FlexPotentialEstimationOptions":
        """Build options model from multipart form fields."""
        return cls(
            solver_backend=solver_backend,
            capability_export_enabled=capability_export_enabled,
            capability_export_format=capability_export_format,
            generate_plots=generate_plots,
            run_kpi_analysis_enabled=run_kpi_analysis_enabled,
            output_dir=output_dir,
        )


class PlanningDataRequest(BaseModel):
    """JSON body schema for planning metadata ingestion."""

    planning_start: datetime
    planning_end: datetime
    time_step_minutes: int = Field(..., gt=0)


class FlexAwareSmartChargeSchedulingRequest(BaseModel):
    """JSON body schema for Stage-2 endpoint."""

    stage1_id: str = Field(..., description="Identifier returned by flex-potential-estimation")
    command_type: str = ABSOLUTE_SETPOINT
    command_strategy: str = "midpoint"
    tracking_mode: str = TRACKING_MODE_STRICT
    match_tolerance_kw: float = Field(default=1e-3, ge=0.0)
    commands_by_cluster: Dict[str, List[CommandPoint]] | None = None
    stage2_export_enabled: bool = True
    stage2_export_format: str = "xlsx"
    generate_stage2_soc_plots: bool = True


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe endpoint."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str | int]:
    """Readiness probe with operational metadata."""
    return {
        "status": "ok",
        "stage1_cache_size": stage1_cache.size(),
        "default_solver_backend": DEFAULT_SOLVER_BACKEND,
    }


def _fetch_cluster_and_fleet_from_db() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Fetch clusters and fleet from the database for forecast computation."""
    from utils.input_parser import parse_database_input

    return parse_database_input()


@app.post("/v1/compute_forecasts")
def compute_forecasts(payload: PlanningDataRequest) -> dict[str, object]:
    """Compute cluster forecasts from DB-backed input data."""
    global latest_planning_data

    planning_start = payload.planning_start
    planning_end = payload.planning_end
    time_step_minutes = int(payload.time_step_minutes)

    planning_payload = {
        "planning_start": planning_start.isoformat() if hasattr(planning_start, "isoformat") else str(planning_start),
        "planning_end": planning_end.isoformat() if hasattr(planning_end, "isoformat") else str(planning_end),
        "time_step_minutes": time_step_minutes,
    }
    latest_planning_data = planning_payload

    artifacts = run_flex_potential_estimation(
        input_file_path="nonexistent.xlsx",  # Placeholder path; actual file is not needed for DB-backed run
        planning_start=planning_start,
        planning_end=planning_end,
        time_step=timedelta(minutes=time_step_minutes),
        solver_backend=DEFAULT_SOLVER_BACKEND,
        output_dir=None,  # No output directory needed for DB-backed run
        capability_export_enabled=True,
        capability_export_format="xlsx",
        generate_plots=False,
        run_kpi_analysis_enabled=False,
        db_input_enabled=True,
        db_export_enabled=True,
    )

    cluster_forecasts, charging_schedules = _serialize_forecast_artifacts(artifacts)

    return {
        "status": "computed",
        **planning_payload,
        "db_export_enabled": True,
        "cluster_forecasts": cluster_forecasts,
        "charging_schedules": charging_schedules,
    }


def _persist_upload_to_temp_xlsx(upload: UploadFile) -> str:
    """Persist uploaded Excel file to a temporary local path.

    Parameters
    ----------
    upload : UploadFile
        File object provided by FastAPI multipart handling.

    Returns
    -------
    str
        Absolute path to temporary file.

    Side Effects
    ------------
    - Creates a temporary file in system temp directory.
    - Closes the uploaded file handle.

    Raises
    ------
    ValueError
        If extension is not one of `xlsx/xlsm/xls`.
    """
    suffix = os.path.splitext(upload.filename or "")[1].lower()
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError("Uploaded file must be an Excel workbook (.xlsx/.xlsm/.xls).")

    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        with open(tmp_path, "wb") as temp_file:
            shutil.copyfileobj(upload.file, temp_file)
    finally:
        upload.file.close()

    return tmp_path


def _resolve_output_root(output_dir: str | None) -> str:
    """Resolve API output root directory."""
    root = output_dir or DEFAULT_OUTPUT_DIR or os.path.join(os.getcwd(), DEFAULT_OUTPUT_ROOT_DIRNAME)
    return os.path.abspath(root)


def _build_job_output_root(output_root: str, stage1_id: str) -> str:
    """Build per-request output root path under `<output_root>/jobs/<stage1_id>`."""
    return os.path.join(output_root, JOB_OUTPUT_DIRNAME, stage1_id)


@app.post("/v1/flex-potential-estimation")
def flex_potential_estimation(
    input_file: UploadFile = File(...),
    options: FlexPotentialEstimationOptions = Depends(FlexPotentialEstimationOptions.as_form),
):
    """Run Stage-1 via HTTP request and cache artifacts for Stage-2.

    Parameters
    ----------
    input_file : UploadFile
        Primary workbook containing planning/fleet/cluster sheets.
    options : FlexPotentialEstimationOptions
        Solver/export/plot options supplied through multipart form fields.

    Returns
    -------
    dict
        Stage-1 metadata including generated ``stage1_id``.

    Side Effects
    ------------
    - Creates and deletes temporary upload file.
    - Executes Stage-1 solver workflow.
    - Stores artifacts in in-memory TTL cache.

    Raises
    ------
    HTTPException
        ``400`` for malformed input or workflow failures.
    """
    tmp_path = None
    try:
        stage1_id = uuid4().hex
        output_root = _resolve_output_root(options.output_dir)
        job_output_root = _build_job_output_root(output_root, stage1_id)
        tmp_path = _persist_upload_to_temp_xlsx(input_file)
        artifacts = run_flex_potential_estimation(
            input_file_path=tmp_path,
            solver_backend=options.solver_backend,
            output_dir=job_output_root,
            capability_export_enabled=options.capability_export_enabled,
            capability_export_format=options.capability_export_format,
            generate_plots=options.generate_plots,
            run_kpi_analysis_enabled=options.run_kpi_analysis_enabled,
        )
        stage1_cache.put_with_key(stage1_id, artifacts)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "stage1_id": stage1_id,
        "planning_start": artifacts.planning_start,
        "planning_end": artifacts.planning_end,
        "time_step_minutes": int(artifacts.time_step.total_seconds() / 60),
        "cluster_count": len(artifacts.cluster_capability_ts),
        "clusters": sorted(list(artifacts.cluster_capability_ts.keys())),
        "cluster_capability_summary": artifacts.cluster_capability_summary,
        "output_dir": artifacts.output_dir,
        "job_output_root": job_output_root,
        "cache_ttl_seconds": stage1_cache.ttl_seconds,
    }


def _commands_to_payload(
    commands_by_cluster: Dict[str, List[CommandPoint]] | None,
    command_type: str,
) -> dict[str, pd.Series | pd.DataFrame] | None:
    """Convert JSON command payload to pandas objects.

    Parameters
    ----------
    commands_by_cluster : Dict[str, List[CommandPoint]] | None
        Cluster-command map from JSON body.
    command_type : str
        `absolute_setpoint` or `flex_band`.

    Returns
    -------
    dict[str, pd.Series | pd.DataFrame] | None
        Per-cluster command payload indexed by datetime.
    """
    normalized_type = (command_type or "").strip().lower()
    if normalized_type not in {ABSOLUTE_SETPOINT, FLEX_BAND}:
        raise ValueError(
            "Unsupported command_type. Use 'absolute_setpoint' or 'flex_band'."
        )

    if commands_by_cluster is None:
        return None

    out: dict[str, pd.Series | pd.DataFrame] = {}
    for cc_id, points in commands_by_cluster.items():
        if not points:
            if normalized_type == ABSOLUTE_SETPOINT:
                out[cc_id] = pd.Series(dtype=float)
            else:
                out[cc_id] = pd.DataFrame(columns=["p_min_kw", "p_max_kw"], dtype=float)
            continue

        points_sorted = sorted(points, key=lambda p: p.timestamp)
        idx = [p.timestamp for p in points_sorted]
        if normalized_type == ABSOLUTE_SETPOINT:
            vals: list[float] = []
            for point in points_sorted:
                if point.p_set_kw is None:
                    raise ValueError(
                        "absolute_setpoint commands require 'p_set_kw' for each timestamp."
                    )
                vals.append(float(point.p_set_kw))
            out[cc_id] = pd.Series(vals, index=pd.to_datetime(idx), dtype=float)
        else:
            mins: list[float] = []
            maxs: list[float] = []
            for point in points_sorted:
                if point.p_min_kw is None or point.p_max_kw is None:
                    raise ValueError(
                        "flex_band commands require 'p_min_kw' and 'p_max_kw' for each timestamp."
                    )
                if point.p_set_kw is not None:
                    raise ValueError(
                        "flex_band commands must not include 'p_set_kw'."
                    )
                mins.append(float(point.p_min_kw))
                maxs.append(float(point.p_max_kw))

            payload_df = pd.DataFrame(
                {"p_min_kw": mins, "p_max_kw": maxs},
                index=pd.to_datetime(idx),
                dtype=float,
            )
            out[cc_id] = payload_df

    return out


def _parse_form_bool(raw_value: object | None, field_name: str, default: bool) -> bool:
    """Parse tolerant boolean values from form fields.

    Accepts canonical values like `true/false`, `1/0`, `yes/no`.
    """
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)) and raw_value in (0, 1):
        return bool(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(
        f"Invalid boolean value for '{field_name}'. "
        "Use one of: true/false, 1/0, yes/no."
    )


def _parse_form_float(raw_value: object | None, field_name: str, default: float) -> float:
    """Parse float values from form fields with default fallback."""
    if raw_value is None:
        return default
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        if normalized == "":
            return default
        return float(normalized)
    raise ValueError(f"Invalid numeric value for '{field_name}'.")


@app.post("/v1/flex-aware-smart-charge-scheduling")
async def flex_aware_smart_charge_scheduling(request: Request):
    """Run Stage-2 scheduling with JSON or multipart input.

    Purpose
    -------
    Accepts either:
    - JSON body with inline commands, or
    - Multipart form with optional external setpoint Excel file.

    Parameters
    ----------
    request : Request
        Raw FastAPI request used for content-type based dispatch.

    Returns
    -------
    dict
        Stage-2 command status, accepted/rejected cluster lists and planning
        metadata.

    Side Effects
    ------------
    - Reads and possibly writes temp file for uploaded setpoint workbook.
    - Executes Stage-2 solver workflow.
    - Deletes cached Stage-1 artifact on successful scheduling.

    Raises
    ------
    HTTPException
        - ``400`` for validation/parsing/workflow failures.
        - ``404`` if ``stage1_id`` is unknown or expired.
        - ``415`` for unsupported content type.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    stage1_id: str
    command_type: str
    command_strategy: str
    tracking_mode: str
    match_tolerance_kw: float
    stage2_export_enabled: bool
    stage2_export_format: str
    generate_stage2_soc_plots: bool
    commands_by_cluster: dict[str, pd.Series | pd.DataFrame] | None

    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
            req = FlexAwareSmartChargeSchedulingRequest.model_validate(payload)
            stage1_id = req.stage1_id
            command_type = req.command_type
            command_strategy = req.command_strategy
            tracking_mode = req.tracking_mode
            match_tolerance_kw = float(req.match_tolerance_kw)
            stage2_export_enabled = req.stage2_export_enabled
            stage2_export_format = req.stage2_export_format
            generate_stage2_soc_plots = req.generate_stage2_soc_plots
            commands_by_cluster = _commands_to_payload(
                req.commands_by_cluster,
                command_type=command_type,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif (
        content_type.startswith("multipart/form-data")
        or content_type.startswith("application/x-www-form-urlencoded")
    ):
        form = await request.form()
        stage1_id_raw = form.get("stage1_id")
        if stage1_id_raw is None or str(stage1_id_raw).strip() == "":
            raise HTTPException(status_code=400, detail="'stage1_id' is required.")

        stage1_id = str(stage1_id_raw).strip()
        command_type = str(form.get("command_type", ABSOLUTE_SETPOINT))
        command_strategy = str(form.get("command_strategy", "midpoint"))
        tracking_mode = str(form.get("tracking_mode", TRACKING_MODE_STRICT))
        match_tolerance_kw = _parse_form_float(
            form.get("match_tolerance_kw"),
            field_name="match_tolerance_kw",
            default=1e-3,
        )
        stage2_export_enabled = _parse_form_bool(
            form.get("stage2_export_enabled"),
            field_name="stage2_export_enabled",
            default=True,
        )
        stage2_export_format = str(form.get("stage2_export_format", "xlsx"))
        generate_stage2_soc_plots = _parse_form_bool(
            form.get("generate_stage2_soc_plots"),
            field_name="generate_stage2_soc_plots",
            default=True,
        )

        upload = form.get("setpoints_file")
        if upload is None:
            commands_by_cluster = None
        else:
            if not hasattr(upload, "filename") or not hasattr(upload, "file"):
                raise HTTPException(
                    status_code=400,
                    detail="'setpoints_file' must be an uploaded Excel file.",
                )
            if (upload.filename or "").strip() == "":
                commands_by_cluster = None
            else:
                tmp_path = None
                try:
                    tmp_path = _persist_upload_to_temp_xlsx(upload)
                    commands_by_cluster = parse_stage2_setpoints_sheet(
                        tmp_path,
                        command_type=command_type,
                    )
                except Exception as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
    else:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported Content-Type. Use application/json for inline commands "
                "or multipart/form-data with optional 'setpoints_file'."
            ),
        )

    artifacts = stage1_cache.get(stage1_id)
    if artifacts is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Unknown or expired stage1_id. Run /v1/flex-potential-estimation again "
                "before scheduling."
            ),
        )

    try:
        stage2_artifacts = run_flex_aware_smart_charge_scheduling(
            artifacts=artifacts,
            command_type=command_type,
            command_strategy=command_strategy,
            tracking_mode=tracking_mode,
            match_tolerance_kw=match_tolerance_kw,
            commands_by_cluster=commands_by_cluster,
            export_enabled=stage2_export_enabled,
            export_format=stage2_export_format,
            generate_soc_plots=generate_stage2_soc_plots,
        )
        # Clear cached artifacts after successful scheduling to keep service stateless across jobs.
        stage1_cache.delete(stage1_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    command_status_records = stage2_artifacts.command_status.to_dict(orient="records")
    accepted_clusters = sorted(
        stage2_artifacts.command_status.loc[
            stage2_artifacts.command_status["status"] == "accepted", "cluster_id"
        ].astype(str).tolist()
    )
    rejected_clusters = sorted(
        stage2_artifacts.command_status.loc[
            stage2_artifacts.command_status["status"] == "rejected", "cluster_id"
        ].astype(str).tolist()
    )

    return {
        "planning_start": artifacts.planning_start,
        "planning_end": artifacts.planning_end,
        "time_step_minutes": int(artifacts.time_step.total_seconds() / 60),
        "accepted_clusters": accepted_clusters,
        "rejected_clusters": rejected_clusters,
        "command_status": command_status_records,
        "tracking_mode": tracking_mode,
        "match_tolerance_kw": float(match_tolerance_kw),
        "cluster_tracking_summary": stage2_artifacts.cluster_tracking_summary,
        "output_dir": stage2_artifacts.output_dir,
    }
