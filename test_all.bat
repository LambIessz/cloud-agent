@echo off
REM ============================================================
REM Phase 4 canonical regression set (Python regression + frontend security/build)
REM
REM Run from the repository root. --basetemp is kept inside the
REM repository to avoid Windows AppData Temp locks / WinError 5.
REM ============================================================

setlocal

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"

python -m pytest ^
  cloud_agent/agent/test/test_observability_ops.py ^
  cloud_agent/agent/test/test_observability_acceptance.py ^
  cloud_agent/agent/test/test_observability_window.py ^
  cloud_agent/agent/test/test_llm_metrics_callback.py ^
  cloud_agent/agent/test/test_metrics.py ^
  cloud_agent/agent/test/test_event_log.py ^
  cloud_agent/agent/test/test_tracing.py ^
  cloud_agent/agent/test/test_health_router.py ^
  cloud_agent/agent/test/test_auth_router.py ^
  cloud_agent/agent/test/test_app_security_config.py ^
  cloud_agent/agent/test/test_streaming_sse.py ^
  cloud_agent/agent/test/test_chat_sse_smoke.py ^
  cloud_agent/agent/test/test_chat_sse_local_smoke_script.py ^
  cloud_agent/agent/test/test_chat_sse_local_doctor_script.py ^
  cloud_agent/agent/test/test_deploy_doctor.py ^
  cloud_agent/agent/test/test_external_dependency_readonly_smoke.py ^
  cloud_agent/agent/test/test_mcp_billing_readonly_smoke.py ^
  cloud_agent/agent/test/test_memory_e2e_smoke.py ^
  cloud_agent/agent/test/test_real_idp_smoke.py ^
  cloud_agent/agent/test/test_release_gate.py ^
  cloud_agent/agent/test/test_release_evidence.py ^
  cloud_agent/agent/test/test_compose_deploy_smoke_script.py ^
  cloud_agent/agent/test/test_frontend_bundle_config.py ^
  cloud_agent/agent/test/test_frontend_ui_text_encoding.py ^
  cloud_agent/agent/test/test_frontend_app_decomposition.py ^
  cloud_agent/agent/test/test_frontend_app_wiring_coverage.py ^
  cloud_agent/agent/test/test_frontend_component_decomposition.py ^
  cloud_agent/agent/test/test_frontend_shell_component_decomposition.py ^
  cloud_agent/agent/test/test_frontend_message_list_behavior_coverage.py ^
  cloud_agent/agent/test/test_frontend_session_persistence.py ^
  cloud_agent/agent/test/test_frontend_template_cleanup.py ^
  cloud_agent/agent/test/test_frontend_style_decomposition.py ^
  cloud_agent/agent/test/test_frontend_global_assets_cleanup.py ^
  cloud_agent/agent/test/test_frontend_browser_smoke_config.py ^
  cloud_agent/agent/test/test_frontend_real_backend_browser_smoke_config.py ^
  cloud_agent/agent/test/test_grafana_ui_smoke.py ^
  cloud_agent/agent/test/test_secrets_config.py ^
  cloud_agent/agent/test/test_container_config.py ^
  cloud_agent/agent/test/test_ci_config.py ^
  cloud_agent/agent/test/test_requirements_constraints.py ^
  cloud_agent/agent/test/test_semantic_cache.py ^
  cloud_agent/agent/test/test_memory_background_extract.py ^
  cloud_agent/agent/test/test_background_preference_extract.py ^
  cloud_agent/agent/test/test_mcp_tool_registry.py ^
  cloud_agent/agent/test/test_tool_audit.py ^
  cloud_agent/agent/test/test_tool_error_sanitization.py ^
  cloud_agent/agent/test/test_finops_validator.py ^
  cloud_agent/agent/test/test_degradation_audit.py ^
  cloud_agent/agent/test/test_identity_context.py ^
  cloud_agent/agent/test/test_orchestrator_routing.py ^
  deep_research/app/test ^
  --basetemp=.pytest_tmp -q %*

if errorlevel 1 exit /b %errorlevel%

pushd cloud_agent\front\cloud_agent
call npm run test:markdown
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:sse
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:scenarios
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:chat-stream
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:sessions
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:chat-controller
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run test:components
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
call npm run build
if errorlevel 1 (
  popd
  exit /b %errorlevel%
)
popd

endlocal
