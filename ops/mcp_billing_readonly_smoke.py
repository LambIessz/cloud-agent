#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Sequence


PASS = "pass"
DEGRADED = "degraded"
BLOCKED = "blocked"
FAIL = "fail"

REQUIRED_BILLING_TOOLS = ("query_user_orders", "query_user_instances")

PLACEHOLDER_VALUES = {
    "placeholder",
    "change-me",
    "changeme",
    "ci-placeholder",
    "your_mysql_host",
    "your_mysql_password",
}

SECRET_NAMES = (
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MILVUS_API_KEY",
    "CLOUD_AGENT_AUTH_JWT_SECRET",
)


class CheckResult:
    def __init__(self, name: str, status: str, detail: str) -> None:
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
    def __init__(self, checks: list[CheckResult]) -> None:
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
    def __init__(self, *, value: str | None, source: str | None, status: str, detail: str) -> None:
        self.value = value
        self.source = source
        self.status = status
        self.detail = detail


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _agent_dir() -> Path:
    return _repo_root() / "cloud_agent" / "agent"


def _ensure_agent_path() -> None:
    agent_dir = str(_agent_dir())
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)


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


def _is_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().lower() in PLACEHOLDER_VALUES)


def _secret_values(env: dict[str, str]) -> list[str]:
    values: list[str] = []
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
    rendered = re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+", "jwt-<redacted>", rendered)
    rendered = re.sub(
        r"(?i)((api[_-]?key|token|secret|password)\s*=\s*)[^\s,;]+",
        r"\1<redacted>",
        rendered,
    )
    return rendered


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
            status=BLOCKED,
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


def _safe_limit(value: int) -> int:
    return max(1, min(int(value), 20))


def _mysql_config_check(env: dict[str, str]) -> CheckResult:
    host = env.get("MYSQL_HOST", "").strip()
    if not host or _is_placeholder(host):
        return CheckResult("mysql_billing_config", BLOCKED, "MYSQL_HOST unset; billing tool smoke skipped")

    password = _resolve_secret(env, "MYSQL_PASSWORD")
    if password.status != PASS:
        return CheckResult("mysql_billing_config", password.status, password.detail)

    user = env.get("MYSQL_USER", "cloud_agent").strip() or "cloud_agent"
    database = env.get("MYSQL_DATABASE", "cloud_platform").strip() or "cloud_platform"
    port = env.get("MYSQL_PORT", "3306").strip() or "3306"
    return CheckResult(
        "mysql_billing_config",
        PASS,
        f"host={host}, port={port}, user={user}, database={database}, secret_source={password.source}",
    )


def default_registry_probe(config_path: Path, timeout: float) -> list[str]:
    _ensure_agent_path()
    from core.mcp.mcp_manager import MCPToolRegistry

    async def _probe() -> list[str]:
        registry = MCPToolRegistry(config_path)
        try:
            return await registry.get_tool_names_for_agent(
                "billing",
                request_id="req_mcp_billing_smoke",
                user_id_hash="hash_mcp_billing_smoke",
            )
        finally:
            await registry.close()

    output_buffer = io.StringIO()
    with contextlib.redirect_stdout(output_buffer):
        return asyncio.run(asyncio.wait_for(_probe(), timeout=timeout))


def default_billing_tool_call(
    tool_name: str,
    env: dict[str, str],
    user_id: str,
    limit: int,
    timeout: float,
) -> dict[str, object]:
    _ensure_agent_path()
    module = importlib.import_module("mcp_servers.cloud_platform_server")
    original_get_connection = module.get_db_connection

    def _get_timed_connection():
        import pymysql

        return pymysql.connect(
            host=env.get("MYSQL_HOST", "127.0.0.1"),
            port=int(env.get("MYSQL_PORT", "3306")),
            user=env.get("MYSQL_USER", "cloud_agent"),
            password=_resolve_secret(env, "MYSQL_PASSWORD").value or "",
            database=env.get("MYSQL_DATABASE", "cloud_platform"),
            connect_timeout=max(1, int(timeout)),
            read_timeout=max(1, int(timeout)),
            write_timeout=max(1, int(timeout)),
            cursorclass=pymysql.cursors.DictCursor,
        )

    module.get_db_connection = _get_timed_connection
    try:
        raw = getattr(module, tool_name)(user_id=user_id, limit=_safe_limit(limit))
    finally:
        module.get_db_connection = original_get_connection

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "error", "error_type": "NonJsonToolOutput"}
    return payload if isinstance(payload, dict) else {"status": "error", "error_type": "UnexpectedToolOutput"}


