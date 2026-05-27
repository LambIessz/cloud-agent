#!/usr/bin/env bash
# Ubuntu VM / CI acceptance helper for cloud_agent.
#
# Run from the repository root or from any subdirectory:
#   bash ops/ubuntu_ci_acceptance.sh
#
# Optional runtime checks:
#   CLOUD_AGENT_BASE_URL=http://127.0.0.1:5000 \
#   PROMETHEUS_URL=http://127.0.0.1:9090 \
#   GRAFANA_URL=http://127.0.0.1:3000 \
#   RUN_CHAT_SMOKE=1 \
#   bash ops/ubuntu_ci_acceptance.sh
#
# The script does not archive chat response bodies or /api/metrics bodies.
# It only records status, command output, and low-cardinality summaries.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARTIFACT_DIR="${ACCEPTANCE_ARTIFACT_DIR:-${REPO_ROOT}/.acceptance/${STAMP}}"
SUMMARY_FILE="${ARTIFACT_DIR}/summary.tsv"

BASE_URL="${CLOUD_AGENT_BASE_URL:-http://127.0.0.1:5000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"

export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_SYMLINKS_WARNING="${HF_HUB_DISABLE_SYMLINKS_WARNING:-1}"
export CLOUD_AGENT_LLM_PRICING_CONFIG="${CLOUD_AGENT_LLM_PRICING_CONFIG:-${REPO_ROOT}/ops/prometheus/llm_pricing.example.yml}"

if [[ -z "${DEEPSEEK_API_KEY:-}" && -z "${DEEPSEEK_API_KEY_FILE:-}" ]]; then
  export DEEPSEEK_API_KEY="ci-placeholder"
fi

mkdir -p "${ARTIFACT_DIR}"
printf "step\tstatus\tdetail\n" > "${SUMMARY_FILE}"

FAILURES=0
BLOCKED=0

log() {
  printf '[acceptance] %s\n' "$*"
}

record() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "${SUMMARY_FILE}"
}

run_step() {
  local name="$1"
  shift
  local stdout_file="${ARTIFACT_DIR}/${name}.stdout"
  local stderr_file="${ARTIFACT_DIR}/${name}.stderr"

  log "running ${name}"
  "$@" >"${stdout_file}" 2>"${stderr_file}"
  local code=$?

  if [[ ${code} -eq 0 ]]; then
    record "${name}" "PASS" "exit=0"
  else
    record "${name}" "FAIL" "exit=${code}; see ${stdout_file} and ${stderr_file}"
    FAILURES=$((FAILURES + 1))
  fi
}

block_step() {
  record "$1" "BLOCKED" "$2"
  BLOCKED=$((BLOCKED + 1))
}

