from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "cloud_agent_compose_smoke.ps1"
README_PATH = PROJECT_ROOT / "README.md"
RUNBOOK_PATH = PROJECT_ROOT / "ops" / "local_dev_runbook.md"


def test_compose_deploy_smoke_script_validates_compose_starts_app_and_runs_doctor():
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    required_snippets = {
        "param(",
        "$EnvFile = 'ops/cloud_agent.env'",
        "$ComposeFile = 'ops/docker-compose.cloud-agent.yml'",
        "$BackendUrl = 'http://127.0.0.1:5000'",
        "[switch]$KeepRunning",
        "docker compose",
        "[string[]]$ComposeArgs",
        "@ComposeArgs",
        "config",
        "config', '--quiet",
        "up -d --build",
        "/readyz",
        "python ops/cloud_agent_doctor.py",
        "--env-file",
        "--base-url",
        ".codex-run",
        "compose-doctor.json",
        "compose-cloud-agent.log",
        "docker compose logs --no-color",
        "docker compose down",
        "$PreExistingServiceIds",
        "Get-ComposeServiceId",
        "Stop-NewComposeServices",
        "preserving pre-existing compose services",
        "stopping only smoke-started services",
        "finally",
    }

    for snippet in required_snippets:
        assert snippet in script
    assert "param([string[]]$Args)" not in script


def test_compose_deploy_smoke_script_is_documented():
    readme = README_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "ops/cloud_agent_compose_smoke.ps1" in readme
    assert "ops/cloud_agent_compose_smoke.ps1" in runbook
    assert "compose-doctor.json" in runbook
    assert "compose-cloud-agent.log" in runbook
    assert "pre-existing compose services" in runbook
