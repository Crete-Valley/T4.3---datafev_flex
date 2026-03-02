#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
CASE_DIR="${CASE_DIR:-inputs/acceptance_cases}"
SOLVER_BACKEND="${SOLVER_BACKEND:-glpk}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
UNSET_LD_LIBRARY_PATH="${UNSET_LD_LIBRARY_PATH:-0}"

if [[ -n "${CURL_BIN:-}" ]]; then
  CURL_BIN="${CURL_BIN}"
elif [[ -x "/usr/bin/curl" ]]; then
  CURL_BIN="/usr/bin/curl"
else
  CURL_BIN="curl"
fi

HTTP_CODE=""
HTTP_BODY=""

log() {
  echo "[acceptance] $*"
}

fail() {
  echo "[acceptance] ERROR: $*" >&2
  exit 1
}

curl_request() {
  local tmp_body
  tmp_body="$(mktemp)"

  if [[ "${UNSET_LD_LIBRARY_PATH}" == "1" ]]; then
    if ! HTTP_CODE="$(env -u LD_LIBRARY_PATH "${CURL_BIN}" -sS -o "${tmp_body}" -w "%{http_code}" "$@")"; then
      local rc=$?
      rm -f "${tmp_body}"
      return "${rc}"
    fi
  else
    if ! HTTP_CODE="$("${CURL_BIN}" -sS -o "${tmp_body}" -w "%{http_code}" "$@")"; then
      local rc=$?
      rm -f "${tmp_body}"
      return "${rc}"
    fi
  fi

  HTTP_BODY="$(cat "${tmp_body}")"
  rm -f "${tmp_body}"
}

require_file() {
  local file_path="$1"
  [[ -f "${file_path}" ]] || fail "Missing required file: ${file_path}"
}

assert_json_status_ok() {
  local payload="$1"
  local field="$2"
  printf "%s" "${payload}" | "${PYTHON_BIN}" - "${field}" <<'PY'
import json
import sys

field = sys.argv[1]
data = json.load(sys.stdin)
if str(data.get(field, "")).lower() != "ok":
    raise SystemExit(f"Expected {field}=ok, got {data.get(field)!r}")
PY
}

extract_stage1_id() {
  local payload="$1"
  printf "%s" "${payload}" | "${PYTHON_BIN}" - <<'PY'
import json
import sys

data = json.load(sys.stdin)
stage1_id = str(data.get("stage1_id", "")).strip()
if not stage1_id:
    raise SystemExit("stage1_id missing in stage-1 response")
print(stage1_id)
PY
}

assert_stage1_payload() {
  local payload="$1"
  local expected="$2"
  printf "%s" "${payload}" | "${PYTHON_BIN}" - "${expected}" <<'PY'
import json
import sys

expected = sys.argv[1]
data = json.load(sys.stdin)
required = ["stage1_id", "cluster_count", "cluster_capability_summary", "job_output_root", "output_dir"]
missing = [key for key in required if key not in data]
if missing:
    raise SystemExit(f"stage-1 response missing keys: {missing}")

cluster_count = int(data["cluster_count"])
if expected == "single":
    if cluster_count != 1:
        raise SystemExit(f"expected cluster_count=1, got {cluster_count}")
else:
    if cluster_count <= 0:
        raise SystemExit(f"expected cluster_count>0, got {cluster_count}")
PY
}

assert_stage2_payload() {
  local payload="$1"
  local expected_reason="${2:-}"
  printf "%s" "${payload}" | "${PYTHON_BIN}" - "${expected_reason}" <<'PY'
import json
import sys

expected_reason = sys.argv[1].strip()
data = json.load(sys.stdin)
status_rows = data.get("command_status", [])
if not isinstance(status_rows, list) or len(status_rows) == 0:
    raise SystemExit("stage-2 response has empty command_status")

if expected_reason:
    reasons = {str(row.get("reason", "")) for row in status_rows}
    if expected_reason not in reasons:
        raise SystemExit(
            f"expected reason {expected_reason!r} not found in command_status reasons: {sorted(reasons)}"
        )
PY
}

assert_404_detail() {
  local payload="$1"
  printf "%s" "${payload}" | "${PYTHON_BIN}" - <<'PY'
import json
import sys

data = json.load(sys.stdin)
detail = str(data.get("detail", ""))
if not detail:
    raise SystemExit("404 response missing detail")
if "stage1_id" not in detail and "expired" not in detail and "unknown" not in detail.lower():
    raise SystemExit(f"404 detail does not explain stage1_id/cache issue: {detail!r}")
PY
}

stage1_id_from() {
  local input_file="$1"
  curl_request -X POST "${BASE_URL}/v1/flex-potential-estimation" \
    -F "input_file=@${input_file}" \
    -F "solver_backend=${SOLVER_BACKEND}"
  [[ "${HTTP_CODE}" == "200" ]] || fail "stage-1 request failed for ${input_file} (HTTP ${HTTP_CODE})"
  printf "%s" "${HTTP_BODY}"
}

