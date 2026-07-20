import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCTOR_PATH = PROJECT_ROOT / "ops" / "cloud_agent_doctor.py"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("cloud_agent_doctor", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _healthy_fetch(url):
    if url.endswith("/api/metrics"):
        return 200, ""
    return 200, {"status": "ready" if url.endswith("/readyz") else "ok"}


def _base_env(**overrides):
    env = {
        "DEEPSEEK_API_KEY": "doctor-key",
        "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED": "false",
        "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
        "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
    }
    env.update(overrides)
    return env


def test_deploy_doctor_marks_optional_dependency_outages_as_degraded(tmp_path):
    doctor = _load_doctor()
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    env = {
        "DEEPSEEK_API_KEY": "key-real-value-that-must-not-print",
        "CLOUD_AGENT_CORS_ORIGINS": "https://console.example.com",
        "REDIS_URL": "redis://127.0.0.1:6379",
        "CLOUD_AGENT_MILVUS_MODE": "remote",
        "MILVUS_HOST": "127.0.0.1",
        "MILVUS_PORT": "19530",
    }
    http_payloads = {
        "http://127.0.0.1:5000/healthz": (200, {"status": "ok"}),
        "http://127.0.0.1:5000/readyz": (200, {"status": "ready"}),
        "http://127.0.0.1:5000/api/metrics": (
            200,
            "# HELP cloud_agent_request_total requests\ncloud_agent_request_total 1\n",
        ),
    }

    report = doctor.build_report(
        env=env,
        base_url="http://127.0.0.1:5000",
        frontend_origin="https://console.example.com",
        mcp_config_path=mcp_config,
        fetch=lambda url: http_payloads[url],
        can_connect=lambda host, port, timeout: False,
    )

    assert report.status == "degraded"
    assert report.exit_code(strict=False) == 0
    assert report.exit_code(strict=True) == 1
    assert any(check.name == "redis" and check.status == "degraded" for check in report.checks)
    assert any(check.name == "milvus" and check.status == "degraded" for check in report.checks)
    assert "key-real-value-that-must-not-print" not in doctor.format_text(report)


def test_deploy_doctor_accepts_milvus_lite_mode_without_remote_port(tmp_path, monkeypatch):
    doctor = _load_doctor()
    for module_name in ("pymilvus", "langchain_milvus", "langchain_huggingface"):
        monkeypatch.setitem(sys.modules, module_name, SimpleNamespace())
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    report = doctor.build_report(
        env=_base_env(
            CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED="true",
            CLOUD_AGENT_VECTOR_SEARCH_ENABLED="true",
            CLOUD_AGENT_MILVUS_MODE="lite",
        ),
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=mcp_config,
        fetch=_healthy_fetch,
        can_connect=lambda host, port, timeout: False,
    )

    assert report.status == "ready"
    assert any(
        check.name == "milvus"
        and check.status == "pass"
        and "Milvus Lite" in check.detail
        for check in report.checks
    )


def test_deploy_doctor_fails_missing_secret_and_wildcard_cors(tmp_path):
    doctor = _load_doctor()
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    report = doctor.build_report(
        env={"CLOUD_AGENT_CORS_ORIGINS": "*"},
        base_url="http://127.0.0.1:5000",
        frontend_origin="https://console.example.com",
        mcp_config_path=mcp_config,
        fetch=lambda url: (200, {"status": "ready"} if url.endswith("/readyz") else {"status": "ok"}),
        can_connect=lambda host, port, timeout: True,
    )

    rendered = doctor.format_text(report)

    assert report.status == "failed"
    assert report.exit_code(strict=False) == 1
    assert "DEEPSEEK_API_KEY" in rendered
    assert "wildcard" in rendered
    assert "CLOUD_AGENT_CORS_ORIGINS=*" not in rendered


def test_deploy_doctor_json_output_is_machine_readable(tmp_path):
    doctor = _load_doctor()
    mcp_config = tmp_path / "mcp_servers.json"
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("secret-value", encoding="utf-8")
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    report = doctor.build_report(
        env={
            "DEEPSEEK_API_KEY_FILE": str(secret_file),
            "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED": "false",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
        },
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=mcp_config,
        fetch=lambda url: (
            (200, "# HELP cloud_agent_request_total requests\n")
            if url.endswith("/api/metrics")
            else (200, {"status": "ready" if url.endswith("/readyz") else "ok"})
        ),
        can_connect=lambda host, port, timeout: True,
    )

    payload = json.loads(doctor.format_json(report))

    assert payload["status"] == "ready"
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["degraded"] == 0
    assert {check["name"] for check in payload["checks"]} >= {
        "llm_secret",
        "healthz",
        "readyz",
        "metrics",
        "mcp_config",
    }


def test_deploy_doctor_fails_when_secret_file_is_missing(tmp_path):
    doctor = _load_doctor()
    mcp_config = tmp_path / "mcp_servers.json"
    missing_secret = tmp_path / "missing-secret.txt"
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    report = doctor.build_report(
        env={
            "DEEPSEEK_API_KEY_FILE": str(missing_secret),
            "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED": "false",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
        },
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=mcp_config,
        fetch=lambda url: (
            (200, "# HELP cloud_agent_request_total requests\n")
            if url.endswith("/api/metrics")
            else (200, {"status": "ready" if url.endswith("/readyz") else "ok"})
        ),
        can_connect=lambda host, port, timeout: True,
    )

    assert report.status == "failed"
    assert any(
        check.name == "llm_secret" and check.status == "fail"
        for check in report.checks
    )
    assert str(missing_secret) in doctor.format_text(report)


def test_deploy_doctor_accepts_empty_metrics_from_fresh_backend(tmp_path):
    doctor = _load_doctor()
    secret_file = tmp_path / "secret.txt"
    metrics_token_file = tmp_path / "metrics-token.txt"
    mcp_config = tmp_path / "mcp_servers.json"
    secret_file.write_text("secret-value", encoding="utf-8")
    metrics_token_file.write_text("metrics-secret", encoding="utf-8")
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    report = doctor.build_report(
        env={
            "DEEPSEEK_API_KEY_FILE": str(secret_file),
            "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED": "false",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
        },
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=mcp_config,
        fetch=lambda url: (
            (200, "")
            if url.endswith("/api/metrics")
            else (200, {"status": "ready" if url.endswith("/readyz") else "ok"})
        ),
        can_connect=lambda host, port, timeout: True,
    )

    assert report.status == "ready"
    assert any(
        check.name == "metrics"
        and check.status == "pass"
        and "no samples yet" in check.detail
        for check in report.checks
    )


def test_deploy_doctor_uses_metrics_token_when_fetching_metrics(tmp_path, monkeypatch):
    doctor = _load_doctor()
    secret_file = tmp_path / "secret.txt"
    metrics_token_file = tmp_path / "metrics-token.txt"
    mcp_config = tmp_path / "mcp_servers.json"
    secret_file.write_text("secret-value", encoding="utf-8")
    metrics_token_file.write_text("metrics-secret", encoding="utf-8")
    mcp_config.write_text(
        json.dumps({"mcpServers": {"billing": {"command": "python"}}}),
        encoding="utf-8",
    )

    captured_requests = []

    def fake_fetch_url(url, timeout=5.0, headers=None):
        captured_requests.append((url, headers))
        if url.endswith("/api/metrics"):
            return 200, ""
        return 200, {"status": "ready" if url.endswith("/readyz") else "ok"}

    monkeypatch.setattr(doctor, "fetch_url", fake_fetch_url)

    report = doctor.build_report(
        env={
            "DEEPSEEK_API_KEY_FILE": str(secret_file),
            "CLOUD_AGENT_METRICS_TOKEN_FILE": str(metrics_token_file),
            "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED": "false",
            "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false",
            "CLOUD_AGENT_VECTOR_SEARCH_ENABLED": "false",
        },
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=mcp_config,
        can_connect=lambda host, port, timeout: True,
    )

    assert report.status == "ready"
    metrics_request = next(url_headers for url_headers in captured_requests if url_headers[0].endswith("/api/metrics"))
    assert metrics_request[1] == {"Authorization": "Bearer metrics-secret"}


def test_deploy_doctor_runtime_artifacts_are_ignored():
    gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert ".cloud-agent-doctor/" in gitignore


def test_deploy_doctor_loads_env_file_without_leaking_values(tmp_path):
    doctor = _load_doctor()
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text(
        "\n".join(
            [
                "# deployment env",
                "DEEPSEEK_API_KEY=env-file-secret-value",
                'CLOUD_AGENT_CORS_ORIGINS="https://console.example.com"',
                "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED=false",
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED=false",
                "CLOUD_AGENT_VECTOR_SEARCH_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    loaded = doctor.load_env_file(env_file)

    assert loaded["DEEPSEEK_API_KEY"] == "env-file-secret-value"
    assert loaded["CLOUD_AGENT_CORS_ORIGINS"] == "https://console.example.com"
    assert "env-file-secret-value" not in doctor.format_text(
        doctor.build_report(
            env=loaded,
            base_url="http://127.0.0.1:5000",
            frontend_origin="https://console.example.com",
            mcp_config_path=tmp_path / "missing-mcp.json",
            fetch=lambda url: (
                (200, "")
                if url.endswith("/api/metrics")
                else (200, {"status": "ready" if url.endswith("/readyz") else "ok"})
            ),
            can_connect=lambda host, port, timeout: True,
        )
    )


def test_deploy_doctor_merges_env_file_with_process_env_precedence(tmp_path):
    doctor = _load_doctor()
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=from-file",
                "CLOUD_AGENT_CORS_ORIGINS=https://from-file.example.com",
            ]
        ),
        encoding="utf-8",
    )

    merged = doctor.merge_env(env_file, {"CLOUD_AGENT_CORS_ORIGINS": "https://from-env.example.com"})

    assert merged["DEEPSEEK_API_KEY"] == "from-file"
    assert merged["CLOUD_AGENT_CORS_ORIGINS"] == "https://from-env.example.com"


def test_deploy_doctor_empty_process_env_does_not_override_env_file(tmp_path):
    doctor = _load_doctor()
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")

    merged = doctor.merge_env(env_file, {"DEEPSEEK_API_KEY": ""})

    assert merged["DEEPSEEK_API_KEY"] == "from-file"


def test_deploy_doctor_loads_env_file_with_utf8_bom(tmp_path):
    doctor = _load_doctor()
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text("\ufeffDEEPSEEK_API_KEY=from-bom-file\n", encoding="utf-8")

    loaded = doctor.load_env_file(env_file)

    assert loaded["DEEPSEEK_API_KEY"] == "from-bom-file"


def test_deploy_doctor_fails_production_jwt_without_secret(tmp_path):
    doctor = _load_doctor()

    report = doctor.build_report(
        env=_base_env(
            CLOUD_AGENT_AUTH_MODE="production",
            CLOUD_AGENT_AUTH_STRATEGY="jwt",
        ),
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=tmp_path / "missing-mcp.json",
        fetch=_healthy_fetch,
        can_connect=lambda host, port, timeout: True,
    )

    assert report.status == "failed"
    assert any(
        check.name == "auth_config"
        and check.status == "fail"
        and "CLOUD_AGENT_AUTH_JWT_SECRET" in check.detail
        for check in report.checks
    )


def test_deploy_doctor_accepts_production_jwt_secret_file_without_leaking_value(tmp_path):
    doctor = _load_doctor()
    secret_file = tmp_path / "jwt-secret.txt"
    secret_file.write_text("jwt-secret-value", encoding="utf-8")

    report = doctor.build_report(
        env=_base_env(
            CLOUD_AGENT_AUTH_MODE="production",
            CLOUD_AGENT_AUTH_STRATEGY="jwt",
            CLOUD_AGENT_AUTH_JWT_SECRET_FILE=str(secret_file),
        ),
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=tmp_path / "missing-mcp.json",
        fetch=_healthy_fetch,
        can_connect=lambda host, port, timeout: True,
    )

    rendered = doctor.format_text(report)

    assert any(
        check.name == "auth_config" and check.status == "pass"
        for check in report.checks
    )
    assert "jwt-secret-value" not in rendered


def test_deploy_doctor_fails_production_oidc_without_jwks_or_discovery(tmp_path):
    doctor = _load_doctor()

    report = doctor.build_report(
        env=_base_env(
            CLOUD_AGENT_AUTH_MODE="production",
            CLOUD_AGENT_AUTH_STRATEGY="oidc",
        ),
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=tmp_path / "missing-mcp.json",
        fetch=_healthy_fetch,
        can_connect=lambda host, port, timeout: True,
    )

    assert report.status == "failed"
    assert any(
        check.name == "auth_config"
        and check.status == "fail"
        and "CLOUD_AGENT_AUTH_JWKS_URL" in check.detail
        for check in report.checks
    )


def test_deploy_doctor_accepts_production_gateway_defaults(tmp_path):
    doctor = _load_doctor()

    report = doctor.build_report(
        env=_base_env(
            CLOUD_AGENT_AUTH_MODE="production",
            CLOUD_AGENT_AUTH_STRATEGY="gateway",
        ),
        base_url="http://127.0.0.1:5000",
        frontend_origin=None,
        mcp_config_path=tmp_path / "missing-mcp.json",
        fetch=_healthy_fetch,
        can_connect=lambda host, port, timeout: True,
    )

    assert any(
        check.name == "auth_config"
        and check.status == "pass"
        and "gateway" in check.detail
        for check in report.checks
    )
