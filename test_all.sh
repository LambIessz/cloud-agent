#!/usr/bin/env bash
# ============================================================
# 第四阶段 - 核心回归集（23 个 Python 测试文件 + 前端安全/构建）
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
  cloud_agent/agent/test/test_observability_acceptance.py \
  cloud_agent/agent/test/test_observability_window.py \
  cloud_agent/agent/test/test_llm_metrics_callback.py \
  cloud_agent/agent/test/test_metrics.py \
  cloud_agent/agent/test/test_event_log.py \
  cloud_agent/agent/test/test_tracing.py \
  cloud_agent/agent/test/test_health_router.py \
  cloud_agent/agent/test/test_auth_router.py \
  cloud_agent/agent/test/test_app_security_config.py \
  cloud_agent/agent/test/test_streaming_sse.py \
  cloud_agent/agent/test/test_chat_sse_smoke.py \
  cloud_agent/agent/test/test_chat_sse_local_smoke_script.py \
  cloud_agent/agent/test/test_chat_sse_local_doctor_script.py \
  cloud_agent/agent/test/test_deploy_doctor.py \
  cloud_agent/agent/test/test_external_dependency_readonly_smoke.py \
  cloud_agent/agent/test/test_mcp_billing_readonly_smoke.py \
  cloud_agent/agent/test/test_memory_e2e_smoke.py \
  cloud_agent/agent/test/test_real_idp_smoke.py \
  cloud_agent/agent/test/test_release_gate.py \
  cloud_agent/agent/test/test_release_evidence.py \
  cloud_agent/agent/test/test_compose_deploy_smoke_script.py \
  cloud_agent/agent/test/test_local_dev_runbook.py \
  cloud_agent/agent/test/test_frontend_bundle_config.py \
  cloud_agent/agent/test/test_frontend_ui_text_encoding.py \
  cloud_agent/agent/test/test_frontend_app_decomposition.py \
  cloud_agent/agent/test/test_frontend_app_wiring_coverage.py \
  cloud_agent/agent/test/test_frontend_component_decomposition.py \
  cloud_agent/agent/test/test_frontend_shell_component_decomposition.py \
  cloud_agent/agent/test/test_frontend_message_list_behavior_coverage.py \
  cloud_agent/agent/test/test_frontend_session_persistence.py \
  cloud_agent/agent/test/test_frontend_template_cleanup.py \
  cloud_agent/agent/test/test_frontend_style_decomposition.py \
  cloud_agent/agent/test/test_frontend_global_assets_cleanup.py \
  cloud_agent/agent/test/test_frontend_browser_smoke_config.py \
  cloud_agent/agent/test/test_frontend_real_backend_browser_smoke_config.py \
  cloud_agent/agent/test/test_grafana_ui_smoke.py \
  cloud_agent/agent/test/test_secrets_config.py \
  cloud_agent/agent/test/test_container_config.py \
  cloud_agent/agent/test/test_ci_config.py \
  cloud_agent/agent/test/test_requirements_constraints.py \
  cloud_agent/agent/test/test_semantic_cache.py \
  cloud_agent/agent/test/test_memory_background_extract.py \
  cloud_agent/agent/test/test_background_preference_extract.py \
  cloud_agent/agent/test/test_mcp_tool_registry.py \
  cloud_agent/agent/test/test_tool_audit.py \
  cloud_agent/agent/test/test_tool_error_sanitization.py \
  cloud_agent/agent/test/test_finops_validator.py \
  cloud_agent/agent/test/test_degradation_audit.py \
  cloud_agent/agent/test/test_identity_context.py \
  cloud_agent/agent/test/test_orchestrator_routing.py \
  deep_research/app/test \
  --basetemp="${BASETEMP}" -q "$@"

pushd cloud_agent/front/cloud_agent >/dev/null
npm run test:markdown
npm run test:sse
npm run test:scenarios
npm run test:chat-stream
npm run test:sessions
npm run test:chat-controller
npm run test:components
npm run build
popd >/dev/null