run_stage2_from_file() {
  local stage1_id="$1"
  local command_type="$2"
  local setpoints_file="$3"
  shift 3
  curl_request -X POST "${BASE_URL}/v1/flex-aware-smart-charge-scheduling" \
    -F "stage1_id=${stage1_id}" \
    -F "command_type=${command_type}" \
    -F "setpoints_file=@${setpoints_file}" \
    "$@"
}

run_health_checks() {
  log "health checks"
  local ready=0
  for _ in $(seq 1 20); do
    if curl_request "${BASE_URL}/healthz"; then
      if [[ "${HTTP_CODE}" == "200" ]]; then
        ready=1
        break
      fi
    fi
    sleep 1
  done
  [[ "${ready}" == "1" ]] || fail "healthz did not become ready at ${BASE_URL}"

  curl_request "${BASE_URL}/healthz"
  [[ "${HTTP_CODE}" == "200" ]] || fail "healthz returned HTTP ${HTTP_CODE}"
  assert_json_status_ok "${HTTP_BODY}" "status"

  curl_request "${BASE_URL}/readyz"
  [[ "${HTTP_CODE}" == "200" ]] || fail "readyz returned HTTP ${HTTP_CODE}"
  assert_json_status_ok "${HTTP_BODY}" "status"
}

run_stage1_matrix() {
  log "stage-1 matrix"
  local stage1_file
  for stage1_file in \
    "${CASE_DIR}/stage1_baseline_multi_cluster.xlsx" \
    "${CASE_DIR}/stage1_single_cluster_short_horizon.xlsx" \
    "${CASE_DIR}/stage1_no_v2g_cluster2.xlsx"
  do
    local expected="multi"
    if [[ "${stage1_file}" == *"single_cluster_short_horizon"* ]]; then
      expected="single"
    fi
    log "stage-1 case: ${stage1_file}"
    local payload
    payload="$(stage1_id_from "${stage1_file}")"
    assert_stage1_payload "${payload}" "${expected}"
  done
}

run_stage2_absolute_matrix() {
  log "stage-2 absolute matrix"
  local baseline_stage1="${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  local stage2_file
  for stage2_file in \
    "${CASE_DIR}/stage2_absolute_accept_baseline.xlsx" \
    "${CASE_DIR}/stage2_absolute_out_of_envelope.xlsx" \
    "${CASE_DIR}/stage2_absolute_timestep_mismatch.xlsx" \
    "${CASE_DIR}/stage2_absolute_unknown_cluster.xlsx"
  do
    local stage1_payload
    stage1_payload="$(stage1_id_from "${baseline_stage1}")"
    local sid
    sid="$(extract_stage1_id "${stage1_payload}")"
    log "stage-2 absolute case: ${stage2_file} (sid=${sid})"

    run_stage2_from_file "${sid}" "absolute_setpoint" "${stage2_file}"
    [[ "${HTTP_CODE}" == "200" ]] || fail "stage-2 absolute failed for ${stage2_file} (HTTP ${HTTP_CODE})"

    local expected_reason=""
    case "${stage2_file}" in
      *out_of_envelope*) expected_reason="OUT_OF_ENVELOPE" ;;
      *timestep_mismatch*) expected_reason="TIMESTEP_MISMATCH" ;;
      *unknown_cluster*) expected_reason="UNKNOWN_CLUSTER" ;;
    esac
    assert_stage2_payload "${HTTP_BODY}" "${expected_reason}"
  done
}

run_stage2_flex_band_matrix() {
  log "stage-2 flex-band matrix"
  local baseline_stage1="${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  local stage2_file
  for stage2_file in \
    "${CASE_DIR}/stage2_flex_band_accept_baseline.xlsx" \
    "${CASE_DIR}/stage2_flex_band_out_of_envelope.xlsx" \
    "${CASE_DIR}/stage2_flex_band_invalid_range.xlsx"
  do
    local stage1_payload
    stage1_payload="$(stage1_id_from "${baseline_stage1}")"
    local sid
    sid="$(extract_stage1_id "${stage1_payload}")"
    log "stage-2 flex-band case: ${stage2_file} (sid=${sid})"

    run_stage2_from_file "${sid}" "flex_band" "${stage2_file}"
    [[ "${HTTP_CODE}" == "200" ]] || fail "stage-2 flex-band failed for ${stage2_file} (HTTP ${HTTP_CODE})"

    local expected_reason=""
    case "${stage2_file}" in
      *out_of_envelope*) expected_reason="OUT_OF_ENVELOPE" ;;
      *invalid_range*) expected_reason="INVALID_BAND_RANGE" ;;
    esac
    assert_stage2_payload "${HTTP_BODY}" "${expected_reason}"
  done
}

