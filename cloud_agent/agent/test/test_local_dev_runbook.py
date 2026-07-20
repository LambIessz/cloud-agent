from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK_PATH = PROJECT_ROOT / "ops" / "local_dev_runbook.md"
RELEASE_CHECKLIST_PATH = PROJECT_ROOT / "ops" / "release_checklist.md"
README_PATH = PROJECT_ROOT / "README.md"


def test_local_dev_runbook_covers_core_local_workflows():
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    required_terms = {
        "powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1",
        "python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000",
        "python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000",
        "python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000 --json",
        "powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1",
        "python ops/chat_sse_smoke.py",
        "python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json",
        "python ops/release_evidence.py --json",
        "python ops/release_evidence.py --require-observability --json",
        "npm run smoke:browser",
        "npm run smoke:browser:real-backend",
        "CLOUD_AGENT_SMOKE_FAKE_GRAPH=true",
        "playwright-report",
        "playwright-report-real-backend",
        ".cloud-agent-doctor/doctor.json",
        "deploy-doctor-artifacts",
        "http://127.0.0.1:5000/readyz",
        "http://127.0.0.1:5173",
        "npm run dev",
        ".codex-run",
        ".codex-run/mcp-billing-smoke.json",
        ".codex-run/release-evidence.json",
        ".codex-run/release-evidence.md",
        "test_all.bat",
        "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED=false",
        "CLOUD_AGENT_AUTH_MODE",
        "CLOUD_AGENT_AUTH_STRATEGY",
        "CLOUD_AGENT_VECTOR_SEARCH_ENABLED=false",
        "CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED=false",
        "RequestsDependencyWarning",
        "Vite chunk size",
        "CRLF",
        "端口冲突",
        "降级",
    }

    for term in required_terms:
        assert term in runbook


def test_readme_links_to_local_dev_runbook():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "ops/local_dev_runbook.md" in readme
    assert "ops/release_checklist.md" in readme
    assert "ops/cloud_agent_doctor.py" in readme
    assert "--env-file ops/cloud_agent.env" in readme
    assert "npm run smoke:browser" in readme
    assert "npm run smoke:browser:real-backend" in readme


def test_release_checklist_covers_release_gate_order_and_artifacts():
    checklist = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    required_terms = {
        "docker compose --env-file ops/cloud_agent.env -f ops/docker-compose.cloud-agent.yml config",
        "python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000 --json",
        "python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000",
        "npm run smoke:browser",
        "npm run smoke:browser:real-backend",
        "python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --json",
        "python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --skip-llm-call --json",
        "python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json",
        "python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env --json",
        "python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json",
        "python ops/release_gate.py --env-file ops/cloud_agent.env --backend-url http://127.0.0.1:5000 --strict",
        "python ops/release_evidence.py --json",
        "python ops/release_evidence.py --require-observability --json",
        "powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1",
        "docker compose -f ops/docker-compose.observability.yml up -d",
        "bash ops/ubuntu_ci_acceptance.sh",
        "ops/observability_checklist.md",
        ".\\test_all.bat",
        "git diff --check",
        "python ops/secret_scan.py",
        ".codex-run/external-readonly-smoke.json",
        ".codex-run/mcp-billing-smoke.json",
        ".codex-run/memory-e2e-smoke.json",
        ".codex-run/real-idp-smoke.json",
        ".codex-run/release-gate.json",
        ".codex-run/release-evidence.json",
        ".codex-run/release-evidence.md",
        ".codex-run/compose-doctor.json",
        "cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json",
        "cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html",
        ".acceptance/<timestamp>/summary.tsv",
        "query_user_orders",
        "query_user_instances",
        "Go / No-Go",
        "rollback",
        "No real secrets or business data",
    }

    for term in required_terms:
        assert term in checklist
    assert "ops/release_checklist.md" in runbook


def test_long_observability_window_is_optional_for_local_validation():
    readme = README_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    checklist = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")

    for text in (readme, runbook, checklist):
        normalized = " ".join(text.split())
        assert "local release blocker" in normalized
        assert "production-like host" in normalized
