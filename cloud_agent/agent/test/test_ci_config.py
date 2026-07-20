from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cloud-agent-regression.yml"
BROWSER_SMOKE_WORKFLOW_PATH = (
    PROJECT_ROOT / ".github" / "workflows" / "cloud-agent-browser-smoke.yml"
)
SUPPLY_CHAIN_WORKFLOW_PATH = (
    PROJECT_ROOT / ".github" / "workflows" / "cloud-agent-supply-chain.yml"
)

SENSITIVE_TERMS = {
    "user_id",
    "user_id_hash",
    "tenant_id",
    "session_id",
    "thread_id",
    "conversation_id",
    "query",
    "prompt",
    "completion",
    "matched_question",
}


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _github_on(workflow: dict):
    return workflow.get("on", workflow.get(True))


def test_github_actions_workflow_runs_canonical_regression_without_real_secrets():
    assert WORKFLOW_PATH.exists()
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _load_workflow()

    triggers = _github_on(workflow)
    assert {"push", "pull_request", "workflow_dispatch"}.issubset(triggers)
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["pytest"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 45

    env = job["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert env["CLOUD_AGENT_LLM_PRICING_CONFIG"].endswith(
        "/ops/prometheus/llm_pricing.example.yml"
    )
    assert env["DEEPSEEK_API_KEY"] == "ci-placeholder"
    assert "".join(("secrets", ".")) not in workflow_text
    assert "sk-" not in workflow_text
    assert "".join(("4AM", "DiDi", "Wei")) not in workflow_text

    steps = job["steps"]
    setup_python_steps = [
        step for step in steps if step.get("uses") == "actions/setup-python@v5"
    ]
    assert len(setup_python_steps) == 1
    assert setup_python_steps[0]["with"]["python-version"] == "3.12"
    setup_node_steps = [
        step for step in steps if step.get("uses") == "actions/setup-node@v4"
    ]
    assert len(setup_node_steps) == 1
    assert setup_node_steps[0]["with"]["node-version"] == "24"
    assert setup_node_steps[0]["with"]["cache"] == "npm"

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "python -m pip install -r cloud_agent/requirements-container.txt" in run_commands
    assert "npm ci" in run_commands
    assert "bash test_all.sh" in run_commands

    lowered = workflow_text.lower()
    leaked_terms = {
        term
        for term in SENSITIVE_TERMS
        if f"{term}=" in lowered or f"{term}:" in lowered
    }
    assert leaked_terms == set()


def test_canonical_regression_scripts_include_frontend_security_checks():
    required_entries = {
        "cloud_agent/agent/test/test_app_security_config.py",
        "cloud_agent/agent/test/test_streaming_sse.py",
        "cloud_agent/agent/test/test_chat_sse_smoke.py",
        "cloud_agent/agent/test/test_chat_sse_local_smoke_script.py",
        "cloud_agent/agent/test/test_chat_sse_local_doctor_script.py",
        "cloud_agent/agent/test/test_compose_deploy_smoke_script.py",
        "cloud_agent/agent/test/test_deploy_doctor.py",
        "cloud_agent/agent/test/test_external_dependency_readonly_smoke.py",
        "cloud_agent/agent/test/test_mcp_billing_readonly_smoke.py",
        "cloud_agent/agent/test/test_memory_e2e_smoke.py",
        "cloud_agent/agent/test/test_real_idp_smoke.py",
        "cloud_agent/agent/test/test_release_gate.py",
        "cloud_agent/agent/test/test_release_evidence.py",
        "cloud_agent/agent/test/test_observability_acceptance.py",
        "cloud_agent/agent/test/test_observability_window.py",
        "cloud_agent/agent/test/test_llm_metrics_callback.py",
        "cloud_agent/agent/test/test_frontend_bundle_config.py",
        "cloud_agent/agent/test/test_frontend_ui_text_encoding.py",
        "cloud_agent/agent/test/test_frontend_app_decomposition.py",
        "cloud_agent/agent/test/test_frontend_app_wiring_coverage.py",
        "cloud_agent/agent/test/test_frontend_component_decomposition.py",
        "cloud_agent/agent/test/test_frontend_shell_component_decomposition.py",
        "cloud_agent/agent/test/test_frontend_message_list_behavior_coverage.py",
        "cloud_agent/agent/test/test_frontend_session_persistence.py",
        "cloud_agent/agent/test/test_frontend_template_cleanup.py",
        "cloud_agent/agent/test/test_frontend_style_decomposition.py",
        "cloud_agent/agent/test/test_frontend_global_assets_cleanup.py",
        "cloud_agent/agent/test/test_frontend_browser_smoke_config.py",
        "cloud_agent/agent/test/test_frontend_real_backend_browser_smoke_config.py",
        "cloud_agent/agent/test/test_grafana_ui_smoke.py",
        "cloud_agent/agent/test/test_tool_error_sanitization.py",
        "npm run test:markdown",
        "npm run test:sse",
        "npm run test:scenarios",
        "npm run test:chat-stream",
        "npm run test:sessions",
        "npm run test:chat-controller",
        "npm run test:components",
        "npm run build",
    }
    scripts = [
        PROJECT_ROOT / "test_all.sh",
        PROJECT_ROOT / "test_all.bat",
    ]

    for script in scripts:
        text = script.read_text(encoding="utf-8").replace("\\", "/")
        for entry in required_entries:
            assert entry in text


def test_browser_smoke_workflow_is_optional_and_runs_playwright_smoke():
    assert BROWSER_SMOKE_WORKFLOW_PATH.exists()
    workflow_text = BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    triggers = _github_on(workflow)
    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert triggers["schedule"] == [{"cron": "0 20 * * 0"}]
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["browser-smoke"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 20
    assert job["env"]["CI"] == "true"

    steps = job["steps"]
    assert any(step.get("uses") == "actions/checkout@v4" for step in steps)
    setup_node_steps = [
        step for step in steps if step.get("uses") == "actions/setup-node@v4"
    ]
    assert len(setup_node_steps) == 1
    assert setup_node_steps[0]["with"]["node-version"] == "24"
    assert setup_node_steps[0]["with"]["cache"] == "npm"
    assert (
        setup_node_steps[0]["with"]["cache-dependency-path"]
        == "cloud_agent/front/cloud_agent/package-lock.json"
    )

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "npm ci" in run_commands
    assert "npx playwright install --with-deps chromium" in run_commands
    assert "npm run smoke:browser" in run_commands
    assert "test_all.sh" not in run_commands
    assert "".join(("secrets", ".")) not in workflow_text


def test_browser_smoke_workflow_uploads_failure_diagnostics():
    workflow = yaml.safe_load(BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["browser-smoke"]["steps"]

    upload_steps = [
        step for step in steps if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert len(upload_steps) == 1

    upload_step = upload_steps[0]
    assert upload_step["if"] == "${{ failure() }}"
    assert upload_step["with"]["name"] == "browser-smoke-artifacts"
    assert upload_step["with"]["if-no-files-found"] == "ignore"
    assert upload_step["with"]["retention-days"] == 7

    artifact_paths = upload_step["with"]["path"]
    assert "cloud_agent/front/cloud_agent/playwright-report/" in artifact_paths
    assert "cloud_agent/front/cloud_agent/test-results/" in artifact_paths


def test_browser_smoke_workflow_runs_real_backend_browser_smoke_optionally():
    workflow = yaml.safe_load(BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["real-backend-browser-smoke"]

    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 30
    assert job["env"]["CI"] == "true"

    steps = job["steps"]
    assert any(step.get("uses") == "actions/checkout@v4" for step in steps)
    assert any(step.get("uses") == "actions/setup-python@v5" for step in steps)
    setup_node_steps = [
        step for step in steps if step.get("uses") == "actions/setup-node@v4"
    ]
    assert len(setup_node_steps) == 1
    assert setup_node_steps[0]["with"]["node-version"] == "24"
    assert setup_node_steps[0]["with"]["cache"] == "npm"

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "python -m pip install -r cloud_agent/requirements-container.txt" in run_commands
    assert "npm ci" in run_commands
    assert "npx playwright install --with-deps chromium" in run_commands
    assert "npm run smoke:browser:real-backend" in run_commands
    assert "test_all.sh" not in run_commands


def test_browser_smoke_workflow_uploads_real_backend_failure_diagnostics():
    workflow = yaml.safe_load(BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["real-backend-browser-smoke"]["steps"]

    upload_steps = [
        step for step in steps if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert len(upload_steps) == 1

    upload_step = upload_steps[0]
    assert upload_step["if"] == "${{ failure() }}"
    assert upload_step["with"]["name"] == "real-backend-browser-smoke-artifacts"
    assert upload_step["with"]["if-no-files-found"] == "ignore"
    assert upload_step["with"]["retention-days"] == 7

    artifact_paths = upload_step["with"]["path"]
    assert "cloud_agent/front/cloud_agent/playwright-report-real-backend/" in artifact_paths
    assert "cloud_agent/front/cloud_agent/test-results-real-backend/" in artifact_paths


def test_browser_smoke_workflow_runs_deploy_doctor_with_fake_backend():
    workflow_text = BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["deploy-doctor"]

    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 20
    assert job["env"]["CI"] == "true"
    assert job["env"]["PYTHONIOENCODING"] == "utf-8"
    assert job["env"]["PYTHONUTF8"] == "1"
    assert job["env"]["DEEPSEEK_API_KEY"] == "ci-doctor-key"
    assert job["env"]["CLOUD_AGENT_SMOKE_FAKE_GRAPH"] == "true"
    assert job["env"]["CLOUD_AGENT_SEMANTIC_CACHE_ENABLED"] == "false"
    assert job["env"]["CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED"] == "false"
    assert job["env"]["CLOUD_AGENT_VECTOR_SEARCH_ENABLED"] == "false"
    assert "".join(("secrets", ".")) not in workflow_text
    assert "sk-" not in workflow_text

    steps = job["steps"]
    assert any(step.get("uses") == "actions/checkout@v4" for step in steps)
    assert any(step.get("uses") == "actions/setup-python@v5" for step in steps)

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "python -m pip install -r cloud_agent/requirements-container.txt" in run_commands
    assert "python -X utf8 -m uvicorn app_main:app" in run_commands
    assert "http://127.0.0.1:15300/readyz" in run_commands
    assert "python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:15300 --json" in run_commands
    assert ".cloud-agent-doctor/doctor.json" in run_commands


def test_browser_smoke_workflow_uploads_deploy_doctor_artifacts():
    workflow = yaml.safe_load(BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy-doctor"]["steps"]

    upload_steps = [
        step for step in steps if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert len(upload_steps) == 1

    upload_step = upload_steps[0]
    assert upload_step["if"] == "${{ always() }}"
    assert upload_step["with"]["name"] == "deploy-doctor-artifacts"
    assert upload_step["with"]["if-no-files-found"] == "ignore"
    assert upload_step["with"]["retention-days"] == 7

    artifact_paths = upload_step["with"]["path"]
    assert ".cloud-agent-doctor/doctor.json" in artifact_paths
    assert ".cloud-agent-doctor/backend.out.log" in artifact_paths
    assert ".cloud-agent-doctor/backend.err.log" in artifact_paths


def test_browser_smoke_workflow_runs_observability_stack_with_fake_backend():
    workflow_text = BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["observability-stack-smoke"]

    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 30
    assert job["env"]["CI"] == "true"
    assert job["env"]["CLOUD_AGENT_SMOKE_FAKE_GRAPH"] == "true"
    assert job["env"]["DEEPSEEK_API_KEY"] == "ci-observability-placeholder"
    assert "sk-" not in workflow_text
    assert "".join(("secrets", ".")) not in workflow_text

    steps = job["steps"]
    assert any(step.get("uses") == "actions/setup-python@v5" for step in steps)
    setup_node_steps = [
        step for step in steps if step.get("uses") == "actions/setup-node@v4"
    ]
    assert len(setup_node_steps) == 1
    assert setup_node_steps[0]["with"]["node-version"] == "24"

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "python -X utf8 -m uvicorn app_main:app --host 127.0.0.1 --port 5000" in run_commands
    assert "docker compose -f ops/docker-compose.observability.yml up -d" in run_commands
    assert "python ops/observability_acceptance.py --run-chat-smoke" in run_commands
    assert "npm run smoke:grafana" in run_commands
    assert "docker compose -f ops/docker-compose.observability.yml down -v" in run_commands


def test_browser_smoke_workflow_uploads_observability_diagnostics_on_failure():
    workflow = yaml.safe_load(BROWSER_SMOKE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["observability-stack-smoke"]["steps"]
    upload_steps = [
        step for step in steps if step.get("uses") == "actions/upload-artifact@v4"
    ]

    assert len(upload_steps) == 1
    upload_step = upload_steps[0]
    assert upload_step["if"] == "${{ failure() }}"
    assert upload_step["with"]["name"] == "observability-stack-smoke-artifacts"
    assert upload_step["with"]["path"] == ".observability-ci/\n"
    assert upload_step["with"]["if-no-files-found"] == "ignore"
    assert upload_step["with"]["retention-days"] == 7


def test_supply_chain_workflow_blocks_high_risk_findings_for_both_python_projects():
    workflow_text = SUPPLY_CHAIN_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["supply-chain-gate"]

    assert set(_github_on(workflow)) == {"push", "pull_request", "schedule", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 30
    assert job["env"]["NPM_CONFIG_REGISTRY"] == "https://registry.npmjs.org"
    assert job["env"]["PYTHONUTF8"] == "1"
    assert "sk-" not in workflow_text
    assert "".join(("secrets", ".")) not in workflow_text

    steps = job["steps"]
    trivy_step = next(step for step in steps if step.get("id") == "trivy-config")
    assert trivy_step["uses"] == "aquasecurity/trivy-action@v0.36.0"
    assert trivy_step["with"]["scanners"] == "config"
    assert trivy_step["with"]["severity"] == "HIGH,CRITICAL"
    assert trivy_step["continue-on-error"] is True

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "pip-audit==2.10.1" in run_commands
    assert "cloud_agent/agent/requirements.txt" in run_commands
    assert "deep_research/requirements.txt" in run_commands
    assert "npm audit --registry=https://registry.npmjs.org --omit=dev --audit-level=high" in run_commands
    assert "steps.cloud-agent-pip-audit.outcome" in run_commands
    assert "steps.npm-audit.outcome" in run_commands
    assert "steps.deep-research-pip-audit.outcome" in run_commands


def test_supply_chain_workflow_retains_reports_for_blocking_and_report_only_audits():
    workflow = yaml.safe_load(SUPPLY_CHAIN_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["supply-chain-gate"]["steps"]
    upload_steps = [
        step for step in steps if step.get("uses") == "actions/upload-artifact@v4"
    ]

    assert len(upload_steps) == 1
    upload_step = upload_steps[0]
    assert upload_step["if"] == "${{ always() }}"
    assert upload_step["with"]["name"] == "supply-chain-audit-reports"
    assert upload_step["with"]["path"] == ".security-audit/"
    assert upload_step["with"]["if-no-files-found"] == "ignore"
    assert upload_step["with"]["retention-days"] == 14
