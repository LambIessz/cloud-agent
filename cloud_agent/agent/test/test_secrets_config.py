import os
import re
import subprocess
import sys
from pathlib import Path

import yaml


AGENT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from config.secrets import load_file_secrets


SECRET_ENV_NAMES = {
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "NEO4J_PASSWORD",
    "MILVUS_API_KEY",
    "OPENWEATHER_API_KEY",
    "CLOUD_AGENT_AUTH_JWT_SECRET",
}
APP_SECRET_ENV_NAMES = SECRET_ENV_NAMES - {"MYSQL_ROOT_PASSWORD"}


def test_load_file_secrets_sets_env_without_printing_value(tmp_path, monkeypatch, capsys):
    secret_file = tmp_path / "deepseek"
    secret_file.write_text("secret-value\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY_FILE", str(secret_file))

    loaded = load_file_secrets(["DEEPSEEK_API_KEY"])

    assert loaded == ["DEEPSEEK_API_KEY"]
    assert os.environ["DEEPSEEK_API_KEY"] == "secret-value"
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "secret-value" not in output
    assert str(secret_file) not in output


def test_load_file_secrets_keeps_direct_env_value(tmp_path, monkeypatch):
    secret_file = tmp_path / "mysql"
    secret_file.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("MYSQL_PASSWORD", "direct-secret")
    monkeypatch.setenv("MYSQL_PASSWORD_FILE", str(secret_file))

    loaded = load_file_secrets(["MYSQL_PASSWORD"])

    assert loaded == []
    assert os.environ["MYSQL_PASSWORD"] == "direct-secret"


def test_load_file_secrets_ignores_missing_file_without_error(monkeypatch, capsys):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("NEO4J_PASSWORD_FILE", "missing-secret-file")

    loaded = load_file_secrets(["NEO4J_PASSWORD"])

    assert loaded == []
    assert os.getenv("NEO4J_PASSWORD") is None
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "missing-secret-file" not in output


def test_gitignore_and_env_example_keep_local_secrets_out_of_repo():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / "ops" / "cloud_agent.env.example").read_text(encoding="utf-8")

    for pattern in (".env", "*.env", "cloud_agent/agent/.env", "ops/cloud_agent.env"):
        assert pattern in gitignore
    for pattern in ("*.pem", "*.tgz", "*.db", "deep_research/app/data/"):
        assert pattern in gitignore
    assert "!ops/*.env.example" in gitignore

    for name in SECRET_ENV_NAMES:
        assert f"{name}=" in env_example
        assert f"{name}_FILE=" in env_example

    forbidden_values = ("sk-", "4AMDiDiWei", "YOUR_MYSQL_PASSWORD", "YOUR_NEO4J_PASSWORD")
    for value in forbidden_values:
        assert value not in env_example


def test_dockerignore_keeps_secret_and_runtime_artifacts_out_of_images():
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in ("*.pem", "*.tgz", "*.db", "deep_research/app/data/"):
        assert pattern in dockerignore


def test_bocha_api_smoke_uses_environment_secret_only():
    smoke_test = (PROJECT_ROOT / "deep_research" / "app" / "test" / "bocha_api_test.py").read_text(
        encoding="utf-8"
    )

    assert 'os.getenv("BOCHA_API_KEY"' in smoke_test
    assert "sk-" not in smoke_test


def test_ubuntu_acceptance_requires_deployment_secret_without_hardcoded_fallback():
    script = (PROJECT_ROOT / "ops" / "ubuntu_ci_acceptance.sh").read_text(encoding="utf-8")

    assert "DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE must be set" in script
    assert not re.search(
        r"(?m)^\s*export\s+DEEPSEEK_API_KEY\s*=\s*[^$\s#]",
        script,
    )


def test_api_switch_handoff_does_not_embed_or_assign_api_keys():
    handoff = (PROJECT_ROOT / "API_SWITCH_HANDOFF.md").read_text(encoding="utf-8")

    assert "sk-" not in handoff
    assert "DEEPSEEK_API_KEY=" not in handoff
    assert "DEEPSEEK_API_KEY:" not in handoff


def test_runtime_memory_database_is_not_tracked_by_git():
    result = subprocess.run(
        ["git", "ls-files", "deep_research/app/data/memory.db"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == ""


def test_compose_exposes_file_secret_envs_without_plaintext_values():
    compose = yaml.safe_load(
        (PROJECT_ROOT / "ops" / "docker-compose.cloud-agent.yml").read_text(encoding="utf-8")
    )
    app_env = compose["services"]["cloud_agent"]["environment"]

    for name in APP_SECRET_ENV_NAMES:
        assert app_env[name] == f"${{{name}:-}}"
        assert app_env[f"{name}_FILE"] == f"${{{name}_FILE:-}}"
