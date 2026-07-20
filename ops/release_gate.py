#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"

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


@dataclass(frozen=True)
class StepSpec:
    name: str
    command: tuple[str, ...]
    pass_exit_codes: tuple[int, ...] = (0,)
    artifact: Path | None = None
    capture_json_artifact: bool = False
    success_detail: str | None = None


@dataclass(frozen=True)
class CommandOutput:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class StepResult:
    def __init__(
        self,
        *,
        name: str,
        status: str,
        command: list[str],
        exit_code: int | None,
        duration_ms: int,
        detail: str,
        artifact: str | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.command = command
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.detail = detail
        self.artifact = artifact

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "status": self.status,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }
        if self.artifact:
            payload["artifact"] = self.artifact
        return payload


class ReleaseGateReport:
    def __init__(self, steps: list[StepResult]):
        self.steps = steps

    @property
    def summary(self) -> dict[str, int]:
        return {
            "passed": sum(1 for step in self.steps if step.status == PASS),
            "failed": sum(1 for step in self.steps if step.status == FAIL),
            "skipped": sum(1 for step in self.steps if step.status == SKIPPED),
        }

    @property
    def status(self) -> str:
        if self.summary["failed"]:
            return "failed"
        if self.summary["skipped"]:
            return "incomplete"
        return "ready"

    def exit_code(self) -> int:
        return 1 if self.summary["failed"] else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
        }


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
    rendered = re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+", "jwt-<redacted>", rendered)
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


def _truncate(text: str, limit: int = 500) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...<truncated>"


def _format_command(command: Sequence[str]) -> list[str]:
    rendered = []
    for item in command:
        if item == sys.executable:
            rendered.append("python")
        else:
            rendered.append(item)
    return rendered


