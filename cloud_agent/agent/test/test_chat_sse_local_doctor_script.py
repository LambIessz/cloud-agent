from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "chat_sse_local_doctor.ps1"
README_PATH = PROJECT_ROOT / "README.md"


def test_local_doctor_script_checks_ports_http_proxy_env_and_logs():
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    required_snippets = {
        "param(",
        "$BackendUrl = 'http://127.0.0.1:5000'",
        "$FrontendUrl = 'http://127.0.0.1:5173'",
        "[switch]$Strict",
        "Get-NetTCPConnection",
        "Get-Process",
        "Invoke-WebRequest",
        "/readyz",
        "/api/metrics",
        "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED",
        "CLOUD_AGENT_VECTOR_SEARCH_ENABLED",
        "CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED",
        "CLOUD_AGENT_CORS_ORIGINS",
        ".codex-run",
        "Get-Content",
        "-Tail $TailLines",
        "exit 1",
    }

    for snippet in required_snippets:
        assert snippet in script


def test_local_doctor_script_is_documented_next_to_local_smoke_command():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "ops/chat_sse_local_doctor.ps1" in readme
    assert "ops/chat_sse_local_smoke.ps1" in readme
