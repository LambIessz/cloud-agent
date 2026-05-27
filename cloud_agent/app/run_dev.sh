#!/usr/bin/env bash
# ============================================================
# cloud_agent FastAPI 后端本地开发启动脚本 (Linux / Ubuntu VM / WSL)
#
# 固化以下环境约束（详见 后续改进计划.md 第 4.3 节）：
#   PYTHONIOENCODING / PYTHONUTF8       UTF-8 输出一致性
#   HF_ENDPOINT                         HuggingFace 国内镜像
#   CLOUD_AGENT_LLM_PRICING_CONFIG      LLM 估价配置（相对仓库根）
# ============================================================
set -euo pipefail

_require_python() {
  local min_major=3 min_minor=10
  local py_version
  py_version="$("$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || {
    echo "[run_dev.sh] ERROR: cannot run python ($1). Is Python installed?" >&2
    exit 1
  }
  local major minor
  major="${py_version%%.*}"
  minor="${py_version#*.}"
  if [[ $major -lt $min_major ]] || { [[ $major -eq $min_major ]] && [[ $minor -lt $min_minor ]]; }; then
    echo "[run_dev.sh] ERROR: Python $py_version detected, but langchain>=1.2.0 requires Python >=3.10." >&2
    echo "[run_dev.sh]        Install python3.10+ and retry (conda/pyenv/deadsnakes PPA)." >&2
    exit 1
  fi
}

_require_python python

export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export CLOUD_AGENT_LLM_PRICING_CONFIG="${CLOUD_AGENT_LLM_PRICING_CONFIG:-${REPO_ROOT}/ops/prometheus/llm_pricing.example.yml}"

echo "[run_dev.sh] PYTHONUTF8=${PYTHONUTF8}"
echo "[run_dev.sh] HF_ENDPOINT=${HF_ENDPOINT}"
echo "[run_dev.sh] CLOUD_AGENT_LLM_PRICING_CONFIG=${CLOUD_AGENT_LLM_PRICING_CONFIG}"
echo "[run_dev.sh] Starting FastAPI on :5000 ..."

cd "${SCRIPT_DIR}"
exec python -X utf8 app_main.py "$@"