run_stage2_best_effort_absolute_check() {
  log "stage-2 best-effort absolute check"
  local baseline_stage1="${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  local stage2_file="${CASE_DIR}/stage2_absolute_out_of_envelope.xlsx"

  local stage1_payload
  stage1_payload="$(stage1_id_from "${baseline_stage1}")"
  local sid
  sid="$(extract_stage1_id "${stage1_payload}")"

  run_stage2_from_file "${sid}" "absolute_setpoint" "${stage2_file}" \
    -F "tracking_mode=best_effort" \
    -F "match_tolerance_kw=0.25"
  [[ "${HTTP_CODE}" == "200" ]] || fail "stage-2 best-effort failed (HTTP ${HTTP_CODE})"

  printf "%s" "${HTTP_BODY}" | "${PYTHON_BIN}" - <<'PY'
import json
import sys

data = json.load(sys.stdin)
status_rows = data.get("command_status", [])
if not status_rows:
    raise SystemExit("empty command_status in best_effort response")
accepted = [row for row in status_rows if str(row.get("status", "")) == "accepted"]
if not accepted:
    raise SystemExit("best_effort response has no accepted clusters")
if str(data.get("tracking_mode", "")) != "best_effort":
    raise SystemExit(f"tracking_mode is not best_effort: {data.get('tracking_mode')!r}")
if "cluster_tracking_summary" not in data:
    raise SystemExit("missing cluster_tracking_summary in best_effort response")
PY
}

run_cache_consumption_check() {
  log "stage-1 cache consumption check"
  local baseline_stage1="${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  local stage2_ok_file="${CASE_DIR}/stage2_absolute_accept_baseline.xlsx"

  local stage1_payload
  stage1_payload="$(stage1_id_from "${baseline_stage1}")"
  local sid
  sid="$(extract_stage1_id "${stage1_payload}")"

  run_stage2_from_file "${sid}" "absolute_setpoint" "${stage2_ok_file}"
  [[ "${HTTP_CODE}" == "200" ]] || fail "first stage-2 cache check call failed (HTTP ${HTTP_CODE})"
  assert_stage2_payload "${HTTP_BODY}" ""

  run_stage2_from_file "${sid}" "absolute_setpoint" "${stage2_ok_file}"
  [[ "${HTTP_CODE}" == "404" ]] || fail "expected HTTP 404 on reused stage1_id, got ${HTTP_CODE}"
  assert_404_detail "${HTTP_BODY}"
}

run_output_artifact_check() {
  log "output artifact check"
  local baseline_stage1="${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  local stage2_ok_file="${CASE_DIR}/stage2_absolute_accept_baseline.xlsx"

  local stage1_payload
  stage1_payload="$(stage1_id_from "${baseline_stage1}")"
  local sid
  sid="$(extract_stage1_id "${stage1_payload}")"

  run_stage2_from_file "${sid}" "absolute_setpoint" "${stage2_ok_file}"
  [[ "${HTTP_CODE}" == "200" ]] || fail "stage-2 artifact check call failed (HTTP ${HTTP_CODE})"
  assert_stage2_payload "${HTTP_BODY}" ""

  local stage1_artifact="${OUTPUT_ROOT}/jobs/${sid}/flex_potential_estimation/cluster_capability_timeseries.xlsx"
  local stage2_artifact="${OUTPUT_ROOT}/jobs/${sid}/flex_aware_smart_charge_scheduling/stage2_flex_scheduling_results.xlsx"
  [[ -f "${stage1_artifact}" ]] || fail "missing artifact: ${stage1_artifact}"
  [[ -f "${stage2_artifact}" ]] || fail "missing artifact: ${stage2_artifact}"
}

validate_prerequisites() {
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "Python not found: ${PYTHON_BIN}"
  command -v "${CURL_BIN}" >/dev/null 2>&1 || fail "curl not found: ${CURL_BIN}"

  require_file "${CASE_DIR}/stage1_baseline_multi_cluster.xlsx"
  require_file "${CASE_DIR}/stage1_single_cluster_short_horizon.xlsx"
  require_file "${CASE_DIR}/stage1_no_v2g_cluster2.xlsx"
  require_file "${CASE_DIR}/stage2_absolute_accept_baseline.xlsx"
  require_file "${CASE_DIR}/stage2_absolute_out_of_envelope.xlsx"
  require_file "${CASE_DIR}/stage2_absolute_timestep_mismatch.xlsx"
  require_file "${CASE_DIR}/stage2_absolute_unknown_cluster.xlsx"
  require_file "${CASE_DIR}/stage2_flex_band_accept_baseline.xlsx"
  require_file "${CASE_DIR}/stage2_flex_band_out_of_envelope.xlsx"
  require_file "${CASE_DIR}/stage2_flex_band_invalid_range.xlsx"
}

main() {
  validate_prerequisites
  run_health_checks
  run_stage1_matrix
  run_stage2_absolute_matrix
  run_stage2_flex_band_matrix
  run_stage2_best_effort_absolute_check
  run_cache_consumption_check
  run_output_artifact_check
  log "acceptance matrix completed successfully"
}

main "$@"
