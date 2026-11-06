#!/usr/bin/env bash
set -euo pipefail

# Minimal API smoke flow:
# 1. health/ready probes
# 2. Stage-1 request with canonical day-ahead price input
# 3. Stage-2 strict scheduling request with committed happy-path setpoints
# 4. verification of Stage-1 and Stage-2 workbook artifacts

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
INPUT_FILE="${INPUT_FILE:-inputs/stage1_sample_input.xlsx}"
SETPOINTS_FILE="${SETPOINTS_FILE:-inputs/stage2_sample_absolute_setpoints.xlsx}"
SOLVER_BACKEND="${SOLVER_BACKEND:-glpk}"

if [[ -n "${CURL_BIN:-}" ]]; then
  CURL_BIN="${CURL_BIN}"
elif [[ -x "/usr/bin/curl" ]]; then
  CURL_BIN="/usr/bin/curl"
else
  CURL_BIN="curl"
fi

curl_cmd() {
  if [[ "${UNSET_LD_LIBRARY_PATH:-0}" == "1" ]]; then
    env -u LD_LIBRARY_PATH "${CURL_BIN}" "$@"
  else
    "${CURL_BIN}" "$@"
  fi
}

echo "[smoke] health checks"
for _ in $(seq 1 20); do
  if curl_cmd -fsS "${BASE_URL}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl_cmd -fsS "${BASE_URL}/healthz" >/dev/null
curl_cmd -fsS "${BASE_URL}/readyz" >/dev/null

echo "[smoke] stage-1 request"
STAGE1_JSON="$(curl_cmd -fsS -X POST "${BASE_URL}/v1/flex-potential-estimation" \
  -F "input_file=@${INPUT_FILE}" \
  -F "solver_backend=${SOLVER_BACKEND}")"
STAGE1_ID="$(python3 - <<'PY' "$STAGE1_JSON"
import json
import sys
print(json.loads(sys.argv[1])["stage1_id"])
PY
)"

if [[ -z "${STAGE1_ID}" ]]; then
  echo "[smoke] ERROR: stage1_id not found"
  exit 1
fi

STAGE1_OUTPUT_DIR="outputs/jobs/${STAGE1_ID}/flex_potential_estimation"
STAGE2_OUTPUT_DIR="outputs/jobs/${STAGE1_ID}/flex_aware_smart_charge_scheduling"

echo "[smoke] stage-2 strict request with committed sample setpoints"
STAGE2_JSON="$(curl_cmd -fsS -X POST "${BASE_URL}/v1/flex-aware-smart-charge-scheduling" \
  -F "stage1_id=${STAGE1_ID}" \
  -F "command_type=absolute_setpoint" \
  -F "tracking_mode=strict" \
  -F "setpoints_file=@${SETPOINTS_FILE}")"

python3 - <<'PY' "$STAGE2_JSON" "$STAGE1_ID"
import json
import sys

payload = json.loads(sys.argv[1])
status = payload.get("command_status", [])
if not status:
    raise SystemExit("[smoke] ERROR: command_status is empty")
expected_suffix = f"/jobs/{sys.argv[2]}/flex_aware_smart_charge_scheduling"
output_dir = payload.get("output_dir", "")
if expected_suffix not in output_dir:
    raise SystemExit(
        f"[smoke] ERROR: unexpected stage-2 output_dir={output_dir!r}, "
        f"expected suffix {expected_suffix!r}"
    )
print("[smoke] accepted_clusters:", payload.get("accepted_clusters", []))
print("[smoke] rejected_clusters:", payload.get("rejected_clusters", []))
rows = payload.get("command_status", [])
accepted = [row for row in rows if str(row.get("status", "")) == "accepted"]
if len(accepted) != len(rows):
    raise SystemExit(f"[smoke] ERROR: expected all clusters accepted, got {rows!r}")
PY

if [[ ! -f "${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx" ]]; then
  echo "[smoke] ERROR: missing ${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx"
  exit 1
fi
if [[ ! -f "${STAGE1_OUTPUT_DIR}/day_ahead_smart_charging_schedule.xlsx" ]]; then
  echo "[smoke] ERROR: missing ${STAGE1_OUTPUT_DIR}/day_ahead_smart_charging_schedule.xlsx"
  exit 1
fi
if [[ ! -f "${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx" ]]; then
  echo "[smoke] ERROR: missing ${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx"
  exit 1
fi

echo "[smoke] verified outputs:"
echo "[smoke] - ${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx"
echo "[smoke] - ${STAGE1_OUTPUT_DIR}/day_ahead_smart_charging_schedule.xlsx"
echo "[smoke] - ${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx"
echo "[smoke] completed"
