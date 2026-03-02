#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
INPUT_FILE="${INPUT_FILE:-inputs/stage1_sample_input.xlsx}"

echo "[smoke] health checks"
for _ in $(seq 1 20); do
  if curl -fsS "${BASE_URL}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "${BASE_URL}/healthz" >/dev/null
curl -fsS "${BASE_URL}/readyz" >/dev/null

echo "[smoke] stage-1 request"
STAGE1_JSON="$(curl -fsS -X POST "${BASE_URL}/v1/flex-potential-estimation" \
  -F "input_file=@${INPUT_FILE}" \
  -F "solver_backend=glpk")"
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

echo "[smoke] stage-2 midpoint request (acceptance path)"
STAGE2_JSON="$(curl -fsS -X POST "${BASE_URL}/v1/flex-aware-smart-charge-scheduling" \
  -H "Content-Type: application/json" \
  -d "{\"stage1_id\":\"${STAGE1_ID}\",\"command_type\":\"absolute_setpoint\",\"command_strategy\":\"midpoint\"}")"

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
PY

if [[ ! -f "${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx" ]]; then
  echo "[smoke] ERROR: missing ${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx"
  exit 1
fi
if [[ ! -f "${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx" ]]; then
  echo "[smoke] ERROR: missing ${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx"
  exit 1
fi

echo "[smoke] verified outputs:"
echo "[smoke] - ${STAGE1_OUTPUT_DIR}/cluster_capability_timeseries.xlsx"
echo "[smoke] - ${STAGE2_OUTPUT_DIR}/stage2_flex_scheduling_results.xlsx"
echo "[smoke] completed"