check_http_status() {
  local url="$1"
  python3 - "$url" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
request = urllib.request.Request(url, headers={"User-Agent": "cloud-agent-acceptance"})
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        print(f"http_status={response.status}")
        if response.status >= 400:
            raise SystemExit(1)
except Exception as exc:
    print(f"http_error_type={type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)
PY
}

check_metrics_summary() {
  local url="${BASE_URL}/api/metrics"
  python3 - "$url" <<'PY'
import re
import sys
import urllib.request

url = sys.argv[1]
forbidden = (
    "request_id",
    "user_id=",
    "user_id_hash",
    "tenant_id=",
    "session_id",
    "thread_id",
    "conversation_id",
    "prompt=",
    "completion=",
    "query=",
    "matched_question",
)

request = urllib.request.Request(url, headers={"User-Agent": "cloud-agent-acceptance"})
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("utf-8", errors="replace")
except Exception as exc:
    print(f"http_error_type={type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)

lowered = text.lower()
leaked = [term for term in forbidden if term in lowered]
if leaked:
    print("forbidden_terms_present=true", file=sys.stderr)
    raise SystemExit(1)

families = sorted(set(re.findall(r"^# HELP (cloud_agent_[A-Za-z0-9_]+)", text, re.MULTILINE)))
print(f"metric_family_count={len(families)}")
for family in families[:80]:
    print(f"metric_family={family}")
PY
}

query_prometheus_summary() {
  local promql="$1"
  python3 - "$PROMETHEUS_URL" "$promql" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base_url, promql = sys.argv[1], sys.argv[2]
url = f"{base_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
request = urllib.request.Request(url, headers={"User-Agent": "cloud-agent-acceptance"})

try:
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    print(f"http_error_type={type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)

if payload.get("status") != "success":
    print("prometheus_status_not_success=true", file=sys.stderr)
    raise SystemExit(1)

result = payload.get("data", {}).get("result", [])
print(f"result_count={len(result)}")
if result:
    value = result[0].get("value", [None, None])[1]
    print(f"first_value={value}")
PY
}

check_grafana_dashboard() {
  python3 - "$GRAFANA_URL" <<'PY'
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

base_url = sys.argv[1].rstrip("/")
user = os.getenv("GRAFANA_USER", "")
password = os.getenv("GRAFANA_PASSWORD", "")
if not user or not password:
    print("grafana_credentials=not_set")
    raise SystemExit(2)

url = f"{base_url}/api/search?{urllib.parse.urlencode({'query': 'Cloud Agent Overview'})}"
token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
request = urllib.request.Request(
    url,
    headers={
        "Authorization": f"Basic {token}",
        "User-Agent": "cloud-agent-acceptance",
    },
)

try:
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    print(f"http_error_type={type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)

matches = [
    item
    for item in payload
    if item.get("title") == "Cloud Agent Overview"
]
print(f"dashboard_match_count={len(matches)}")
if not matches:
    raise SystemExit(1)
PY
}

run_chat_smoke() {
  python3 - "$BASE_URL" <<'PY'
import json
import os
import sys
import urllib.request

base_url = sys.argv[1].rstrip("/")
payload = {
    "query": os.getenv("CHAT_SMOKE_TEXT", "ping"),
    "session_id": "acceptance_session",
}
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
headers = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "User-Agent": "cloud-agent-acceptance",
}

auth_user = os.getenv("CHAT_SMOKE_AUTH_USER")
if auth_user:
    user_header = os.getenv("CLOUD_AGENT_AUTH_USER_HEADER", "X-Authenticated-User-Id")
    headers[user_header] = auth_user

auth_tenant = os.getenv("CHAT_SMOKE_AUTH_TENANT")
if auth_tenant:
    tenant_header = os.getenv("CLOUD_AGENT_AUTH_TENANT_HEADER", "X-Authenticated-Tenant-Id")
    headers[tenant_header] = auth_tenant

request = urllib.request.Request(
    f"{base_url}/api/chat",
    data=body,
    headers=headers,
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=90) as response:
        for _ in response:
            pass
        print(f"http_status={response.status}")
except Exception as exc:
    print(f"http_error_type={type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)
PY
}

run_step "canonical_regression" bash test_all.sh

if command -v promtool >/dev/null 2>&1; then
  run_step "promtool_rules" promtool check rules ops/prometheus/cloud_agent_alerts.yml
else
  block_step "promtool_rules" "promtool_not_found"
fi

run_step "healthz" check_http_status "${BASE_URL}/healthz"
run_step "readyz" check_http_status "${BASE_URL}/readyz"
run_step "metrics_summary" check_metrics_summary

if [[ "${RUN_CHAT_SMOKE:-0}" == "1" ]]; then
  run_step "chat_smoke" run_chat_smoke
  run_step "metrics_summary_after_chat" check_metrics_summary
else
  block_step "chat_smoke" "set RUN_CHAT_SMOKE=1 to execute synthetic chat traffic"
fi

run_step "prometheus_ready" check_http_status "${PROMETHEUS_URL}/-/ready"
run_step "prometheus_up_cloud_agent" query_prometheus_summary 'up{job="cloud_agent"}'
run_step "prometheus_request_metric" query_prometheus_summary 'cloud_agent_request_total'
run_step "prometheus_llm_metric" query_prometheus_summary 'cloud_agent_llm_call_total or cloud_agent_llm_estimated_cost_usd_total'
run_step "prometheus_tool_metric" query_prometheus_summary 'cloud_agent_tool_call_total or cloud_agent_mcp_registry_initialize_total'
run_step "prometheus_cache_benefit_metric" query_prometheus_summary 'cloud_agent_semantic_cache_estimated_saved_call_total or cloud_agent_semantic_cache_hit_total'

run_step "grafana_health" check_http_status "${GRAFANA_URL}/api/health"
check_grafana_dashboard >"${ARTIFACT_DIR}/grafana_dashboard.stdout" 2>"${ARTIFACT_DIR}/grafana_dashboard.stderr"
case $? in
  0)
    record "grafana_dashboard" "PASS" "dashboard_found"
    ;;
  2)
    block_step "grafana_dashboard" "set GRAFANA_USER and GRAFANA_PASSWORD to verify dashboard via API"
    ;;
  *)
    record "grafana_dashboard" "FAIL" "dashboard_missing_or_api_error"
    FAILURES=$((FAILURES + 1))
    ;;
esac

log "artifacts: ${ARTIFACT_DIR}"
log "summary:"
cat "${SUMMARY_FILE}"

if [[ ${FAILURES} -gt 0 ]]; then
  log "completed with ${FAILURES} failed step(s) and ${BLOCKED} blocked step(s)"
  exit 1
fi

log "completed with 0 failed step(s) and ${BLOCKED} blocked step(s)"
exit 0
