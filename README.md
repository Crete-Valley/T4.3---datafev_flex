# datafev_flex

FastAPI-based EV flexibility service for two-stage optimization, command tracking, and job-scoped exports.

This repository provides a reproducible service workflow for EV charging clusters: Stage-1 computes day-ahead G2V/V2G flexibility envelopes, and Stage-2 performs flex-aware smart charging against absolute setpoints or flex-band commands. It includes both HTTP APIs and local workflow execution, with per-job artifact exports, tracking KPIs, and plotting utilities built on adapted components from [datafev](https://github.com/sogno-platform/datafev).

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Installation](#installation)
5. [Usage](#usage)
6. [Configuration](#configuration)
7. [Testing](#testing)
8. [Developer Guide](#developer-guide)
9. [Troubleshooting](#troubleshooting)
10. [License](#license)
11. [Contact](#contact)

## Overview

The service answers a core question for EV aggregators and charging networks: *How much upward (V2G/export) or downward (G2V/consumption) flexibility can each cluster deliver in a specified planning window, and can clusters track requested aggregate setpoints while respecting EV constraints?* Given charger definitions and EV schedules stored in an Excel workbook, the pipeline:

1. Loads cluster topology and EV fleet data.
2. Builds legacy-compatible `ChargerCluster` and `EVFleet` objects.
3. Solves Stage-1 MILPs for maximum consumption and minimum consumption (export) profiles per cluster using Pyomo + Gurobi.
4. Builds and validates Stage-2 commands (absolute setpoint or flex band) with schema and envelope checks; invalid commands are rejected.
5. Solves Stage-2 cluster-aggregate flex-aware scheduling MILP with command tracking (`p_cc == p_set` for absolute, `p_min <= p_cc <= p_max` for flex band) and optional target SoC enforcement (`use_target_soc`).
6. Exports capability and scheduling results, renders plots, and optionally runs KPI post-processing.

Outputs are written under `outputs/`:
- CLI (`python run_local_workflow.py`) writes stage-level paths:
  - `outputs/flex_potential_estimation/`
  - `outputs/flex_aware_smart_charge_scheduling/`
- API (`POST /v1/flex-potential-estimation` + Stage-2 call) writes per-request job paths:
  - `outputs/jobs/<stage1_id>/flex_potential_estimation/`
  - `outputs/jobs/<stage1_id>/flex_aware_smart_charge_scheduling/`

## Architecture

Architecture flowchart: [`docs/architecture/flowchart.html`](docs/architecture/flowchart.html)

```
.
├── run_local_workflow.py                     # Orchestrates planning workflow
├── algorithms/
│   ├── capability/             # Stage-1 MILPs (G2V / V2G envelopes)
│   └── scheduling/             # Stage-2 MILP (flex-aware setpoint tracking)
├── data_handling/              # Legacy datafev adapters (clusters, fleet, EVs)
├── utils/
│   ├── input_parser.py         # Reads Excel into dataframes
│   ├── flex_command_utils.py   # Stage-2 setpoint command generation/validation
│   ├── output_utils.py         # Export helpers for Stage-1 / Stage-2 outputs
│   └── plotting_service.py     # Matplotlib plots for capabilities
├── analysis/kpi_analysis.py    # KPI computation and plots from exported XLSX
├── api/app.py                  # FastAPI entrypoint (HTTP service mode)
├── services/                   # Orchestration layer used by CLI and API
├── inputs/stage1_sample_input.xlsx     # Sample Stage-1 input data
├── inputs/stage2_sample_absolute_setpoints.xlsx # Sample Stage-2 absolute setpoints
├── inputs/stage2_sample_flex_band_commands.xlsx # Sample Stage-2 flex-band commands
├── outputs/                    # Generated results (ignored by git)
│   ├── flex_potential_estimation/
│   ├── flex_aware_smart_charge_scheduling/
│   └── jobs/<stage1_id>/
│       ├── flex_potential_estimation/
│       └── flex_aware_smart_charge_scheduling/
└── tests/                      # Pytest suite (unit + integration)
```

Key flow:

1. `run_local_workflow.py` calls service-layer workflows, which parse planning metadata from Excel.
2. `data_handling.charger.ChargerCluster`, `data_handling.fleet.EVFleet`, and `data_handling.multi_cluster.MultiClusterSystem` adapt to the legacy datafev model.
3. `algorithms.capability.g2v_capability` and `algorithms.capability.v2g_capability` compute downward/upward capability envelopes (Stage 1).
4. `utils.flex_command_utils` prepares setpoint commands and validates them against Stage-1 envelopes/timestamps.
5. `algorithms.scheduling.flex_aware_scheduling` solves Stage-2 cluster-aggregate schedules in `strict` (hard tracking) or `best_effort` (mismatch-minimizing) mode.
6. `utils.output_utils` exports Stage-1 and Stage-2 artifacts; `utils.plotting_service` creates Stage-1 capability plots.
7. `api/app.py` exposes the same workflow through HTTP endpoints.

## Features

- **Excel-driven inputs**: configure EVs and chargers via `inputs/stage1_sample_input.xlsx`.
- **Cluster capability computation**: solves MILPs for max consumption and min consumption per planning step.
- **Stage-2 command scheduling**: solves cluster-aggregate MILP schedules for accepted absolute setpoints or flex bands.
- **Optional best-effort Stage-2 tracking**: when strict tracking is infeasible or oversized, solve a feasible schedule that minimizes command mismatch and reports per-timestep compliance.
- **Dual command type support**:
  - `absolute_setpoint`: `p_set_kw` per timestamp
  - `flex_band`: `p_min_kw` / `p_max_kw` per timestamp
- **Command validation and rejection policy**: strict mode rejects commands on schema/timestep mismatch, unknown clusters, envelope violations, and MILP infeasibility.
- **Optional target SoC in Stage-2**: `use_target_soc=1` enforces target SoC at departure; `0` enforces minimum SoC only.
- **Legacy object reuse**: bridges to existing `EVFleet` and `ChargerCluster` without modifying their internals.
- **Time-series export**: configurable CSV/Parquet/XLSX exports including Stage-1 capability, Stage-2 scheduling, and Stage-2 tracking compliance reports.
- **Plotting**: per-cluster and aggregate capability plots with optional connected-EV overlay.
- **KPI post-processing**: `analysis/kpi_analysis.py` to compute energy potentials, ramp rates, availability, etc.
- **Testing**: unit/integration tests via pytest, covering input parsing, capability solvers, exports, and plotting.
- **Extensible**: add more algorithms, inputs, or visualization steps as needed.

## Installation

Clone the repository and install dependencies. The project expects Python 3.12+.

### Using virtualenv (recommended)

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Development dependencies

```bash
pip install -r requirements-test.txt
```

### Conda (alternative)

```bash
conda create -n datafev_flex python=3.12
conda activate datafev_flex
pip install -r requirements.txt
```

### Docker

The repository now includes:

- `Dockerfile` (Python 3.12 slim + Pyomo + GLPK runtime)
- `docker-compose.yml` (API-first deployment, port `8000`)

Quick start:

```bash
export USER_ID="$(id -u)"
export GROUP_ID="$(id -g)"
docker compose build
docker compose up -d
docker compose logs -f datafev-flex
```

Quick API smoke flow after `up`:

```bash
# Compatibility note for environments with custom libcurl/LD_LIBRARY_PATH.
CURL_CMD='env -u LD_LIBRARY_PATH /usr/bin/curl'
BASE_URL='http://127.0.0.1:8000'

# 1) Health checks
$CURL_CMD -sS "$BASE_URL/healthz"
$CURL_CMD -sS "$BASE_URL/readyz"

# 2) Stage-1 request (capture stage1_id)
STAGE1_ID="$($CURL_CMD -sS -X POST "$BASE_URL/v1/flex-potential-estimation" \
  -F "input_file=@inputs/stage1_sample_input.xlsx" \
  -F "solver_backend=glpk" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["stage1_id"])')"
echo "STAGE1_ID=$STAGE1_ID"

# 3) Stage-2 request (absolute setpoint example)
$CURL_CMD -sS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=$STAGE1_ID" \
  -F "command_type=absolute_setpoint" \
  -F "setpoints_file=@inputs/stage2_sample_absolute_setpoints.xlsx"

# 4) Verify generated artifacts for this request id
ls -lah "outputs/jobs/$STAGE1_ID/flex_potential_estimation"
ls -lah "outputs/jobs/$STAGE1_ID/flex_aware_smart_charge_scheduling"
```

For additional manual test scenarios, see
[`docs/acceptance_test_checklist.md`](docs/acceptance_test_checklist.md).

The compose setup mounts:

- `./outputs` -> `/app/outputs` (persistent exports)
- `./inputs` -> `/app/inputs` (read-only sample/input files)

To keep generated files deletable by your host user, run compose with your user/group id:

```bash
USER_ID="$(id -u)" GROUP_ID="$(id -g)" docker compose up -d
```

`docker-compose.yml` uses `user: "${USER_ID}:${GROUP_ID}"`, so files written under
`outputs/` are owned by your current host user.

Stop the stack:

```bash
export USER_ID="$(id -u)"
export GROUP_ID="$(id -g)"
docker compose down
```

## Usage

### 1. Prepare inputs

Edit `inputs/stage1_sample_input.xlsx` (or create your own) to define:

- `Planning` sheet:
  - `planning_start`
  - `planning_end`
  - `time_step_minutes`
- `Cluster` sheets: charger IDs, max charge/discharge power, efficiency.
- `Fleet` sheet: EV arrival/departure times, SOC targets, cluster assignments, charging limits.
  - Optional Stage-2 strictness flag: `exact_target_soc` (`0/1`)

### 2. Run the planning workflow

```bash
source venv/bin/activate
python run_local_workflow.py
```

Console output previews Stage-1 capability and Stage-2 scheduling summaries. Results are split by stage:

- `outputs/flex_potential_estimation/cluster_capability_timeseries.xlsx` (one sheet per cluster, includes connected EV count).
- `outputs/flex_potential_estimation/cluster_<id>_capability.png` (per-cluster capability band + optional EV overlay).
- `outputs/flex_potential_estimation/aggregate_capability.png` (sum across clusters).
- `outputs/flex_aware_smart_charge_scheduling/stage2_flex_scheduling_results.xlsx` (command status, per-cluster command band, cluster power, EV power, EV SoC, and tracking report sheets).
- `outputs/flex_aware_smart_charge_scheduling/stage2_cluster_<id>_ev_soc.png` (EV SoC schedules for accepted clusters).

### 3. Post-process KPIs (optional)

After running `run_local_workflow.py`, execute:

```bash
source venv/bin/activate
python analysis/kpi_analysis.py
```

This reads the exported XLSX, computes cluster-level KPIs, writes `kpi_summary.csv`, `aggregate_timeseries.csv`, and additional plots (`plot_aggregate_capability.png`, etc.).

### 4. Run as HTTP service (FastAPI)

```bash
source venv/bin/activate
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

or with Docker:

```bash
export USER_ID="$(id -u)"
export GROUP_ID="$(id -g)"
docker compose up -d
```

Main endpoints:

- `POST /v1/flex-potential-estimation` (multipart Excel upload, returns `stage1_id`)
- `POST /v1/flex-aware-smart-charge-scheduling`:
  - JSON body (`stage1_id` + optional inline `commands_by_cluster`), or
  - multipart form (`stage1_id` + optional `setpoints_file` Excel upload)
- `GET /healthz`
- `GET /readyz`

Typical API flow:

1. Upload workbook to `POST /v1/flex-potential-estimation`.
2. Receive `stage1_id` and envelope summary.
   Response also includes `job_output_root` and Stage-1 `output_dir` for this request.
3. Call `POST /v1/flex-aware-smart-charge-scheduling` with the same `stage1_id`
   and either inline JSON commands or an external setpoint Excel file.
   Optional controls:
   - `tracking_mode`: `strict` (default) or `best_effort`
   - `match_tolerance_kw`: tolerance for per-timestep `is_met` reporting
4. The service clears cached Stage-1 artifacts after successful scheduling.
5. Files for this request remain available under:
   `outputs/jobs/<stage1_id>/flex_potential_estimation/` and
   `outputs/jobs/<stage1_id>/flex_aware_smart_charge_scheduling/`.

Example `curl` commands:

1. Stage-1: upload planning input workbook and get `stage1_id`

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/flex-potential-estimation" \
  -F "input_file=@inputs/stage1_sample_input.xlsx" \
  -F "solver_backend=glpk"
```

2. Stage-2 (recommended): upload external setpoints workbook

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=<PASTE_STAGE1_ID_HERE>" \
  -F "command_type=absolute_setpoint" \
  -F "command_strategy=midpoint" \
  -F "setpoints_file=@inputs/stage2_sample_absolute_setpoints.xlsx"
```

3. Stage-2 (alternative): send inline JSON commands

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/flex-aware-smart-charge-scheduling" \
  -H "Content-Type: application/json" \
  -d '{
    "stage1_id": "<PASTE_STAGE1_ID_HERE>",
    "command_type": "absolute_setpoint",
    "command_strategy": "midpoint",
    "commands_by_cluster": {
      "1": [
        {"timestamp": "2022-01-08T07:00:00", "p_set_kw": 0.0},
        {"timestamp": "2022-01-08T07:15:00", "p_set_kw": 0.0}
      ]
    }
  }'
```

4. Stage-2 (alternative): send inline JSON flex-band command

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/flex-aware-smart-charge-scheduling" \
  -H "Content-Type: application/json" \
  -d '{
    "stage1_id": "<PASTE_STAGE1_ID_HERE>",
    "command_type": "flex_band",
    "commands_by_cluster": {
      "1": [
        {"timestamp": "2022-01-08T07:00:00", "p_min_kw": -20.0, "p_max_kw": -5.0},
        {"timestamp": "2022-01-08T07:15:00", "p_min_kw": -20.0, "p_max_kw": -5.0}
      ]
    }
  }'
```

5. Stage-2 (best-effort absolute tracking for oversized commands)

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=<PASTE_STAGE1_ID_HERE>" \
  -F "command_type=absolute_setpoint" \
  -F "tracking_mode=best_effort" \
  -F "match_tolerance_kw=0.25" \
  -F "setpoints_file=@inputs/stage2_sample_absolute_setpoints.xlsx"
```

External Stage-2 command Excel format (`setpoints_file`):

- Sheet name: `Setpoints`
- Always required columns:
  - `cluster_id`
  - `timestamp`
- If `command_type=absolute_setpoint`, required:
  - `p_set_kw`
- If `command_type=flex_band`, required:
  - `p_min_kw`
  - `p_max_kw`
- For `command_type=flex_band`, `p_set_kw` must not be provided.
- One row per `(cluster_id, timestamp)`; duplicate rows are rejected.
- Sample files:
  - `inputs/stage2_sample_absolute_setpoints.xlsx` (absolute setpoint)
  - `inputs/stage2_sample_flex_band_commands.xlsx` (flex band schema example)

## Configuration

Core runtime configuration lives in `run_local_workflow.py`. Key knobs:

- `file_path`: default is `inputs/stage1_sample_input.xlsx`; change to other workbooks as needed.
- `stage2_test_profile`: selects one Stage-2 mode:
  - `absolute_from_file`
  - `flex_band_from_file`
  - `absolute_midpoint`
  - `flex_band_midpoint`
- `stage2_profiles`: maps profile to `command_type` and optional `setpoints_file`.
- `stage2_refresh_flex_band_file_from_stage1`: when `True`, file-based flex-band commands are regenerated from current Stage-1 envelopes before Stage-2.
- `capability_export_enabled` / `capability_export_format`: Stage-1 export controls (`csv`, `xlsx`, `parquet`).
- `stage2_enabled`: enable/disable Stage-2 scheduling.
- `stage2_command_strategy`: `midpoint` is used when the selected profile does not provide a command file.
- `stage2_tracking_mode`: Stage-2 tracking policy (`strict` or `best_effort`).
- `stage2_match_tolerance_kw`: tolerance used to mark per-timestep tracking `is_met`.
- `stage2_export_enabled` / `stage2_export_format`: Stage-2 export controls (`csv`, `xlsx`).

Planning configuration:

- Planning horizon is read from the Excel `Planning` sheet (`planning_start`, `planning_end`, `time_step_minutes`).

Solver configuration:

- Default runtime backend is `gurobi_direct` in local Python runs.
- `docker-compose.yml` sets API default backend to `glpk` via environment variables.
- `solver = SolverFactory("gurobi_direct")` – update or replace with other Pyomo-supported solvers if Gurobi is unavailable (e.g., `cbc`, `glpk`) but note capability formulations assume a MILP solver that handles continuous variables and constraints efficiently.
- API requests expose `solver_backend`; examples: `glpk`, `gurobi_direct`.

Stage-2 policy:

- Cluster-aggregate optimization per cluster/timestep.
- Command type supports both:
  - absolute setpoint (`P_cluster(t) == P_set(t)`)
  - flex band (`P_min(t) <= P_cluster(t) <= P_max(t)`)
- Tracking mode is selectable:
  - `strict`: hard command tracking with rejection on envelope/model infeasibility.
  - `best_effort`: keeps EV/SOC constraints hard and minimizes command mismatch.
- Departure target policy per EV:
  - `use_target_soc=0` -> enforce `soc_dep >= min_soc`
  - `use_target_soc=1` and `exact_target_soc=0` -> enforce `soc_dep >= target_soc`
  - `use_target_soc=1` and `exact_target_soc=1` -> enforce `soc_dep == target_soc`
- Stage-2 outputs include per-cluster tracking report time-series (`requested`, `delivered`, `is_met`).
- In `strict`, invalid/infeasible commands are rejected and no Stage-2 schedule is applied for that cluster.

Plotting:

- `utils.plotting_service.plot_cluster_capability_bands` and `plot_aggregate_capability` run automatically at the end of `run_local_workflow.py`. Adjust plot style or disable by commenting out calls if not needed.

Environment variables:

- `DEFAULT_SOLVER_BACKEND`: API default solver backend (defaults to `gurobi_direct`; compose uses `glpk`).
- `DEFAULT_OUTPUT_DIR`: API default output root path. Request files are placed under
  `jobs/<stage1_id>/flex_potential_estimation/` and
  `jobs/<stage1_id>/flex_aware_smart_charge_scheduling/`.
- `STAGE1_CACHE_TTL_SECONDS`: Stage-1 artifact cache TTL.
- `STAGE1_CACHE_MAX_ITEMS`: maximum number of cached Stage-1 artifacts.
- For Gurobi usage, set license-related variables as needed (`GUROBI_HOME`, `GRB_LICENSE_FILE`).

## Testing

The project uses pytest. Activate the virtual environment and run:

```bash
source venv/bin/activate
pytest
```

Tests cover:

- Input parsing from Excel (`tests/unit/test_input_parser.py`)
- Capability solver logic (`tests/unit/test_g2v_capability.py`, `tests/unit/test_cluster_capability.py`)
- Stage-2 command validation (`tests/unit/test_flex_command_utils.py`)
- Stage-2 scheduling solver behavior (`tests/unit/test_flex_aware_scheduling.py`)
- Export utilities (`tests/unit/test_output_utils.py`)
- Plot generation (`tests/unit/test_plotting_service.py`)
- End-to-end workflow smoke test (`tests/integration/test_flex_workflow.py`)

Use `pytest -vv` for verbose output. Coverage can be collected via `pytest --cov`.

Acceptance testing:

- Manual acceptance scenarios are documented in [`docs/acceptance_test_checklist.md`](docs/acceptance_test_checklist.md).
- One-command acceptance matrix runner: `scripts/run_acceptance_matrix.sh`.
- Deterministic acceptance fixtures can be generated with `venv/bin/python scripts/generate_acceptance_inputs.py`.
- Remove generated acceptance fixtures with `venv/bin/python scripts/generate_acceptance_inputs.py --clean`.
- Container smoke script: `scripts/smoke_api.sh`.

CI:

- GitLab CI pipeline is defined in `.gitlab-ci.yml` with:
  - `pytest` job (unit/integration tests)
  - `docker_smoke` job (build + run + API smoke flow)

## Developer Guide

### Folder structure

```
algorithms/                 Stage-1/Stage-2 MILP formulations
  capability/               G2V/V2G envelope models (Stage 1)
  scheduling/               Flex-aware setpoint scheduler (Stage 2)
api/                        FastAPI service entrypoint
analysis/                   KPI scripts
data_handling/              datafev adapters for clusters/fleet
inputs/                     Excel inputs (sample provided)
outputs/                    Generated artifacts (ignored by git)
  flex_potential_estimation/        Stage-1 exports and plots
  flex_aware_smart_charge_scheduling/ Stage-2 exports and SoC plots
  jobs/<stage1_id>/                 API request-specific artifact root
    flex_potential_estimation/      Stage-1 artifacts for this request
    flex_aware_smart_charge_scheduling/ Stage-2 artifacts for this request
services/                   Shared orchestration for CLI/API workflows
tests/                      Pytest suite
utils/                      Helpers (input parsing, command validation, exports, plotting)
```

### Conventions

- Follow PEP 8 for Python style.
- Use type hints for new code.
- Keep plotting functions deterministic and headless (`matplotlib` uses `Agg` backend).
- Prefer pandas/numpy for data manipulation; avoid editing legacy `data_handling` objects unless necessary.
- New command-line utilities should go under `analysis/` or `utils/` with clear docstrings.

### Adding new features

1. Extend input schema (`utils/input_parser.py`) if new columns are needed.
2. Update `run_local_workflow.py` to pass additional parameters into `EVFleet` or capability solvers.
3. Add tests under `tests/unit/` or `tests/integration/`.
4. Document the change in this README.

## Troubleshooting

| Issue | Cause / Fix |
|-------|-------------|
| `ModuleNotFoundError: pandas` | Ensure the virtual environment is activated and dependencies installed (`pip install -r requirements.txt`). |
| `No license for gurobi` | Configure Gurobi license file/environment (`GRB_LICENSE_FILE`). Alternatively, switch to another Pyomo solver but expect longer runtimes. |
| `inputs/stage1_sample_input.xlsx not found` | Verify working directory is repo root or update `file_path` in `run_local_workflow.py`. |
| Cannot delete files under `outputs/jobs/` after Docker runs | Old artifacts may be owned by `root` from earlier container runs. One-time fix: `sudo chown -R $(id -u):$(id -g) outputs/jobs` |
| Empty plots or NaNs | Confirm EVs are assigned to clusters and planning window overlaps with EV arrivals/departures. |
| Stage-2 command rejected (`OUT_OF_ENVELOPE`) | The setpoint exceeds Stage-1 envelope bounds for at least one timestep. |
| Stage-2 command rejected (`TIMESTEP_MISMATCH`) | Command timestamps do not exactly match the planning horizon index. |
| Stage-2 command rejected (`INFEASIBLE_MILP`) | Setpoint is envelope-feasible but conflicts with EV-level SoC/availability constraints in Stage-2 MILP. |

## License

- Base components derived from the [datafev framework](https://github.com/sogno-platform/datafev) (MIT License).

## Contact

- Aytug Yavuzer, M.Sc. aytug.yavuzer@eonerc.rwth-aachen.de
- Univ.-Prof. Antonello Monti, Ph.D. post_acs@eonerc.rwth-aachen.de

Institute for Automation of Complex Power Systems (ACS): http://www.acs.eonerc.rwth-aachen.de
E.ON Energy Research Center (E.ON ERC): http://www.eonerc.rwth-aachen.de
RWTH Aachen University, Germany: http://www.rwth-aachen.de