def _check_registry(
    checks: list[CheckResult],
    *,
    mcp_config_path: Path,
    timeout: float,
    registry_probe: Callable[[Path, float], Sequence[str]],
) -> None:
    if not mcp_config_path.exists():
        checks.append(CheckResult("mcp_registry_billing_tools", BLOCKED, f"missing {mcp_config_path}"))
        return

    try:
        tool_names = list(registry_probe(mcp_config_path, timeout))
    except ModuleNotFoundError as error:
        checks.append(CheckResult("mcp_registry_billing_tools", BLOCKED, f"{error.name or 'mcp'} package is not installed"))
        return
    except TimeoutError:
        checks.append(CheckResult("mcp_registry_billing_tools", DEGRADED, f"registry probe timed out after {timeout:g}s"))
        return
    except Exception as error:
        checks.append(CheckResult("mcp_registry_billing_tools", DEGRADED, f"registry probe failed: {error.__class__.__name__}"))
        return

    missing = [name for name in REQUIRED_BILLING_TOOLS if name not in tool_names]
    if missing:
        checks.append(CheckResult("mcp_registry_billing_tools", FAIL, f"missing billing tool(s): {','.join(missing)}"))
        return

    unexpected = [name for name in tool_names if name not in REQUIRED_BILLING_TOOLS]
    detail = f"billing tools ready: {','.join(REQUIRED_BILLING_TOOLS)}"
    if unexpected:
        detail += f"; unexpected={len(unexpected)}"
    checks.append(CheckResult("mcp_registry_billing_tools", PASS, detail))


def _tool_payload_detail(payload: dict[str, object]) -> tuple[str, str]:
    status = str(payload.get("status", "")).lower()
    if status == "success":
        data = payload.get("data")
        if isinstance(data, list):
            return PASS, f"status=success, rows={len(data)}"
        if "message" in payload:
            return PASS, "status=success, message_only"
        return PASS, "status=success"

    error_type = payload.get("error_type")
    if isinstance(error_type, str) and error_type:
        return DEGRADED, f"status={status or 'unknown'}, error_type={error_type}"
    return DEGRADED, f"status={status or 'unknown'}"


def _check_billing_tool(
    checks: list[CheckResult],
    *,
    tool_name: str,
    env: dict[str, str],
    user_id: str,
    limit: int,
    timeout: float,
    billing_tool_call: Callable[[str, dict[str, str], str, int, float], dict[str, object]],
) -> None:
    try:
        payload = billing_tool_call(tool_name, env, user_id, _safe_limit(limit), timeout)
    except ModuleNotFoundError as error:
        checks.append(CheckResult(tool_name, BLOCKED, f"{error.name or 'dependency'} package is not installed"))
        return
    except TimeoutError:
        checks.append(CheckResult(tool_name, DEGRADED, f"timed out after {timeout:g}s"))
        return
    except Exception as error:
        checks.append(CheckResult(tool_name, DEGRADED, sanitize_text(f"tool call failed: {error.__class__.__name__}", env)))
        return

    status, detail = _tool_payload_detail(payload)
    checks.append(CheckResult(tool_name, status, sanitize_text(detail, env)))


def build_report(
    *,
    env: dict[str, str] | None = None,
    mcp_config_path: Path | None = None,
    user_id: str = "smoke_billing_user",
    limit: int = 2,
    timeout: float = 5.0,
    registry_probe_func: Callable[[Path, float], Sequence[str]] = default_registry_probe,
    billing_tool_call_func: Callable[[str, dict[str, str], str, int, float], dict[str, object]] = default_billing_tool_call,
) -> SmokeReport:
    env = dict(os.environ if env is None else env)
    mcp_config_path = mcp_config_path or (
        _repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json"
    )

    checks: list[CheckResult] = []
    _check_registry(
        checks,
        mcp_config_path=Path(mcp_config_path),
        timeout=timeout,
        registry_probe=registry_probe_func,
    )

    mysql_check = _mysql_config_check(env)
    checks.append(mysql_check)
    if mysql_check.status != PASS:
        for tool_name in REQUIRED_BILLING_TOOLS:
            checks.append(CheckResult(tool_name, BLOCKED, "skipped because mysql_billing_config is not pass"))
        return SmokeReport(checks)

    for tool_name in REQUIRED_BILLING_TOOLS:
        _check_billing_tool(
            checks,
            tool_name=tool_name,
            env=env,
            user_id=user_id,
            limit=limit,
            timeout=timeout,
            billing_tool_call=billing_tool_call_func,
        )
    return SmokeReport(checks)


def format_text(report: SmokeReport) -> str:
    labels = {
        PASS: "PASS",
        DEGRADED: "DEGRADED",
        BLOCKED: "BLOCKED",
        FAIL: "FAIL",
    }
    lines = [
        f"[mcp-billing-smoke] cloud_agent MCP billing read-only smoke: {report.status}",
        (
            "[mcp-billing-smoke] summary: "
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
    parser = argparse.ArgumentParser(description="Cloud Agent MCP billing read-only smoke")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file to load")
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=_repo_root() / "cloud_agent" / "agent" / "config" / "mcp_servers.json",
        help="MCP server registry config path",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("CLOUD_AGENT_MCP_SMOKE_USER_ID", "smoke_billing_user"),
        help="User id used for read-only billing queries",
    )
    parser.add_argument("--limit", type=int, default=2, help="Maximum records per billing query")
    parser.add_argument("--timeout", type=float, default=5.0, help="Registry and DB timeout seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for blocked/degraded checks")
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "mcp-billing-smoke.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write a JSON artifact")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env = merge_env(args.env_file)
    except OSError as error:
        sys.stderr.write(f"[mcp-billing-smoke] failed to read env file: {error}\n")
        return 2

    report = build_report(
        env=env,
        mcp_config_path=args.mcp_config,
        user_id=args.user_id,
        limit=args.limit,
        timeout=args.timeout,
    )
    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report))
    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
