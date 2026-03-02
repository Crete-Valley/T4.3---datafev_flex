# Acceptance Test Checklist

This checklist validates API behavior end-to-end with a deterministic Stage-1 and Stage-2 input matrix.

## Preconditions

- Service is running on `http://127.0.0.1:8000`.
- Acceptance fixtures are generated under `inputs/acceptance_cases/`.

Generate fixtures:

```bash
venv/bin/python scripts/generate_acceptance_inputs.py
```

Rollback (remove generated fixtures):

```bash
venv/bin/python scripts/generate_acceptance_inputs.py --clean
```

One-command full matrix run:

```bash
scripts/run_acceptance_matrix.sh
```

Optional shell setup (recommended):

```bash
BASE_URL="http://127.0.0.1:8000"
CURL_CMD='env -u LD_LIBRARY_PATH /usr/bin/curl'
CASE_DIR="inputs/acceptance_cases"

stage1_id_from() {
  local input_file="$1"
  $CURL_CMD -sS -X POST "$BASE_URL/v1/flex-potential-estimation" \
    -F "input_file=@${input_file}" \
    -F "solver_backend=glpk" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["stage1_id"])'
}

run_stage2_from_file() {
  local sid="$1"
  local command_type="$2"
  local command_file="$3"
  $CURL_CMD -sS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
    -F "stage1_id=${sid}" \
    -F "command_type=${command_type}" \
    -F "setpoints_file=@${command_file}"
}
```

Runner environment overrides:

- `BASE_URL` (default: `http://127.0.0.1:8000`)
- `CASE_DIR` (default: `inputs/acceptance_cases`)
- `SOLVER_BACKEND` (default: `glpk`)
- `OUTPUT_ROOT` (default: `outputs`)
- `CURL_BIN` (default: `/usr/bin/curl` if available, else `curl`)
- `PYTHON_BIN` (default: `python3`)
- `UNSET_LD_LIBRARY_PATH=1` to run curl with `env -u LD_LIBRARY_PATH`

Important:
- A successful Stage-2 call invalidates the corresponding `stage1_id` (cache entry is deleted).
- Generate a fresh `stage1_id` for each Stage-2 scenario.

## Scenario Matrix

| Group | File | Purpose | Expected |
|---|---|---|---|
| Stage-1 | `inputs/acceptance_cases/stage1_baseline_multi_cluster.xlsx` | Baseline, multi-cluster | HTTP `200`, `cluster_count > 0` |
| Stage-1 | `inputs/acceptance_cases/stage1_single_cluster_short_horizon.xlsx` | Single-cluster short horizon | HTTP `200`, `cluster_count == 1` |
| Stage-1 | `inputs/acceptance_cases/stage1_no_v2g_cluster2.xlsx` | Cluster-2 V2G disabled | HTTP `200`, capability summary present |
| Stage-2 absolute | `inputs/acceptance_cases/stage2_absolute_accept_baseline.xlsx` | Nominal file path | `command_status` populated |
| Stage-2 absolute | `inputs/acceptance_cases/stage2_absolute_out_of_envelope.xlsx` | Envelope violation | reason includes `OUT_OF_ENVELOPE` |
| Stage-2 absolute | `inputs/acceptance_cases/stage2_absolute_timestep_mismatch.xlsx` | Missing timesteps | reason includes `TIMESTEP_MISMATCH` |
| Stage-2 absolute | `inputs/acceptance_cases/stage2_absolute_unknown_cluster.xlsx` | Unknown cluster id | reason includes `UNKNOWN_CLUSTER` |
| Stage-2 absolute (`best_effort`) | `inputs/acceptance_cases/stage2_absolute_out_of_envelope.xlsx` | Soft-tracking fallback | accepted with `BEST_EFFORT_DEVIATION` and tracking summary |
| Stage-2 flex-band | `inputs/acceptance_cases/stage2_flex_band_accept_baseline.xlsx` | Nominal flex-band path | `command_status` populated |
| Stage-2 flex-band | `inputs/acceptance_cases/stage2_flex_band_out_of_envelope.xlsx` | Envelope violation | reason includes `OUT_OF_ENVELOPE` |
| Stage-2 flex-band | `inputs/acceptance_cases/stage2_flex_band_invalid_range.xlsx` | `p_min_kw > p_max_kw` | reason includes `INVALID_BAND_RANGE` |

## 1) Health Checks

```bash
$CURL_CMD -sS "$BASE_URL/healthz"
$CURL_CMD -sS "$BASE_URL/readyz"
```

Expected:

- `healthz`: `{"status":"ok"}`
- `readyz`: includes `status=ok`, cache size, and default solver backend.

## 2) Stage-1 Matrix Run

Run each Stage-1 case:

```bash
for stage1_file in \
  "$CASE_DIR/stage1_baseline_multi_cluster.xlsx" \
  "$CASE_DIR/stage1_single_cluster_short_horizon.xlsx" \
  "$CASE_DIR/stage1_no_v2g_cluster2.xlsx"
do
  echo "== stage1: $stage1_file"
  $CURL_CMD -sS -X POST "$BASE_URL/v1/flex-potential-estimation" \
    -F "input_file=@${stage1_file}" \
    -F "solver_backend=glpk"
done
```

Expected:

- All requests return HTTP `200`.
- Response contains `stage1_id`, `cluster_capability_summary`, `job_output_root`, `output_dir`.

## 3) Stage-2 Absolute Matrix

Use baseline Stage-1 fixture and run absolute command files:

```bash
for stage2_file in \
  "$CASE_DIR/stage2_absolute_accept_baseline.xlsx" \
  "$CASE_DIR/stage2_absolute_out_of_envelope.xlsx" \
  "$CASE_DIR/stage2_absolute_timestep_mismatch.xlsx" \
  "$CASE_DIR/stage2_absolute_unknown_cluster.xlsx"
do
  SID="$(stage1_id_from "$CASE_DIR/stage1_baseline_multi_cluster.xlsx")"
  echo "== stage2 absolute: $stage2_file with SID=$SID"
  run_stage2_from_file "$SID" "absolute_setpoint" "$stage2_file"
done
```

Expected:

- Baseline case: `command_status` populated, with explicit accepted/rejected rows.
- Out-of-envelope case: reason includes `OUT_OF_ENVELOPE`.
- Timestep mismatch case: reason includes `TIMESTEP_MISMATCH`.
- Unknown cluster case: reason includes `UNKNOWN_CLUSTER`.

## 4) Stage-2 Flex-Band Matrix

Use baseline Stage-1 fixture and run flex-band command files:

```bash
for stage2_file in \
  "$CASE_DIR/stage2_flex_band_accept_baseline.xlsx" \
  "$CASE_DIR/stage2_flex_band_out_of_envelope.xlsx" \
  "$CASE_DIR/stage2_flex_band_invalid_range.xlsx"
do
  SID="$(stage1_id_from "$CASE_DIR/stage1_baseline_multi_cluster.xlsx")"
  echo "== stage2 flex_band: $stage2_file with SID=$SID"
  run_stage2_from_file "$SID" "flex_band" "$stage2_file"
done
```

Expected:

- Baseline case: `command_status` populated.
- Out-of-envelope case: reason includes `OUT_OF_ENVELOPE`.
- Invalid-range case: reason includes `INVALID_BAND_RANGE`.

## 4b) Stage-2 Best-Effort Absolute Path

Use out-of-envelope absolute setpoints with `tracking_mode=best_effort`:

```bash
SID="$(stage1_id_from "$CASE_DIR/stage1_baseline_multi_cluster.xlsx")"
$CURL_CMD -sS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=$SID" \
  -F "command_type=absolute_setpoint" \
  -F "tracking_mode=best_effort" \
  -F "match_tolerance_kw=0.25" \
  -F "setpoints_file=@$CASE_DIR/stage2_absolute_out_of_envelope.xlsx"
```

Expected:

- HTTP `200`
- At least one cluster stays `accepted` (not rejected for `OUT_OF_ENVELOPE`)
- `command_status` may include `BEST_EFFORT_DEVIATION`
- Response includes `cluster_tracking_summary`

## 5) Stage-1 Cache Consumption Check

Verify that the same `stage1_id` cannot be reused after successful Stage-2:

```bash
SID="$(stage1_id_from "$CASE_DIR/stage1_baseline_multi_cluster.xlsx")"
$CURL_CMD -sS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=$SID" \
  -F "command_type=absolute_setpoint" \
  -F "setpoints_file=@$CASE_DIR/stage2_absolute_accept_baseline.xlsx" >/dev/null

$CURL_CMD -iS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=$SID" \
  -F "command_type=absolute_setpoint" \
  -F "setpoints_file=@$CASE_DIR/stage2_absolute_accept_baseline.xlsx"
```

Expected:

- Second call returns HTTP `404`.
- Response detail indicates unknown or expired `stage1_id`.

## 6) Output Artifact Check

```bash
SID="$(stage1_id_from "$CASE_DIR/stage1_baseline_multi_cluster.xlsx")"
$CURL_CMD -sS -X POST "$BASE_URL/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=$SID" \
  -F "command_type=absolute_setpoint" \
  -F "setpoints_file=@$CASE_DIR/stage2_absolute_accept_baseline.xlsx" >/dev/null

ls -lah "outputs/jobs/$SID/flex_potential_estimation"
ls -lah "outputs/jobs/$SID/flex_aware_smart_charge_scheduling"
```

Expected files include:

- `outputs/jobs/<stage1_id>/flex_potential_estimation/cluster_capability_timeseries.xlsx`
- `outputs/jobs/<stage1_id>/flex_aware_smart_charge_scheduling/stage2_flex_scheduling_results.xlsx`