def subprocess_runner(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
) -> CommandOutput:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return CommandOutput(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_json_output(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _json_detail(payload: dict[str, object]) -> str:
    status = payload.get("status")
    summary = payload.get("summary")
    if isinstance(summary, dict):
        parts = [f"{key}={value}" for key, value in summary.items()]
        return f"status={status}, " + ", ".join(parts)
    return f"status={status}"


def _write_json_artifact(path: Path, text: str) -> None:
    payload = _parse_json_output(text)
    if payload is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_step(
    spec: StepSpec,
    *,
    env: dict[str, str],
    cwd: Path,
    timeout: float,
    runner: Callable[[Sequence[str]], CommandOutput],
) -> StepResult:
    started = time.monotonic()
    display_command = _format_command(spec.command)
    try:
        output = runner(spec.command)
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        return StepResult(
            name=spec.name,
            status=FAIL,
            command=display_command,
            exit_code=None,
            duration_ms=duration_ms,
            detail=f"timed out after {timeout:g}s",
            artifact=str(spec.artifact) if spec.artifact else None,
        )
    except OSError as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        return StepResult(
            name=spec.name,
            status=FAIL,
            command=display_command,
            exit_code=None,
            duration_ms=duration_ms,
            detail=sanitize_text(error.__class__.__name__, env),
            artifact=str(spec.artifact) if spec.artifact else None,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    if spec.capture_json_artifact and spec.artifact:
        _write_json_artifact(cwd / spec.artifact, output.stdout)

    passed = output.exit_code in spec.pass_exit_codes
    if passed:
        payload = _parse_json_output(output.stdout)
        detail = spec.success_detail or (
            _json_detail(payload) if payload is not None else f"exit_code={output.exit_code}"
        )
        return StepResult(
            name=spec.name,
            status=PASS,
            command=display_command,
            exit_code=output.exit_code,
            duration_ms=duration_ms,
            detail=sanitize_text(detail, env),
            artifact=str(spec.artifact) if spec.artifact else None,
        )

    detail_source = output.stdout if output.stdout.strip() else output.stderr
    detail = _truncate(sanitize_text(detail_source or f"exit_code={output.exit_code}", env))

    return StepResult(
        name=spec.name,
        status=FAIL,
        command=display_command,
        exit_code=output.exit_code,
        duration_ms=duration_ms,
        detail=detail,
        artifact=str(spec.artifact) if spec.artifact else None,
    )


def _skipped_step(spec: StepSpec, reason: str) -> StepResult:
    return StepResult(
        name=spec.name,
        status=SKIPPED,
        command=_format_command(spec.command),
        exit_code=None,
        duration_ms=0,
        detail=reason,
        artifact=str(spec.artifact) if spec.artifact else None,
    )


def build_steps(
    *,
    env_file: Path,
    backend_url: str,
    frontend_url: str | None,
    strict: bool,
    skip_llm_call: bool,
) -> list[StepSpec]:
    python = sys.executable
    codex_run = Path(".codex-run")
    doctor_command: list[str] = [
        python,
        "ops/cloud_agent_doctor.py",
        "--env-file",
        str(env_file),
        "--base-url",
        backend_url,
        "--json",
    ]
    if strict:
        doctor_command.append("--strict")

    sse_command = [
        python,
        "ops/chat_sse_smoke.py",
        "--backend-url",
        backend_url,
    ]
    if frontend_url:
        sse_command.extend(["--frontend-url", frontend_url])

    external_command: list[str] = [
        python,
        "ops/external_dependency_readonly_smoke.py",
        "--env-file",
        str(env_file),
        "--json",
        "--artifact",
        str(codex_run / "external-readonly-smoke.json"),
    ]
    if strict:
        external_command.append("--strict")
    if skip_llm_call:
        external_command.append("--skip-llm-call")

    mcp_billing_command: list[str] = [
        python,
        "ops/mcp_billing_readonly_smoke.py",
        "--env-file",
        str(env_file),
        "--json",
        "--artifact",
        str(codex_run / "mcp-billing-smoke.json"),
    ]
    if strict:
        mcp_billing_command.append("--strict")

    memory_command: list[str] = [
        python,
        "ops/memory_e2e_smoke.py",
        "--env-file",
        str(env_file),
        "--json",
        "--artifact",
        str(codex_run / "memory-e2e-smoke.json"),
    ]
    if strict:
        memory_command.append("--strict")

    return [
        StepSpec(
            name="deployment_doctor",
            command=tuple(doctor_command),
            artifact=codex_run / "release-doctor.json",
            capture_json_artifact=True,
        ),
        StepSpec(
            name="chat_sse",
            command=tuple(sse_command),
            success_detail="SSE contract passed",
        ),
        StepSpec(
            name="external_dependencies",
            command=tuple(external_command),
            artifact=codex_run / "external-readonly-smoke.json",
        ),
        StepSpec(
            name="mcp_billing_readonly",
            command=tuple(mcp_billing_command),
            artifact=codex_run / "mcp-billing-smoke.json",
        ),
        StepSpec(
            name="memory_e2e",
            command=tuple(memory_command),
            artifact=codex_run / "memory-e2e-smoke.json",
        ),
        StepSpec(
            name="auth_idp",
            command=(
                python,
                "ops/auth/real_idp_smoke.py",
                "--env-file",
                str(env_file),
                "--json",
                "--artifact",
                str(codex_run / "real-idp-smoke.json"),
            ),
            artifact=codex_run / "real-idp-smoke.json",
        ),
        StepSpec(
            name="diff_check",
            command=("git", "diff", "--check"),
            success_detail="no whitespace errors",
        ),
        StepSpec(
            name="secret_scan",
            command=(python, "ops/secret_scan.py"),
            success_detail="no real OpenAI-style secret pattern found",
        ),
    ]


def run_release_gate(
    *,
    env_file: Path,
    backend_url: str = "http://127.0.0.1:5000",
    frontend_url: str | None = None,
    strict: bool = False,
    skip_llm_call: bool = False,
    timeout: float = 180.0,
    dry_run: bool = False,
    skip_steps: set[str] | None = None,
    runner: Callable[[Sequence[str]], CommandOutput] | None = None,
    process_env: dict[str, str] | None = None,
) -> ReleaseGateReport:
    cwd = _repo_root()
    env = merge_env(env_file, process_env)
    skip_steps = skip_steps or set()
    specs = build_steps(
        env_file=env_file,
        backend_url=backend_url,
        frontend_url=frontend_url,
        strict=strict,
        skip_llm_call=skip_llm_call,
    )

    if runner is None:
        runner = lambda command: subprocess_runner(command, cwd=cwd, timeout=timeout)

    steps: list[StepResult] = []
    for spec in specs:
        if spec.name in skip_steps:
            steps.append(_skipped_step(spec, "skipped by CLI flag"))
            continue
        if dry_run:
            steps.append(_skipped_step(spec, "dry run"))
            continue
        steps.append(_run_step(spec, env=env, cwd=cwd, timeout=timeout, runner=runner))

    return ReleaseGateReport(steps)


def format_text(report: ReleaseGateReport) -> str:
    lines = [
        f"[release-gate] cloud_agent release gate: {report.status}",
        (
            "[release-gate] summary: "
            f"passed={report.summary['passed']} "
            f"failed={report.summary['failed']} "
            f"skipped={report.summary['skipped']}"
        ),
    ]
    labels = {PASS: "PASS", FAIL: "FAIL", SKIPPED: "SKIPPED"}
    for step in report.steps:
        lines.append(f"[{labels[step.status]}] {step.name} - {step.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: ReleaseGateReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, report: ReleaseGateReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_json(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent release gate aggregator")
    parser.add_argument("--env-file", type=Path, default=Path("ops/cloud_agent.env"))
    parser.add_argument("--backend-url", default="http://127.0.0.1:5000")
    parser.add_argument("--frontend-url", default="", help="Optional frontend URL for proxied SSE smoke")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-step timeout seconds")
    parser.add_argument("--strict", action="store_true", help="Fail on degraded downstream smoke checks")
    parser.add_argument("--skip-llm-call", action="store_true", help="Pass through to external dependency smoke")
    parser.add_argument("--dry-run", action="store_true", help="Print planned steps without executing commands")
    parser.add_argument("--skip-doctor", action="store_true")
    parser.add_argument("--skip-sse", action="store_true")
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--skip-mcp-billing", action="store_true")
    parser.add_argument("--skip-memory", action="store_true")
    parser.add_argument("--skip-idp", action="store_true")
    parser.add_argument("--skip-hygiene", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "release-gate.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write a JSON artifact")
    return parser.parse_args(argv)


def _skip_steps_from_args(args: argparse.Namespace) -> set[str]:
    skipped: set[str] = set()
    if args.skip_doctor:
        skipped.add("deployment_doctor")
    if args.skip_sse:
        skipped.add("chat_sse")
    if args.skip_external:
        skipped.add("external_dependencies")
    if args.skip_mcp_billing:
        skipped.add("mcp_billing_readonly")
    if args.skip_memory:
        skipped.add("memory_e2e")
    if args.skip_idp:
        skipped.add("auth_idp")
    if args.skip_hygiene:
        skipped.update({"diff_check", "secret_scan"})
    return skipped


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run_release_gate(
            env_file=args.env_file,
            backend_url=args.backend_url,
            frontend_url=args.frontend_url or None,
            strict=args.strict,
            skip_llm_call=args.skip_llm_call,
            timeout=args.timeout,
            dry_run=args.dry_run,
            skip_steps=_skip_steps_from_args(args),
        )
    except OSError as error:
        sys.stderr.write(f"[release-gate] failed to read env file: {error}\n")
        return 2

    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
