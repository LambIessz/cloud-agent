#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


PASS = "pass"
DEGRADED = "degraded"
BLOCKED = "blocked"
FAIL = "fail"

SECRET_NAMES = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "NEO4J_PASSWORD",
    "MILVUS_API_KEY",
    "OPENWEATHER_API_KEY",
    "CLOUD_AGENT_AUTH_JWT_SECRET",
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


class MemorySmokeReport:
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


class _SyntheticLLM:
    def __init__(self, marker: str):
        self._marker = marker

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(content=f"preference: {self._marker}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
    rendered = re.sub(
        r"(?i)((api[_-]?key|token|secret|password)\s*=\s*)[^\s,;]+",
        r"\1<redacted>",
        rendered,
    )
    return rendered


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


def _milvus_mode(env: dict[str, str]) -> str:
    return env.get("CLOUD_AGENT_MILVUS_MODE", "lite").strip().lower() or "lite"


def _prepare_lite_smoke_uri(env: dict[str, str]) -> None:
    if _milvus_mode(env) not in {"lite", "local", "milvus-lite"}:
        return
    if env.get("CLOUD_AGENT_LONG_TERM_MEMORY_URI", "").strip():
        return

    run_dir = _repo_root() / ".codex-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    env["CLOUD_AGENT_LONG_TERM_MEMORY_URI"] = str(
        run_dir / f"memory-e2e-milvus-{os.getpid()}-{uuid.uuid4().hex[:8]}.db"
    )


def _install_agent_path() -> None:
    agent_dir = _repo_root() / "cloud_agent" / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))


def default_memory_factory(env: dict[str, str]):
    _install_agent_path()
    from core.memory.memory_manager import MemoryManager

    redis_ttl = _safe_int(
        env.get("CLOUD_AGENT_REDIS_TTL_SECONDS") or env.get("REDIS_TTL_SECONDS"),
        1800,
    )
    return MemoryManager(
        redis_url=env.get("REDIS_URL", "redis://127.0.0.1:6379").strip()
        or "redis://127.0.0.1:6379",
        redis_ttl=redis_ttl,
        milvus_host=env.get("MILVUS_HOST", "localhost").strip() or "localhost",
        milvus_port=_safe_int(env.get("MILVUS_PORT"), 19530),
        milvus_api_key=env.get("MILVUS_API_KEY") or None,
        embedding_api_key=env.get("DASHSCOPE_API_KEY") or None,
    )


def _synthetic_messages(marker: str) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": f"synthetic memory smoke marker {marker}"},
        {"role": "assistant", "content": "acknowledged synthetic memory smoke marker"},
        {"role": "user", "content": "please keep future answers concise for this synthetic smoke"},
        {"role": "assistant", "content": "stored concise synthetic preference"},
    ]


async def _wait_for_retrieval(
    memory: Any,
    *,
    user_id: str,
    marker: str,
    timeout_seconds: float,
) -> tuple[list[str], int]:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_results: list[str] = []
    while True:
        attempts += 1
        last_results = await memory.load_preferences(user_id, query=marker, top_k=10)
        if any(marker in item for item in last_results):
            return last_results, attempts
        if time.monotonic() >= deadline:
            return last_results, attempts
        await asyncio.sleep(0.5)


async def _cleanup_long_term_user(memory: Any, user_id: str) -> str:
    long_term = getattr(memory, "long_term", None)
    client = getattr(long_term, "_client", None)
    if client is None or not getattr(long_term, "available", False):
        return "long-term cleanup skipped"

    _install_agent_path()
    from core.memory.long_term import COLLECTION_NAME

    client.delete(
        collection_name=COLLECTION_NAME,
        filter=f'user_id == "{user_id}"',
    )
    return "long-term synthetic rows deleted"


