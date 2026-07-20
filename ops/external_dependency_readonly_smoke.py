#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PASS = "pass"
DEGRADED = "degraded"
BLOCKED = "blocked"
FAIL = "fail"

PLACEHOLDER_VALUES = {
    "placeholder",
    "change-me",
    "changeme",
    "ci-placeholder",
    "your_api_key",
    "your_mysql_host",
    "your_mysql_password",
    "your_neo4j_password",
}

SECRET_NAMES = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "NEO4J_PASSWORD",
    "MILVUS_API_KEY",
    "OPENWEATHER_API_KEY",
    "CLOUD_AGENT_AUTH_JWT_SECRET",
    "CLOUD_AGENT_METRICS_TOKEN",
)


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


class SmokeReport:
    def __init__(self, checks: list[CheckResult]):
        self.checks = checks

    @property
    def summary(self) -> dict[str, int]:
        return {
            "passed": sum(1 for check in self.checks if check.status == PASS),
            "degraded": sum(1 for check in self.checks if check.status == DEGRADED),
            "blocked": sum(1 for check in self.checks if check.status == BLOCKED),
            "failed": sum(1 for check in self.checks if check.status == FAIL),
        }

    @property
    def status(self) -> str:
        summary = self.summary
        if summary["failed"]:
            return "failed"
        if summary["degraded"]:
            return "degraded"
        if summary["blocked"]:
            return "incomplete"
        return "ready"

    def exit_code(self, *, strict: bool) -> int:
        summary = self.summary
        if summary["failed"]:
            return 1
        if strict and (summary["degraded"] or summary["blocked"]):
            return 1
        return 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


class SecretResolution:
    def __init__(
        self,
        *,
        value: str | None,
        source: str | None,
        status: str,
        detail: str,
    ):
        self.value = value
        self.source = source
        self.status = status
        self.detail = detail


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _has_value(value: str | None) -> bool:
    return bool(value and value.strip())


def _is_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().lower() in PLACEHOLDER_VALUES)


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
        if name:
            values[name] = _strip_optional_quotes(value.strip())
    return values


def merge_env(
    env_file: Path | None,
    process_env: dict[str, str] | None = None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_file is not None:
        merged.update(load_env_file(env_file))
    for name, value in dict(os.environ if process_env is None else process_env).items():
        if str(value).strip():
            merged[name] = value
    return merged


def _resolve_secret(env: dict[str, str], name: str) -> SecretResolution:
    direct_value = env.get(name, "").strip()
    if direct_value:
        if _is_placeholder(direct_value):
            return SecretResolution(
                value=None,
                source=name,
                status=FAIL,
                detail=f"{name} is set to a placeholder value",
            )
        return SecretResolution(value=direct_value, source=name, status=PASS, detail=f"{name}=set")

    file_name = f"{name}_FILE"
    secret_file = env.get(file_name, "").strip()
    if not secret_file:
        return SecretResolution(
            value=None,
            source=None,
            status=FAIL,
            detail=f"{name} or {file_name} is required",
        )

    path = Path(secret_file)
    if not path.exists():
        return SecretResolution(
            value=None,
            source=file_name,
            status=FAIL,
            detail=f"{file_name} does not exist: {path}",
        )

    value = path.read_text(encoding="utf-8").strip()
    if not value:
        return SecretResolution(
            value=None,
            source=file_name,
            status=FAIL,
            detail=f"{file_name} is empty: {path}",
        )
    if _is_placeholder(value):
        return SecretResolution(
            value=None,
            source=file_name,
            status=FAIL,
            detail=f"{file_name} points to a placeholder value",
        )
    return SecretResolution(value=value, source=file_name, status=PASS, detail=f"{file_name}=set")


def _secret_values(env: dict[str, str]) -> list[str]:
    values = []
    for name in SECRET_NAMES:
        value = env.get(name, "").strip()
        if len(value) >= 4:
            values.append(value)
    return values


def sanitize_text(text: object, env: dict[str, str] | None = None) -> str:
    rendered = str(text)
    for value in _secret_values(env or {}):
        rendered = rendered.replace(value, "<redacted>")
    rendered = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-<redacted>", rendered)
    rendered = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,]+",
        r"\1<redacted>",
        rendered,
    )
    rendered = re.sub(
        r"(?i)((api[_-]?key|token|secret|password)\s*=\s*)[^\s,;]+",
        r"\1<redacted>",
        rendered,
    )
    return rendered


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme:
        return value
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{port}{path}"


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _decode_json_body(body: bytes) -> object:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"non_json_body": True}


