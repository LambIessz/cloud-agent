#!/usr/bin/env bash
# ============================================================
# 第四阶段 - 核心回归集（20 个测试文件）
# 在仓库根目录运行。
# ============================================================
set -euo pipefail

export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

BASETEMP="${BASETEMP:-/tmp/pytest_fresh}"

python -m pytest \
  cloud_agent/agent/test/test_observability_ops.py \
  cloud_agent/agent/test/test_metrics.py \
  cloud_agent/agent/test/test_event_log.py \
  cloud_agent/agent/test/test_tracing.py \
  cloud_agent/agent/test/test_health_router.py \
  cloud_agent/agent/test/test_auth_router.py \
  cloud_agent/agent/test/test_secrets_config.py \
  cloud_agent/agent/test/test_container_config.py \
  cloud_agent/agent/test/test_ci_config.py \
  cloud_agent/agent/test/test_requirements_constraints.py \
  cloud_agent/agent/test/test_semantic_cache.py \
  cloud_agent/agent/test/test_memory_background_extract.py \
  cloud_agent/agent/test/test_background_preference_extract.py \
  cloud_agent/agent/test/test_mcp_tool_registry.py \
  cloud_agent/agent/test/test_tool_audit.py \
  cloud_agent/agent/test/test_finops_validator.py \
  cloud_agent/agent/test/test_degradation_audit.py \
  cloud_agent/agent/test/test_identity_context.py \
  cloud_agent/agent/test/test_orchestrator_routing.py \
  deep_research/app/test/test_retrieval_quality_guard.py \
  --basetemp="${BASETEMP}" -q "$@"
