import importlib.util
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = PROJECT_ROOT / "ops" / "mcp_billing_readonly_smoke.py"
RELEASE_GATE_PATH = PROJECT_ROOT / "ops" / "release_gate.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("mcp_billing_readonly_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mcp_config(tmp_path: Path) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"mcpServers": {"cloud_billing": {"transport": "stdio"}}}), encoding="utf-8")
    return path


def test_mcp_billing_smoke_passes_with_injected_registry_and_tool_calls(tmp_path):
    smoke = _load_smoke()
    secret = "mysql-secret-that-must-not-print"
    calls = []

    def fake_registry_probe(config_path, timeout):
        assert config_path == _mcp_config_path
        return ["query_user_orders", "query_user_instances"]

    def fake_billing_call(tool_name, env, user_id, limit, timeout):
        calls.append((tool_name, user_id, limit))
        assert env["MYSQL_PASSWORD"] == secret
        if tool_name == "query_user_orders":
            return {
                "status": "success",
                "data": [{"order_id": "order-secret-001", "amount": 9.9}],
            }
        return {"status": "success", "message": "no instance rows"}

    _mcp_config_path = _mcp_config(tmp_path)
    report = smoke.build_report(
        env={
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3307",
            "MYSQL_USER": "cloud_agent",
            "MYSQL_PASSWORD": secret,
            "MYSQL_DATABASE": "cloud_platform",
        },
        mcp_config_path=_mcp_config_path,
        user_id="smoke_billing_user",
        limit=2,
        registry_probe_func=fake_registry_probe,
        billing_tool_call_func=fake_billing_call,
    )

    assert report.status == "ready"
    assert calls == [
        ("query_user_orders", "smoke_billing_user", 2),
        ("query_user_instances", "smoke_billing_user", 2),
    ]
    checks = {check.name: check.status for check in report.checks}
    assert checks["mcp_registry_billing_tools"] == smoke.PASS
    assert checks["mysql_billing_config"] == smoke.PASS
    assert checks["query_user_orders"] == smoke.PASS
    assert checks["query_user_instances"] == smoke.PASS

    rendered = smoke.format_text(report) + smoke.format_json(report)
    assert secret not in rendered
    assert "order-secret-001" not in rendered


def test_mcp_billing_smoke_blocks_tool_calls_when_mysql_is_unconfigured(tmp_path):
    smoke = _load_smoke()
    tool_calls = []

    report = smoke.build_report(
        env={},
        mcp_config_path=_mcp_config(tmp_path),
        registry_probe_func=lambda config_path, timeout: ["query_user_orders", "query_user_instances"],
        billing_tool_call_func=lambda *args: tool_calls.append(args) or {"status": "success"},
    )

    assert report.status == "incomplete"
    assert tool_calls == []
    assert any(
        check.name == "mysql_billing_config" and check.status == smoke.BLOCKED
        for check in report.checks
    )
    assert all(
        check.status == smoke.BLOCKED
        for check in report.checks
        if check.name in {"query_user_orders", "query_user_instances"}
    )


def test_mcp_billing_smoke_fails_when_billing_allowlist_is_missing_a_tool(tmp_path):
    smoke = _load_smoke()

    report = smoke.build_report(
        env={
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PASSWORD": "real-mysql-password",
        },
        mcp_config_path=_mcp_config(tmp_path),
        registry_probe_func=lambda config_path, timeout: ["query_user_orders"],
        billing_tool_call_func=lambda *args: {"status": "success"},
    )

    assert report.status == "failed"
    assert any(
        check.name == "mcp_registry_billing_tools"
        and check.status == smoke.FAIL
        and "query_user_instances" in check.detail
        for check in report.checks
    )


def test_mcp_billing_smoke_degrades_tool_errors_without_leaking_secrets(tmp_path):
    smoke = _load_smoke()
    secret = "real-mysql-password-that-must-not-leak"

    def fake_billing_call(tool_name, env, user_id, limit, timeout):
        if tool_name == "query_user_orders":
            return {"status": "error", "error_type": "OperationalError", "message": f"password={secret}"}
        raise RuntimeError(f"backend password={secret}")

    report = smoke.build_report(
        env={
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PASSWORD": secret,
        },
        mcp_config_path=_mcp_config(tmp_path),
        registry_probe_func=lambda config_path, timeout: ["query_user_orders", "query_user_instances"],
        billing_tool_call_func=fake_billing_call,
    )

    assert report.status == "degraded"
    rendered = smoke.format_text(report) + smoke.format_json(report)
    assert secret not in rendered
    assert "OperationalError" in rendered
    assert "RuntimeError" in rendered


def test_mcp_billing_smoke_loads_env_and_writes_artifact(tmp_path):
    smoke = _load_smoke()
    secret_file = tmp_path / "mysql-password.txt"
    secret_file.write_text("file-mysql-secret-that-must-not-print", encoding="utf-8")
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text(
        "\n".join(
            [
                "MYSQL_HOST=127.0.0.1",
                f"MYSQL_PASSWORD_FILE={secret_file}",
            ]
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "mcp-billing-smoke.json"

    env = smoke.merge_env(env_file, process_env={})
    report = smoke.build_report(
        env=env,
        mcp_config_path=_mcp_config(tmp_path),
        registry_probe_func=lambda config_path, timeout: ["query_user_orders", "query_user_instances"],
        billing_tool_call_func=lambda *args: {"status": "success", "data": []},
    )
    smoke.write_artifact(artifact, report)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert "file-mysql-secret-that-must-not-print" not in artifact.read_text(encoding="utf-8")