def fetch_json(url: str, timeout: float = 5.0) -> tuple[int, object]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "cloud-agent-readonly-smoke"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, _decode_json_body(response.read(128_000))
    except HTTPError as error:
        return error.code, _decode_json_body(error.read(128_000))
    except (OSError, URLError) as error:
        return 0, {"error_type": error.__class__.__name__}


def post_llm_chat_completion(
    env: dict[str, str],
    api_key: str,
    timeout: float = 15.0,
) -> tuple[int, object]:
    base_url = env.get("BASE_URL", "https://api.deepseek.com").strip() or "https://api.deepseek.com"
    model = env.get("MODEL", "deepseek-chat").strip() or "deepseek-chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Return exactly OK."}],
        "max_tokens": 4,
        "temperature": 0,
        "stream": False,
    }
    request = Request(
        _join_url(base_url, "/chat/completions"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "cloud-agent-readonly-smoke",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, _decode_json_body(response.read(128_000))
    except HTTPError as error:
        return error.code, _decode_json_body(error.read(128_000))
    except (OSError, URLError) as error:
        return 0, {"error_type": error.__class__.__name__}


def redis_ping(redis_url: str, timeout: float = 5.0) -> str:
    import redis

    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        client.ping()
        return "PING ok"
    finally:
        client.close()


def milvus_list_collections(
    *,
    host: str,
    port: int,
    token: str | None,
    timeout: float,
) -> list[str]:
    from pymilvus import connections, utility

    alias = f"cloud_agent_readonly_smoke_{os.getpid()}"
    kwargs: dict[str, object] = {
        "alias": alias,
        "host": host,
        "port": port,
        "timeout": timeout,
    }
    if token:
        kwargs["token"] = token
    connections.connect(**kwargs)
    try:
        return list(utility.list_collections(using=alias))
    finally:
        connections.disconnect(alias)


def milvus_lite_probe(env: dict[str, str]) -> str:
    import pymilvus  # noqa: F401
    import langchain_milvus  # noqa: F401
    import langchain_huggingface  # noqa: F401

    agent_dir = _repo_root() / "cloud_agent" / "agent"
    memory_uri = env.get("CLOUD_AGENT_LONG_TERM_MEMORY_URI", "").strip() or str(
        agent_dir / "milvus_lite_memory.db"
    )
    vector_uri = env.get("CLOUD_AGENT_VECTOR_SEARCH_URI", "").strip() or str(
        agent_dir / "milvus_lite_cloud.db"
    )
    return (
        "Milvus Lite packages available, "
        f"memory_uri={Path(memory_uri).name}, vector_uri={Path(vector_uri).name}"
    )


def mysql_select_one(env: dict[str, str], password: str, timeout: float = 5.0) -> str:
    import pymysql

    connection = pymysql.connect(
        host=env.get("MYSQL_HOST", "127.0.0.1"),
        port=int(env.get("MYSQL_PORT", "3306")),
        user=env.get("MYSQL_USER", "cloud_agent"),
        password=password,
        database=env.get("MYSQL_DATABASE", "cloud_platform"),
        connect_timeout=max(1, int(timeout)),
        read_timeout=max(1, int(timeout)),
        write_timeout=max(1, int(timeout)),
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            row = cursor.fetchone()
        if not row or row.get("ok") != 1:
            raise RuntimeError("SELECT 1 returned an unexpected result")
        return "SELECT 1 ok"
    finally:
        connection.close()


def _check_llm(
    checks: list[CheckResult],
    env: dict[str, str],
    *,
    timeout: float,
    skip_llm_call: bool,
    llm_post,
) -> None:
    secret = _resolve_secret(env, "DEEPSEEK_API_KEY")
    model = env.get("MODEL", "deepseek-chat").strip() or "deepseek-chat"
    base_url = env.get("BASE_URL", "https://api.deepseek.com").strip() or "https://api.deepseek.com"

    if secret.status != PASS or secret.value is None:
        checks.append(CheckResult("llm_config", secret.status, secret.detail))
        return
    if not base_url.startswith(("http://", "https://")):
        checks.append(CheckResult("llm_config", FAIL, "BASE_URL must start with http:// or https://"))
        return
    if not model:
        checks.append(CheckResult("llm_config", FAIL, "MODEL is required"))
        return

    checks.append(
        CheckResult(
            "llm_config",
            PASS,
            f"model={model}, base_url={_safe_url(base_url)}, secret_source={secret.source}",
        )
    )

    if skip_llm_call:
        checks.append(CheckResult("llm_chat_completion", BLOCKED, "skipped by --skip-llm-call"))
        return

    status_code, body = llm_post(env, secret.value, timeout)
    if 200 <= status_code < 300 and isinstance(body, dict) and isinstance(body.get("choices"), list):
        checks.append(
            CheckResult(
                "llm_chat_completion",
                PASS,
                f"HTTP {status_code}, OpenAI-compatible chat completion accepted",
            )
        )
        return

    detail = f"HTTP {status_code}, chat completion rejected"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
            if code:
                detail += f", error_code={code}"
        elif body.get("error_type"):
            detail += f", error_type={body['error_type']}"
    checks.append(CheckResult("llm_chat_completion", FAIL, sanitize_text(detail, env)))


def _check_redis(checks: list[CheckResult], env: dict[str, str], *, timeout: float, ping) -> None:
    redis_url = env.get("REDIS_URL", "redis://127.0.0.1:6379").strip() or "redis://127.0.0.1:6379"
    try:
        detail = ping(redis_url, timeout)
    except ModuleNotFoundError:
        checks.append(CheckResult("redis", BLOCKED, "redis package is not installed"))
    except Exception as error:
        checks.append(CheckResult("redis", DEGRADED, sanitize_text(f"{_safe_url(redis_url)} unreachable: {error.__class__.__name__}", env)))
    else:
        checks.append(CheckResult("redis", PASS, f"{_safe_url(redis_url)} {detail}"))


def _env_flag(env: dict[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _check_milvus(
    checks: list[CheckResult],
    env: dict[str, str],
    *,
    timeout: float,
    list_collections,
    lite_probe,
) -> None:
    long_term_enabled = _env_flag(env, "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED", True)
    vector_enabled = _env_flag(env, "CLOUD_AGENT_VECTOR_SEARCH_ENABLED", True)
    if not long_term_enabled and not vector_enabled:
        checks.append(CheckResult("milvus", PASS, "long-term memory and vector search disabled by env"))
        return

    mode = env.get("CLOUD_AGENT_MILVUS_MODE", "lite").strip().lower() or "lite"
    if mode in {"lite", "local", "milvus-lite"}:
        try:
            detail = lite_probe(env)
        except ModuleNotFoundError as error:
            checks.append(CheckResult("milvus", BLOCKED, f"{error.name or 'milvus'} package is not installed"))
        except Exception as error:
            checks.append(CheckResult("milvus", DEGRADED, sanitize_text(f"Milvus Lite probe failed: {error.__class__.__name__}", env)))
        else:
            checks.append(CheckResult("milvus", PASS, detail))
        return

    if mode not in {"remote", "server", "standalone"}:
        checks.append(CheckResult("milvus", FAIL, f"unknown CLOUD_AGENT_MILVUS_MODE: {mode}"))
        return

    host = env.get("MILVUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _safe_int(env.get("MILVUS_PORT"), 19530)
    token = _resolve_secret(env, "MILVUS_API_KEY")
    token_value = token.value if token.status == PASS else None
    try:
        collections = list_collections(host=host, port=port, token=token_value, timeout=timeout)
    except ModuleNotFoundError:
        checks.append(CheckResult("milvus", BLOCKED, "pymilvus package is not installed"))
    except Exception as error:
        checks.append(CheckResult("milvus", DEGRADED, sanitize_text(f"{host}:{port} list_collections failed: {error.__class__.__name__}", env)))
    else:
        checks.append(CheckResult("milvus", PASS, f"{host}:{port} reachable, collections={len(collections)}"))


def _check_mysql_mcp(checks: list[CheckResult], env: dict[str, str], *, timeout: float, mysql_ping) -> None:
    host = env.get("MYSQL_HOST", "").strip()
    if not host or _is_placeholder(host):
        checks.append(CheckResult("mysql_mcp", BLOCKED, "MYSQL_HOST unset; MySQL-backed MCP smoke skipped"))
        return

    password = _resolve_secret(env, "MYSQL_PASSWORD")
    if password.status != PASS or password.value is None:
        status = BLOCKED if password.source is None else password.status
        checks.append(CheckResult("mysql_mcp", status, password.detail))
        return

    try:
        detail = mysql_ping(env, password.value, timeout)
    except ModuleNotFoundError:
        checks.append(CheckResult("mysql_mcp", BLOCKED, "pymysql package is not installed"))
    except Exception as error:
        checks.append(CheckResult("mysql_mcp", DEGRADED, sanitize_text(f"{host}:{env.get('MYSQL_PORT', '3306')} SELECT 1 failed: {error.__class__.__name__}", env)))
    else:
        checks.append(CheckResult("mysql_mcp", PASS, f"{host}:{env.get('MYSQL_PORT', '3306')} {detail}"))


def _resolve_mcp_cwd(config_path: Path, cwd: str) -> Path:
    if Path(cwd).is_absolute():
        return Path(cwd)
    config_dir = config_path.parent
    if config_dir.name == "config" and config_dir.parent.name == "agent":
        return config_dir.parent.parent / cwd
    return config_dir / cwd


def _check_mcp_config(checks: list[CheckResult], config_path: Path) -> None:
    if not config_path.exists():
        checks.append(CheckResult("mcp_config", BLOCKED, f"missing {config_path}"))
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        checks.append(CheckResult("mcp_config", FAIL, f"invalid JSON: {error.msg}"))
        return

    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        checks.append(CheckResult("mcp_config", BLOCKED, "no MCP servers configured"))
        return

    missing_cwd = []
    for name, server in servers.items():
        if not isinstance(server, dict):
            checks.append(CheckResult("mcp_config", FAIL, f"server {name} must be an object"))
            return
        cwd = server.get("cwd")
        if isinstance(cwd, str) and not _resolve_mcp_cwd(config_path, cwd).exists():
            missing_cwd.append(name)
    if missing_cwd:
        checks.append(CheckResult("mcp_config", DEGRADED, f"missing cwd for server(s): {','.join(missing_cwd)}"))
        return

    checks.append(CheckResult("mcp_config", PASS, f"{len(servers)} server(s) configured"))


def _check_auth(checks: list[CheckResult], env: dict[str, str], *, timeout: float, fetch) -> None:
    mode = env.get("CLOUD_AGENT_AUTH_MODE", "local").strip().lower() or "local"
    if mode not in {"prod", "production"}:
        checks.append(CheckResult("auth_config", PASS, f"mode={mode}"))
        return

    strategy = env.get("CLOUD_AGENT_AUTH_STRATEGY", "gateway").strip().lower() or "gateway"
    if strategy == "gateway":
        checks.append(CheckResult("auth_config", PASS, "production gateway mode uses trusted upstream headers"))
        return

    if strategy in {"jwt", "bearer"}:
        secret = _resolve_secret(env, "CLOUD_AGENT_AUTH_JWT_SECRET")
        checks.append(CheckResult("auth_config", secret.status, secret.detail if secret.status != PASS else f"production jwt {secret.source}=set"))
        return

    if strategy not in {"oidc", "jwks"}:
        checks.append(CheckResult("auth_config", FAIL, f"unknown production auth strategy: {strategy}"))
        return

    discovery_url = env.get("CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL", "").strip()
    jwks_url = env.get("CLOUD_AGENT_AUTH_JWKS_URL", "").strip()
    if discovery_url:
        status_code, body = fetch(discovery_url, timeout)
        if not (200 <= status_code < 300 and isinstance(body, dict)):
            checks.append(CheckResult("auth_jwks", FAIL, f"discovery HTTP {status_code} unavailable"))
            return
        jwks_url = jwks_url or str(body.get("jwks_uri", "")).strip()
        if not jwks_url:
            checks.append(CheckResult("auth_jwks", FAIL, "OIDC discovery did not provide jwks_uri"))
            return

    if not jwks_url:
        checks.append(CheckResult("auth_jwks", FAIL, "production oidc/jwks requires JWKS or discovery URL"))
        return

    status_code, body = fetch(jwks_url, timeout)
    if 200 <= status_code < 300 and isinstance(body, dict) and isinstance(body.get("keys"), list):
        checks.append(CheckResult("auth_jwks", PASS, f"JWKS reachable, keys={len(body['keys'])}"))
        return

    checks.append(CheckResult("auth_jwks", FAIL, f"JWKS HTTP {status_code} unavailable or invalid"))


def build_report(
    *,
    env: dict[str, str] | None = None,
    timeout: float = 5.0,
    mcp_config_path: Path | None = None,
    skip_llm_call: bool = False,
    llm_post=post_llm_chat_completion,
    redis_ping_func=redis_ping,
    milvus_list_func=milvus_list_collections,
    milvus_lite_func=milvus_lite_probe,
    mysql_ping_func=mysql_select_one,
    fetch_json_func=fetch_json,
) -> SmokeReport:
    env = dict(os.environ if env is None else env)
    mcp_config_path = mcp_config_path or (
        _repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json"
    )

    checks: list[CheckResult] = []
    _check_llm(
        checks,
        env,
        timeout=timeout,
        skip_llm_call=skip_llm_call,
        llm_post=llm_post,
    )
    _check_redis(checks, env, timeout=timeout, ping=redis_ping_func)
    _check_milvus(
        checks,
        env,
        timeout=timeout,
        list_collections=milvus_list_func,
        lite_probe=milvus_lite_func,
    )
    _check_mysql_mcp(checks, env, timeout=timeout, mysql_ping=mysql_ping_func)
    _check_mcp_config(checks, Path(mcp_config_path))
    _check_auth(checks, env, timeout=timeout, fetch=fetch_json_func)
    return SmokeReport(checks)


def format_text(report: SmokeReport) -> str:
    labels = {
        PASS: "PASS",
        DEGRADED: "DEGRADED",
        BLOCKED: "BLOCKED",
        FAIL: "FAIL",
    }
    lines = [
        f"[external-smoke] cloud_agent read-only dependency smoke: {report.status}",
        (
            "[external-smoke] summary: "
            f"passed={report.summary['passed']} "
            f"degraded={report.summary['degraded']} "
            f"blocked={report.summary['blocked']} "
            f"failed={report.summary['failed']}"
        ),
    ]
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name} - {check.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: SmokeReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, report: SmokeReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_json(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent external dependency read-only smoke")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file to load")
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=_repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json",
        help="MCP server registry config path",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="Network timeout seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for blocked/degraded checks")
    parser.add_argument("--skip-llm-call", action="store_true", help="Validate LLM config without calling the provider")
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "external-readonly-smoke.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write a JSON artifact")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env = merge_env(args.env_file)
    except OSError as error:
        sys.stderr.write(f"[external-smoke] failed to read env file: {error}\n")
        return 2

    report = build_report(
        env=env,
        timeout=args.timeout,
        mcp_config_path=args.mcp_config,
        skip_llm_call=args.skip_llm_call,
    )
    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report))
    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
