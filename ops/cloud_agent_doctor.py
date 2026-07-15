#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PASS = "pass"
DEGRADED = "degraded"
FAIL = "fail"


class CheckResult:
    def __init__(self, name: str, status: str, detail: str):
        self.name = name
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


class DoctorReport:
    def __init__(self, checks: list[CheckResult]):
        self.checks = checks

    @property
    def summary(self) -> dict[str, int]:
        return {
            "passed": sum(1 for check in self.checks if check.status == PASS),
            "degraded": sum(1 for check in self.checks if check.status == DEGRADED),
            "failed": sum(1 for check in self.checks if check.status == FAIL),
        }

    @property
    def status(self) -> str:
        summary = self.summary
        if summary["failed"]:
            return "failed"
        if summary["degraded"]:
            return "degraded"
        return "ready"

    def exit_code(self, strict: bool) -> int:
        summary = self.summary
        if summary["failed"]:
            return 1
        if strict and summary["degraded"]:
            return 1
        return 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _env_flag(env: dict[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().lstrip("\ufeff")
        if not name:
            continue
        values[name] = _strip_optional_quotes(value.strip())
    return values


def merge_env(env_file: Path | None, process_env: dict[str, str] | None = None) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_file is not None:
        merged.update(load_env_file(env_file))
    for name, value in dict(os.environ if process_env is None else process_env).items():
        if str(value).strip():
            merged[name] = value
    return merged


def fetch_url(url: str, timeout: float = 5.0):
    request = Request(url, headers={"Accept": "application/json,text/plain,*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(512_000).decode("utf-8", errors="replace")
            return response.status, _decode_body(body, response.headers.get("Content-Type", ""))
    except HTTPError as error:
        body = error.read(512_000).decode("utf-8", errors="replace")
        return error.code, _decode_body(body, error.headers.get("Content-Type", ""))
    except URLError as error:
        return 0, {"error": str(error.reason)}
    except OSError as error:
        return 0, {"error": str(error)}


def _decode_body(body: str, content_type: str):
    stripped = body.strip()
    if "json" in content_type or stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return body
    return body


def can_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_llm_secret(checks: list[CheckResult], env: dict[str, str]) -> None:
    api_key = env.get("DEEPSEEK_API_KEY", "").strip()
    api_key_file = env.get("DEEPSEEK_API_KEY_FILE", "").strip()
    if not api_key and not api_key_file:
        checks.append(
            CheckResult(
                "llm_secret",
                FAIL,
                "DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE is required",
            )
        )
        return

    if api_key_file and not api_key and not Path(api_key_file).exists():
        checks.append(
            CheckResult(
                "llm_secret",
                FAIL,
                f"DEEPSEEK_API_KEY_FILE does not exist: {api_key_file}",
            )
        )
        return

    if api_key and api_key.lower() in {"ci-placeholder", "placeholder", "change-me"}:
        checks.append(
            CheckResult(
                "llm_secret",
                FAIL,
                "DEEPSEEK_API_KEY is set to a placeholder value",
            )
        )
        return

    source = "DEEPSEEK_API_KEY_FILE" if api_key_file else "DEEPSEEK_API_KEY"
    checks.append(CheckResult("llm_secret", PASS, f"{source}=set"))


def _check_cors(
    checks: list[CheckResult],
    env: dict[str, str],
    frontend_origin: str | None,
) -> None:
    raw_origins = env.get("CLOUD_AGENT_CORS_ORIGINS", "").strip()
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if "*" in origins:
        checks.append(
            CheckResult(
                "cors",
                FAIL,
                "wildcard origin is not allowed when credentials are enabled",
            )
        )
        return

    if frontend_origin and origins and frontend_origin not in origins:
        checks.append(
            CheckResult(
                "cors",
                FAIL,
                f"frontend origin {frontend_origin} is missing from CLOUD_AGENT_CORS_ORIGINS",
            )
        )
        return

    if frontend_origin and not origins and not frontend_origin.startswith("http://127.0.0.1"):
        checks.append(
            CheckResult(
                "cors",
                DEGRADED,
                "CLOUD_AGENT_CORS_ORIGINS unset; app will use local-development defaults",
            )
        )
        return

    detail = "configured" if origins else "using local-development defaults"
    checks.append(CheckResult("cors", PASS, detail))


def _check_auth_config(checks: list[CheckResult], env: dict[str, str]) -> None:
    mode = env.get("CLOUD_AGENT_AUTH_MODE", "local").strip().lower() or "local"
    if mode not in {"prod", "production"}:
        checks.append(CheckResult("auth_config", PASS, f"mode={mode}"))
        return

    strategy = env.get("CLOUD_AGENT_AUTH_STRATEGY", "gateway").strip().lower() or "gateway"
    if strategy == "gateway":
        user_header = env.get("CLOUD_AGENT_AUTH_USER_HEADER", "X-Authenticated-User-Id")
        tenant_header = env.get("CLOUD_AGENT_AUTH_TENANT_HEADER", "X-Authenticated-Tenant-Id")
        checks.append(
            CheckResult(
                "auth_config",
                PASS,
                f"production gateway headers user={user_header} tenant={tenant_header}",
            )
        )
        return

    if strategy in {"jwt", "bearer"}:
        secret = env.get("CLOUD_AGENT_AUTH_JWT_SECRET", "").strip()
        secret_file = env.get("CLOUD_AGENT_AUTH_JWT_SECRET_FILE", "").strip()
        if not secret and not secret_file:
            checks.append(
                CheckResult(
                    "auth_config",
                    FAIL,
                    "production jwt requires CLOUD_AGENT_AUTH_JWT_SECRET or CLOUD_AGENT_AUTH_JWT_SECRET_FILE",
                )
            )
            return
        if secret_file and not secret and not Path(secret_file).exists():
            checks.append(
                CheckResult(
                    "auth_config",
                    FAIL,
                    f"CLOUD_AGENT_AUTH_JWT_SECRET_FILE does not exist: {secret_file}",
                )
            )
            return
        if secret.lower() in {"placeholder", "change-me", "ci-placeholder"}:
            checks.append(
                CheckResult(
                    "auth_config",
                    FAIL,
                    "CLOUD_AGENT_AUTH_JWT_SECRET is set to a placeholder value",
                )
            )
            return
        source = "CLOUD_AGENT_AUTH_JWT_SECRET_FILE" if secret_file else "CLOUD_AGENT_AUTH_JWT_SECRET"
        checks.append(CheckResult("auth_config", PASS, f"production jwt {source}=set"))
        return

    if strategy in {"oidc", "jwks"}:
        jwks_url = env.get("CLOUD_AGENT_AUTH_JWKS_URL", "").strip()
        discovery_url = env.get("CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL", "").strip()
        if not jwks_url and not discovery_url:
            checks.append(
                CheckResult(
                    "auth_config",
                    FAIL,
                    "production oidc/jwks requires CLOUD_AGENT_AUTH_JWKS_URL or CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
                )
            )
            return
        source = "CLOUD_AGENT_AUTH_JWKS_URL" if jwks_url else "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL"
        checks.append(CheckResult("auth_config", PASS, f"production {strategy} {source}=set"))
        return

    checks.append(CheckResult("auth_config", FAIL, f"unknown production auth strategy: {strategy}"))


def _check_http_json(
    checks: list[CheckResult],
    name: str,
    url: str,
    fetch,
    expected_status: str,
) -> None:
    status_code, body = fetch(url)
    if not isinstance(body, dict):
        checks.append(CheckResult(name, FAIL, f"HTTP {status_code}, non-JSON response"))
        return

    status = body.get("status")
    if 200 <= status_code < 300 and status == expected_status:
        checks.append(CheckResult(name, PASS, f"HTTP {status_code}, status={status}"))
        return

    checks.append(CheckResult(name, FAIL, f"HTTP {status_code}, status={status}"))


def _check_metrics(checks: list[CheckResult], base_url: str, fetch) -> None:
    status_code, body = fetch(_join_url(base_url, "/api/metrics"))
    metrics_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    if 200 <= status_code < 300:
        if "cloud_agent" in metrics_text:
            detail = f"HTTP {status_code}, cloud_agent metrics present"
        else:
            detail = f"HTTP {status_code}, endpoint reachable, no samples yet"
        checks.append(CheckResult("metrics", PASS, detail))
        return

    checks.append(CheckResult("metrics", FAIL, f"HTTP {status_code}, metrics endpoint unavailable"))


def _parse_host_port_from_url(url: str, default_port: int) -> tuple[str, int] | None:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or default_port


def _check_redis(checks: list[CheckResult], env: dict[str, str], connect, timeout: float) -> None:
    if not _env_flag(env, "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED", True):
        checks.append(CheckResult("redis", PASS, "semantic cache disabled by env"))
        return

    redis_url = env.get("REDIS_URL", "").strip()
    if not redis_url:
        checks.append(
            CheckResult(
                "redis",
                DEGRADED,
                "REDIS_URL unset; Redis-backed memory/cache features may be unavailable",
            )
        )
        return

    endpoint = _parse_host_port_from_url(redis_url, 6379)
    if endpoint is None:
        checks.append(CheckResult("redis", FAIL, "REDIS_URL is not a valid URL"))
        return

    host, port = endpoint
    if connect(host, port, timeout):
        checks.append(CheckResult("redis", PASS, f"{host}:{port} reachable"))
        return

    checks.append(CheckResult("redis", DEGRADED, f"{host}:{port} unreachable"))


def _check_milvus(checks: list[CheckResult], env: dict[str, str], connect, timeout: float) -> None:
    long_term_enabled = _env_flag(env, "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED", True)
    vector_enabled = _env_flag(env, "CLOUD_AGENT_VECTOR_SEARCH_ENABLED", True)
    if not long_term_enabled and not vector_enabled:
        checks.append(CheckResult("milvus", PASS, "long-term memory and vector search disabled by env"))
        return

    mode = env.get("CLOUD_AGENT_MILVUS_MODE", "lite").strip().lower() or "lite"
    if mode in {"lite", "local", "milvus-lite"}:
        try:
            import pymilvus  # noqa: F401
            import langchain_milvus  # noqa: F401
            import langchain_huggingface  # noqa: F401
        except ModuleNotFoundError as error:
            checks.append(CheckResult("milvus", DEGRADED, f"{error.name or 'milvus'} package is not installed"))
            return

        checks.append(CheckResult("milvus", PASS, "Milvus Lite packages available"))
        return

    if mode not in {"remote", "server", "standalone"}:
        checks.append(CheckResult("milvus", FAIL, f"unknown CLOUD_AGENT_MILVUS_MODE: {mode}"))
        return

    host = env.get("MILVUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _safe_int(env.get("MILVUS_PORT"), 19530)
    if connect(host, port, timeout):
        checks.append(CheckResult("milvus", PASS, f"{host}:{port} reachable"))
        return

    checks.append(CheckResult("milvus", DEGRADED, f"{host}:{port} unreachable"))


def _check_mcp_config(checks: list[CheckResult], mcp_config_path: Path) -> None:
    if not mcp_config_path.exists():
        checks.append(CheckResult("mcp_config", DEGRADED, f"missing {mcp_config_path}"))
        return

    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        checks.append(CheckResult("mcp_config", FAIL, f"invalid JSON: {error.msg}"))
        return

    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        checks.append(CheckResult("mcp_config", DEGRADED, "no MCP servers configured"))
        return

    checks.append(CheckResult("mcp_config", PASS, f"{len(servers)} server(s) configured"))


def build_report(
    *,
    env: dict[str, str] | None = None,
    base_url: str,
    frontend_origin: str | None,
    mcp_config_path: Path | None = None,
    fetch=None,
    can_connect=None,
    timeout: float = 5.0,
) -> DoctorReport:
    env = dict(os.environ if env is None else env)
    fetch = fetch or (lambda url: fetch_url(url, timeout=timeout))
    connect = can_connect or globals()["can_connect"]
    mcp_config_path = mcp_config_path or (
        _repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json"
    )

    checks: list[CheckResult] = []
    _check_llm_secret(checks, env)
    _check_cors(checks, env, frontend_origin)
    _check_auth_config(checks, env)
    _check_http_json(checks, "healthz", _join_url(base_url, "/healthz"), fetch, "ok")
    _check_http_json(checks, "readyz", _join_url(base_url, "/readyz"), fetch, "ready")
    _check_metrics(checks, base_url, fetch)
    _check_redis(checks, env, connect, timeout)
    _check_milvus(checks, env, connect, timeout)
    _check_mcp_config(checks, Path(mcp_config_path))
    return DoctorReport(checks)


def format_text(report: DoctorReport) -> str:
    lines = [
        f"[doctor] cloud_agent deployment preflight: {report.status}",
        (
            "[doctor] summary: "
            f"passed={report.summary['passed']} "
            f"degraded={report.summary['degraded']} "
            f"failed={report.summary['failed']}"
        ),
    ]
    labels = {
        PASS: "PASS",
        DEGRADED: "DEGRADED",
        FAIL: "FAIL",
    }
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name} - {check.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent deployment preflight doctor")
    parser.add_argument(
        "--base-url",
        default=os.getenv("CLOUD_AGENT_BASE_URL", "http://127.0.0.1:5000"),
        help="Cloud Agent backend base URL",
    )
    parser.add_argument(
        "--frontend-origin",
        default=os.getenv("CLOUD_AGENT_FRONTEND_ORIGIN"),
        help="Expected browser frontend origin to validate against CORS",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=_repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json",
        help="MCP server registry config path",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional env file to load before process environment overrides",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP and socket timeout seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when optional dependencies are degraded",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env = merge_env(args.env_file)
    except OSError as error:
        sys.stderr.write(f"[doctor] failed to read env file: {error}\n")
        return 2

    report = build_report(
        env=env,
        base_url=args.base_url,
        frontend_origin=args.frontend_origin,
        mcp_config_path=args.mcp_config,
        timeout=args.timeout,
    )
    output = format_json(report) if args.json else format_text(report)
    sys.stdout.write(output)
    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
