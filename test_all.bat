@echo off
REM ============================================================
REM Phase 4 canonical regression set (20 test files)
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
  cloud_agent/agent/test/test_metrics.py ^
  cloud_agent/agent/test/test_event_log.py ^
  cloud_agent/agent/test/test_tracing.py ^
  cloud_agent/agent/test/test_health_router.py ^
  cloud_agent/agent/test/test_auth_router.py ^
  cloud_agent/agent/test/test_secrets_config.py ^
  cloud_agent/agent/test/test_container_config.py ^
  cloud_agent/agent/test/test_ci_config.py ^
  cloud_agent/agent/test/test_requirements_constraints.py ^
  cloud_agent/agent/test/test_semantic_cache.py ^
  cloud_agent/agent/test/test_memory_background_extract.py ^
  cloud_agent/agent/test/test_background_preference_extract.py ^
  cloud_agent/agent/test/test_mcp_tool_registry.py ^
  cloud_agent/agent/test/test_tool_audit.py ^
  cloud_agent/agent/test/test_finops_validator.py ^
  cloud_agent/agent/test/test_degradation_audit.py ^
  cloud_agent/agent/test/test_identity_context.py ^
  cloud_agent/agent/test/test_orchestrator_routing.py ^
  deep_research/app/test/test_retrieval_quality_guard.py ^
  --basetemp=.pytest_tmp -q %*

endlocal