async def run_memory_smoke(
    *,
    env: dict[str, str] | None = None,
    memory_factory: Callable[[dict[str, str]], Any] = default_memory_factory,
    marker_factory: Callable[[], str] | None = None,
    retrieval_timeout: float = 8.0,
) -> MemorySmokeReport:
    env = dict(os.environ if env is None else env)
    previous_env = os.environ.copy()
    env_restored = False
    env.setdefault("CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED", "true")
    env.setdefault("CLOUD_AGENT_VECTOR_SEARCH_ENABLED", "true")
    env.setdefault("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "true")
    _prepare_lite_smoke_uri(env)
    os.environ.update(env)

    def restore_env() -> None:
        nonlocal env_restored
        if env_restored:
            return
        os.environ.clear()
        os.environ.update(previous_env)
        env_restored = True

    checks: list[CheckResult] = []
    if not _env_flag(env, "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED", True):
        checks.append(
            CheckResult(
                "memory_config",
                FAIL,
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED must be true for this smoke",
            )
        )
        restore_env()
        return MemorySmokeReport(checks)

    checks.append(CheckResult("memory_config", PASS, "long-term memory smoke enabled"))

    marker = marker_factory() if marker_factory else f"memory-smoke-{uuid.uuid4().hex[:12]}"
    user_id = f"memory_smoke_user_{uuid.uuid4().hex[:12]}"
    session_id = f"memory_smoke_session_{uuid.uuid4().hex[:12]}"
    memory: Any = None

    try:
        memory = memory_factory(env)
        await memory.initialize()
    except ModuleNotFoundError as error:
        checks.append(
            CheckResult(
                "memory_initialize",
                BLOCKED,
                f"{error.name or 'required'} package is not installed",
            )
        )
        restore_env()
        return MemorySmokeReport(checks)
    except Exception as error:
        checks.append(
            CheckResult(
                "memory_initialize",
                FAIL,
                sanitize_text(f"initialization failed: {error.__class__.__name__}", env),
            )
        )
        restore_env()
        return MemorySmokeReport(checks)

    try:
        short_available = bool(getattr(memory.short_term, "available", False))
        long_available = bool(getattr(memory.long_term, "available", False))
        checks.append(
            CheckResult(
                "short_term_redis",
                PASS if short_available else FAIL,
                "Redis short-term memory available" if short_available else "Redis short-term memory unavailable",
            )
        )
        checks.append(
            CheckResult(
                "long_term_milvus",
                PASS if long_available else FAIL,
                "Milvus long-term memory available" if long_available else "Milvus long-term memory unavailable",
            )
        )
        if not short_available or not long_available:
            return MemorySmokeReport(checks)

        await memory.save_conversation(user_id, session_id, _synthetic_messages(marker))
        recent = await memory.get_recent_messages(user_id, session_id)
        if len(recent) >= 4:
            checks.append(CheckResult("short_term_roundtrip", PASS, f"saved and loaded {len(recent)} messages"))
        else:
            checks.append(CheckResult("short_term_roundtrip", FAIL, f"expected >=4 messages, got {len(recent)}"))
            return MemorySmokeReport(checks)

        extracted = await memory.background_extract(
            user_id,
            session_id,
            _SyntheticLLM(marker),
            request_id="memory_smoke",
            user_id_hash="memory_smoke_hash",
        )
        if any(marker in item for item in extracted):
            checks.append(CheckResult("background_extract", PASS, f"saved {len(extracted)} synthetic preference(s)"))
        else:
            checks.append(CheckResult("background_extract", FAIL, "synthetic preference was not extracted"))
            return MemorySmokeReport(checks)

        retrieved, attempts = await _wait_for_retrieval(
            memory,
            user_id=user_id,
            marker=marker,
            timeout_seconds=retrieval_timeout,
        )
        if any(marker in item for item in retrieved):
            checks.append(
                CheckResult(
                    "long_term_retrieval",
                    PASS,
                    f"retrieved synthetic preference after {attempts} attempt(s)",
                )
            )
        else:
            checks.append(
                CheckResult(
                    "long_term_retrieval",
                    FAIL,
                    f"synthetic preference not retrieved after {attempts} attempt(s)",
                )
            )

        cleanup_details = []
        try:
            await memory.short_term.clear(user_id, session_id)
            cleanup_details.append("short-term cleared")
        except Exception as error:
            checks.append(
                CheckResult(
                    "cleanup",
                    DEGRADED,
                    sanitize_text(f"short-term cleanup failed: {error.__class__.__name__}", env),
                )
            )
            return MemorySmokeReport(checks)

        try:
            cleanup_details.append(await _cleanup_long_term_user(memory, user_id))
        except Exception as error:
            checks.append(
                CheckResult(
                    "cleanup",
                    DEGRADED,
                    sanitize_text(f"long-term cleanup failed: {error.__class__.__name__}", env),
                )
            )
        else:
            checks.append(CheckResult("cleanup", PASS, ", ".join(cleanup_details)))

        return MemorySmokeReport(checks)
    finally:
        if memory is not None:
            try:
                await memory.close()
            except Exception:
                pass
        restore_env()


def format_text(report: MemorySmokeReport) -> str:
    labels = {
        PASS: "PASS",
        DEGRADED: "DEGRADED",
        BLOCKED: "BLOCKED",
        FAIL: "FAIL",
    }
    lines = [
        f"[memory-smoke] cloud_agent memory e2e smoke: {report.status}",
        (
            "[memory-smoke] summary: "
            f"passed={report.summary['passed']} "
            f"degraded={report.summary['degraded']} "
            f"blocked={report.summary['blocked']} "
            f"failed={report.summary['failed']}"
        ),
    ]
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name} - {check.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: MemorySmokeReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, report: MemorySmokeReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_json(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent Redis/Milvus memory E2E smoke")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file to load")
    parser.add_argument("--retrieval-timeout", type=float, default=8.0, help="Milvus retrieval wait seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for blocked/degraded checks")
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "memory-e2e-smoke.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write a JSON artifact")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env = merge_env(args.env_file)
    except OSError as error:
        sys.stderr.write(f"[memory-smoke] failed to read env file: {error}\n")
        return 2

    report = await run_memory_smoke(env=env, retrieval_timeout=args.retrieval_timeout)
    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report))
    return report.exit_code(strict=args.strict)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
