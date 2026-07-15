import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = PROJECT_ROOT / "ops" / "external_dependency_readonly_smoke.py"
README_PATH = PROJECT_ROOT / "README.md"
RUNBOOK_PATH = PROJECT_ROOT / "ops" / "local_dev_runbook.md"
HANDOFF_PATH = PROJECT_ROOT / "API_SWITCH_HANDOFF.md"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("external_dependency_readonly_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _mcp_config(tmp_path: Path) -> Path:
    cwd = tmp_path / "agent"
    cwd.mkdir()
    path = tmp_path / "mcp_servers.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cloud_billing": {
                        "command": "python",
                        "args": ["-m", "mcp_servers.cloud_platform_server"],
                        "cwd": str(cwd),
                        "transport": "stdio",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_external_readonly_smoke_passes_with_injected_dependency_probes(tmp_path):
    smoke = _load_smoke()
    secret = "real-secret-value-that-must-not-print"
    mcp_config = _mcp_config(tmp_path)
    called = {
        "llm": False,
        "redis": False,
        "milvus": False,
        "mysql": False,
        "jwks": [],
    }

    def fake_llm_post(env, api_key, timeout):
        called["llm"] = True
        assert api_key == secret
        assert env["MODEL"] == "new-company-model"
        return 200, {"choices": [{"message": {"content": "OK"}}]}

    def fake_redis_ping(redis_url, timeout):
        called["redis"] = True
        assert redis_url == "redis://127.0.0.1:6379"
        return "PING ok"

    def fake_milvus_list(*, host, port, token, timeout):
        called["milvus"] = True
        assert host == "127.0.0.1"
        assert port == 19530
        return ["memory"]

    def fake_mysql_ping(env, password, timeout):
        called["mysql"] = True
        assert password == secret
        return "SELECT 1 ok"

    def fake_fetch(url, timeout):
        called["jwks"].append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return 200, {"jwks_uri": "https://issuer.example/jwks"}
        return 200, {"keys": [{"kid": "key-1"}]}

    report = smoke.build_report(
        env={
            "DEEPSEEK_API_KEY": secret,
            "BASE_URL": "https://api.example.com/v1",
            "MODEL": "new-company-model",
            "REDIS_URL": "redis://127.0.0.1:6379",
            "CLOUD_AGENT_MILVUS_MODE": "remote",
            "MILVUS_HOST": "127.0.0.1",
            "MILVUS_PORT": "19530",
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3307",
            "MYSQL_USER": "cloud_agent",
            "MYSQL_PASSWORD": secret,
            "MYSQL_DATABASE": "cloud_platform",
            "CLOUD_AGENT_AUTH_MODE": "production",
            "CLOUD_AGENT_AUTH_STRATEGY": "oidc",
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL": "https://issuer.example/.well-known/openid-configuration",
        },
        mcp_config_path=mcp_config,
        llm_post=fake_llm_post,
        redis_ping_func=fake_redis_ping,
        milvus_list_func=fake_milvus_list,
        mysql_ping_func=fake_mysql_ping,
        fetch_json_func=fake_fetch,
    )

    assert report.status == "ready"
    assert all(called.values())
    checks = {check.name: check.status for check in report.checks}
    assert checks["llm_config"] == smoke.PASS
    assert checks["llm_chat_completion"] == smoke.PASS
    assert checks["redis"] == smoke.PASS
    assert checks["milvus"] == smoke.PASS
    assert checks["mysql_mcp"] == smoke.PASS
    assert checks["mcp_config"] == smoke.PASS
    assert checks["auth_jwks"] == smoke.PASS

    rendered = smoke.format_text(report) + smoke.format_json(report)
    assert secret not in rendered
    assert "OK" not in rendered


def test_external_readonly_smoke_fails_core_llm_but_blocks_unconfigured_mysql(tmp_path):
    smoke = _load_smoke()

    report = smoke.build_report(
        env={
            "DEEPSEEK_API_KEY": "ci-placeholder",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
            "CLOUD_AGENT_AUTH_MODE": "local",
        },
        mcp_config_path=_mcp_config(tmp_path),
        redis_ping_func=lambda redis_url, timeout: "PING ok",
        llm_post=lambda env, api_key, timeout: (200, {"choices": []}),
    )

    assert report.status == "failed"
    assert report.exit_code(strict=False) == 1
    assert any(check.name == "llm_config" and check.status == smoke.FAIL for check in report.checks)
    assert any(check.name == "mysql_mcp" and check.status == smoke.BLOCKED for check in report.checks)
    assert "ci-placeholder" not in smoke.format_text(report)


def test_external_readonly_smoke_defaults_to_milvus_lite_probe(tmp_path):
    smoke = _load_smoke()

    report = smoke.build_report(
        env={
            "DEEPSEEK_API_KEY": "real-key",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "true",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "true",
            "CLOUD_AGENT_AUTH_MODE": "local",
        },
        skip_llm_call=True,
        mcp_config_path=_mcp_config(tmp_path),
        redis_ping_func=lambda redis_url, timeout: "PING ok",
        milvus_lite_func=lambda env: "Milvus Lite packages available",
    )

    assert any(
        check.name == "milvus"
        and check.status == smoke.PASS
        and "Milvus Lite" in check.detail
        for check in report.checks
    )


def test_external_readonly_smoke_blocks_configured_mysql_when_password_is_absent(tmp_path):
    smoke = _load_smoke()

    report = smoke.build_report(
        env={
            "DEEPSEEK_API_KEY": "real-key",
            "MYSQL_HOST": "127.0.0.1",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
            "CLOUD_AGENT_AUTH_MODE": "local",
        },
        skip_llm_call=True,
        mcp_config_path=_mcp_config(tmp_path),
        redis_ping_func=lambda redis_url, timeout: "PING ok",
    )

    assert report.status == "incomplete"
    assert any(
        check.name == "mysql_mcp"
        and check.status == smoke.BLOCKED
        and "MYSQL_PASSWORD" in check.detail
        for check in report.checks
    )


def test_external_readonly_smoke_loads_env_and_secret_files_without_leaking(tmp_path):
    smoke = _load_smoke()
    secret_file = tmp_path / "api-key.txt"
    secret_file.write_text("file-secret-that-must-not-print", encoding="utf-8")
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text(
        "\n".join(
            [
                f"DEEPSEEK_API_KEY_FILE={secret_file}",
                "BASE_URL=https://api.example.com/v1",
                "MODEL=company-chat",
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED=false",
                "CLOUD_AGENT_VECTOR_SEARCH_ENABLED=false",
                "CLOUD_AGENT_AUTH_MODE=local",
            ]
        ),
        encoding="utf-8",
    )

    env = smoke.merge_env(env_file, process_env={})
    report = smoke.build_report(
        env=env,
        skip_llm_call=True,
        mcp_config_path=_mcp_config(tmp_path),
        redis_ping_func=lambda redis_url, timeout: "PING ok",
    )

    assert any(check.name == "llm_config" and check.status == smoke.PASS for check in report.checks)
    assert any(check.name == "llm_chat_completion" and check.status == smoke.BLOCKED for check in report.checks)
    rendered = smoke.format_json(report)
    assert "file-secret-that-must-not-print" not in rendered
    assert "DEEPSEEK_API_KEY_FILE" in rendered


def test_external_readonly_smoke_writes_json_artifact(tmp_path):
    smoke = _load_smoke()
    artifact = tmp_path / "external-readonly-smoke.json"
    report = smoke.SmokeReport([smoke.CheckResult("llm_config", smoke.PASS, "configured")])

    smoke.write_artifact(artifact, report)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["checks"][0]["name"] == "llm_config"


def test_external_readonly_smoke_is_documented():
    readme = README_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    handoff = HANDOFF_PATH.read_text(encoding="utf-8")

    for text in (readme, runbook, handoff):
        assert "ops/external_dependency_readonly_smoke.py" in text
    assert "external-readonly-smoke.json" in runbook
    assert "--skip-llm-call" in runbook
