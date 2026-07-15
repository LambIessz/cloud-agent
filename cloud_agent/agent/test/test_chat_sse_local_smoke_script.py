from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "chat_sse_local_smoke.ps1"
README_PATH = PROJECT_ROOT / "README.md"


def test_local_sse_smoke_script_wraps_backend_frontend_and_smoke_runner():
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    required_snippets = {
        "param(",
        "$BackendUrl = 'http://127.0.0.1:5000'",
        "$FrontendUrl = 'http://127.0.0.1:5173'",
        "[switch]$KeepRunning",
        "Get-NetTCPConnection",
        "Start-Process",
        "-WindowStyle Hidden",
        "Start-Backend -Port $BackendPort",
        "Start-Frontend -Port $FrontendPort",
        "--port $Port",
        "--strictPort",
        "chat_sse_smoke.py",
        "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED",
        "CLOUD_AGENT_VECTOR_SEARCH_ENABLED",
        "CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED",
        ".codex-run",
        "finally",
        "Stop-Process",
    }

    for snippet in required_snippets:
        assert snippet in script


def test_local_sse_smoke_script_documents_one_command_usage():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "ops/chat_sse_local_smoke.ps1" in readme
    assert "-KeepRunning" in readme
