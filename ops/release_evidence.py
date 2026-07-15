#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


READY = "ready"
INCOMPLETE = "incomplete"
FAILED = "failed"
RUNNING = "running"
DEGRADED = "degraded"
PASS = "pass"
MISSING = "missing"
BLOCKED = "blocked"
DEFAULT_OBSERVABILITY_INTERVAL_SECONDS = 300
OBSERVABILITY_STALE_GRACE_SECONDS = 60

CORE_JSON_ARTIFACTS = (
    ("release_gate", ".codex-run/release-gate.json"),
    ("external_dependencies", ".codex-run/external-readonly-smoke.json"),
    ("mcp_billing_readonly", ".codex-run/mcp-billing-smoke.json"),
    ("memory_e2e", ".codex-run/memory-e2e-smoke.json"),
    ("auth_idp", ".codex-run/real-idp-smoke.json"),
)

CORE_BROWSER_ARTIFACTS = (
    (
        "real_backend_browser_diagnostics",
        "cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json",
    ),
    (
        "real_backend_browser_report",
        "cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html",
    ),
)

OPTIONAL_ARTIFACTS = (
    ("compose_doctor", ".codex-run/compose-doctor.json"),
    ("compose_cloud_agent_log", ".codex-run/compose-cloud-agent.log"),
    ("compose_all_log", ".codex-run/compose-all.log"),
    ("compose_ps_log", ".codex-run/compose-ps.log"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sanitize_text(text: object) -> str:
    rendered = str(text)
    rendered = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-<redacted>", rendered)
    rendered = re.sub(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+",
        "jwt-<redacted>",
        rendered,
    )
    rendered = re.sub(
        r"(?i)((api[_-]?key|token|secret|password)\s*=\s*)[^\s,;]+",
        r"\1<redacted>",
        rendered,
    )
    return rendered


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_metadata(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "sha256": _sha256(path),
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_status(payload: dict[str, Any] | None) -> tuple[str, dict[str, object]]:
    if payload is None:
        return FAILED, {"error": "invalid JSON"}
    status = str(payload.get("status", "")).lower()
    summary = payload.get("summary")
    if status == READY:
        return PASS, summary if isinstance(summary, dict) else {"status": status}
    if status:
        return FAILED, summary if isinstance(summary, dict) else {"status": status}
    return FAILED, {"error": "missing status"}


def _browser_diagnostics_status(payload: dict[str, Any] | None) -> tuple[str, dict[str, object]]:
    if payload is None:
        return FAILED, {"error": "invalid JSON"}

    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    readyz = payload.get("readyz") if isinstance(payload.get("readyz"), dict) else {}
    frontend = (
        payload.get("frontendDiagnostics")
        if isinstance(payload.get("frontendDiagnostics"), dict)
        else {}
    )
    console_count = len(frontend.get("consoleMessages") or [])
    page_error_count = len(frontend.get("pageErrors") or [])
    request_failure_count = len(frontend.get("requestFailures") or [])
    metrics_count = len(payload.get("requestMetrics") or [])
    degradation_count = len(payload.get("degradationMetrics") or [])

    response_status = int(response.get("status") or 0)
    content_type = str(response.get("contentType") or "")
    readyz_ok = bool(readyz.get("ok"))
    passed = (
        200 <= response_status < 300
        and "text/event-stream" in content_type
        and readyz_ok
        and console_count == 0
        and page_error_count == 0
        and request_failure_count == 0
    )
    summary = {
        "response_status": response_status,
        "content_type": content_type,
        "readyz_ok": readyz_ok,
        "request_metrics": metrics_count,
        "degradation_metrics": degradation_count,
        "console_messages": console_count,
        "page_errors": page_error_count,
        "request_failures": request_failure_count,
    }
    return (PASS if passed else FAILED), summary


def _build_missing(name: str, rel_path: str, required: bool) -> dict[str, object]:
    return {
        "name": name,
        "path": rel_path,
        "required": required,
        "present": False,
        "status": MISSING,
        "summary": {"reason": "artifact not found"},
    }


def _build_json_item(
    *,
    repo_root: Path,
    name: str,
    rel_path: str,
    required: bool,
    browser_diagnostics: bool = False,
) -> dict[str, object]:
    path = repo_root / rel_path
    if not path.exists():
        return _build_missing(name, rel_path, required)

    payload = _load_json(path)
    if browser_diagnostics:
        status, summary = _browser_diagnostics_status(payload)
    else:
        status, summary = _json_status(payload)

    item: dict[str, object] = {
        "name": name,
        "path": rel_path,
        "required": required,
        "present": True,
        "status": status,
        "summary": summary,
    }
    item.update(_artifact_metadata(path))
    return item


def _build_file_item(*, repo_root: Path, name: str, rel_path: str, required: bool) -> dict[str, object]:
    path = repo_root / rel_path
    if not path.exists():
        return _build_missing(name, rel_path, required)
    item: dict[str, object] = {
        "name": name,
        "path": rel_path,
        "required": required,
        "present": True,
        "status": PASS,
        "summary": {"artifact": "present"},
    }
    item.update(_artifact_metadata(path))
    return item


def _observability_summary_status(path: Path) -> tuple[str, dict[str, object]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fields = set(reader.fieldnames or [])
            if not {"step", "status", "detail"}.issubset(fields):
                return FAILED, {"error": "invalid acceptance summary header"}

            counts = {"passed": 0, "failed": 0, "blocked": 0, "invalid": 0}
            for row in reader:
                step = str(row.get("step") or "").strip()
                status = str(row.get("status") or "").strip().upper()
                if not step or status not in {"PASS", "FAIL", "BLOCKED"}:
                    counts["invalid"] += 1
                elif status == "PASS":
                    counts["passed"] += 1
                elif status == "FAIL":
                    counts["failed"] += 1
                else:
                    counts["blocked"] += 1
    except OSError:
        return FAILED, {"error": "unable to read acceptance summary"}

    summary = {"steps": sum(counts.values()), **counts}
    if summary["steps"] == 0:
        return FAILED, {"error": "acceptance summary has no steps", **summary}
    if counts["invalid"] or counts["failed"]:
        return FAILED, summary
    if counts["blocked"]:
        return BLOCKED, summary
    return PASS, summary


def _build_observability_item(
    *, repo_root: Path, path: Path, required: bool
) -> dict[str, object]:
    rel_path = path.relative_to(repo_root).as_posix()
    status, summary = _observability_summary_status(path)
    item: dict[str, object] = {
        "name": f"observability_acceptance_{path.parent.name}",
        "path": rel_path,
        "required": required,
        "present": True,
        "status": status,
        "summary": summary,
    }
    item.update(_artifact_metadata(path))
    return item


def _observability_items(repo_root: Path, *, require_latest: bool) -> list[dict[str, object]]:
    acceptance_root = repo_root / ".acceptance"
    paths = sorted(
        acceptance_root.glob("*/summary.tsv"),
        key=lambda path: (path.stat().st_mtime, path.parent.name),
    )
    if not paths:
        if require_latest:
            return [
                _build_missing(
                    "observability_acceptance",
                    ".acceptance/<timestamp>/summary.tsv",
                    required=True,
                )
            ]
        return []

    latest = paths[-1]
    return [
        _build_observability_item(
            repo_root=repo_root,
            path=latest,
            required=require_latest,
        )
    ]


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _observability_window_summary_status(
    path: Path,
    *,
    now_utc: datetime | None = None,
) -> tuple[str, dict[str, object]]:
    payload = _load_json(path)
    if payload is None:
        return FAILED, {"error": "invalid observability window summary"}

    status = str(payload.get("status") or "").lower()
    raw_summary = payload.get("summary")
    if not isinstance(raw_summary, dict):
        return FAILED, {"error": "missing observability window summary"}

    count_names = (
        "sample_count",
        "healthy_samples",
        "degraded_samples",
        "alerting_samples",
    )
    try:
        counts = {name: int(raw_summary[name]) for name in count_names}
    except (KeyError, TypeError, ValueError):
        return FAILED, {"error": "invalid observability window counts"}
    if any(value < 0 for value in counts.values()):
        return FAILED, {"error": "negative observability window count"}
    if counts["healthy_samples"] + counts["degraded_samples"] != counts["sample_count"]:
        return FAILED, {"error": "inconsistent observability window counts"}

    raw_failures = raw_summary.get("check_failures")
    if not isinstance(raw_failures, dict):
        return FAILED, {"error": "invalid observability window failures"}
    try:
        check_failure_total = sum(int(value) for value in raw_failures.values())
    except (TypeError, ValueError):
        return FAILED, {"error": "invalid observability window failure count"}
    if check_failure_total < 0:
        return FAILED, {"error": "negative observability window failure count"}

    summary: dict[str, object] = {**counts, "check_failure_total": check_failure_total}
    if status == RUNNING:
        raw_interval = payload.get("interval_seconds", DEFAULT_OBSERVABILITY_INTERVAL_SECONDS)
        try:
            interval_seconds = int(raw_interval)
        except (TypeError, ValueError):
            return FAILED, {"error": "invalid observability window interval"}
        if interval_seconds < 15:
            return FAILED, {"error": "invalid observability window interval"}

        last_sample_at = _parse_utc_timestamp(payload.get("last_sample_at_utc"))
        planned_end_at = _parse_utc_timestamp(payload.get("planned_end_at_utc"))
        if payload.get("last_sample_at_utc") is not None and last_sample_at is None:
            return FAILED, {"error": "invalid observability window last sample timestamp"}
        if payload.get("planned_end_at_utc") is not None and planned_end_at is None:
            return FAILED, {"error": "invalid observability window planned end timestamp"}

        current_time = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if planned_end_at is not None and current_time >= planned_end_at:
            return FAILED, {
                **summary,
                "reason": "monitor_overdue",
                "planned_end_at_utc": payload["planned_end_at_utc"],
            }
        if last_sample_at is not None:
            stale_after = last_sample_at + timedelta(
                seconds=interval_seconds * 2 + OBSERVABILITY_STALE_GRACE_SECONDS
            )
            if current_time > stale_after:
                return FAILED, {
                    **summary,
                    "reason": "monitor_stale",
                    "last_sample_at_utc": payload["last_sample_at_utc"],
                    "stale_after_utc": stale_after.replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
        return BLOCKED, summary
    if status == READY:
        if (
            counts["sample_count"] == 0
            or counts["degraded_samples"]
            or counts["alerting_samples"]
            or check_failure_total
        ):
            return FAILED, summary
        return PASS, summary
    if status == DEGRADED:
        return FAILED, summary
    return FAILED, {"error": "invalid observability window status"}


def _observability_window_items(
    repo_root: Path,
    *,
    require_latest: bool,
    now_utc: datetime | None = None,
) -> list[dict[str, object]]:
    window_root = repo_root / ".codex-run" / "observability-window"
    paths = sorted(
        window_root.glob("*/summary.json"),
        key=lambda path: (path.stat().st_mtime, path.parent.name),
    )
    if not paths:
        if require_latest:
            return [
                _build_missing(
                    "observability_window",
                    ".codex-run/observability-window/<timestamp>/summary.json",
                    required=True,
                )
            ]
        return []

    path = paths[-1]
    rel_path = path.relative_to(repo_root).as_posix()
    status, summary = _observability_window_summary_status(path, now_utc=now_utc)
    item: dict[str, object] = {
        "name": f"observability_window_{path.parent.name}",
        "path": rel_path,
        "required": require_latest,
        "present": True,
        "status": status,
        "summary": summary,
    }
    item.update(_artifact_metadata(path))
    return [item]


def build_report(
    repo_root: Path | None = None,
    *,
    require_observability: bool = False,
    require_observability_window: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    repo_root = (repo_root or _repo_root()).resolve()
    current_time = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = current_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    items: list[dict[str, object]] = []
    for name, rel_path in CORE_JSON_ARTIFACTS:
        items.append(
            _build_json_item(
                repo_root=repo_root,
                name=name,
                rel_path=rel_path,
                required=True,
            )
        )
    items.append(
        _build_json_item(
            repo_root=repo_root,
            name=CORE_BROWSER_ARTIFACTS[0][0],
            rel_path=CORE_BROWSER_ARTIFACTS[0][1],
            required=True,
            browser_diagnostics=True,
        )
    )
    items.append(
        _build_file_item(
            repo_root=repo_root,
            name=CORE_BROWSER_ARTIFACTS[1][0],
            rel_path=CORE_BROWSER_ARTIFACTS[1][1],
            required=True,
        )
    )
    for name, rel_path in OPTIONAL_ARTIFACTS:
        items.append(_build_file_item(repo_root=repo_root, name=name, rel_path=rel_path, required=False))
    items.extend(_observability_items(repo_root, require_latest=require_observability))
    items.extend(
        _observability_window_items(
            repo_root,
            require_latest=require_observability_window,
            now_utc=current_time,
        )
    )

    required_items = [item for item in items if item["required"]]
    failed_required = [item for item in required_items if item["status"] == FAILED]
    missing_required = [item for item in required_items if item["status"] == MISSING]
    blocked_required = [item for item in required_items if item["status"] == BLOCKED]
    if failed_required:
        status = FAILED
    elif missing_required or blocked_required:
        status = INCOMPLETE
    else:
        status = READY

    return {
        "status": status,
        "generated_at_utc": generated_at,
        "summary": {
            "required": len(required_items),
            "required_passed": sum(1 for item in required_items if item["status"] == PASS),
            "required_missing": len(missing_required),
            "required_failed": len(failed_required),
            "required_blocked": len(blocked_required),
            "optional_present": sum(1 for item in items if not item["required"] and item["present"]),
        },
        "artifacts": items,
    }


def format_json(report: dict[str, object]) -> str:
    return sanitize_text(json.dumps(report, ensure_ascii=False, indent=2)) + "\n"


def _summary_text(summary: object) -> str:
    if not isinstance(summary, dict):
        return ""
    return ", ".join(f"{key}={value}" for key, value in summary.items())


def format_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Cloud Agent Release Evidence",
        "",
        f"- Status: `{report['status']}`",
        f"- Generated UTC: `{report['generated_at_utc']}`",
    ]
    summary = report.get("summary")
    if isinstance(summary, dict):
        lines.append(
            "- Summary: "
            f"required={summary.get('required')}, "
            f"passed={summary.get('required_passed')}, "
            f"missing={summary.get('required_missing')}, "
            f"failed={summary.get('required_failed')}, "
            f"blocked={summary.get('required_blocked')}, "
            f"optional_present={summary.get('optional_present')}"
        )
    lines.extend(
        [
            "",
            "| Artifact | Required | Status | Size | SHA256 | Summary |",
            "|---|---:|---|---:|---|---|",
        ]
    )
    for item in report.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        sha = str(item.get("sha256", ""))
        sha_short = sha[:12] if sha else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.get('path')}`",
                    "yes" if item.get("required") else "no",
                    f"`{item.get('status')}`",
                    str(item.get("size_bytes", "")),
                    sha_short,
                    sanitize_text(_summary_text(item.get("summary"))),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- The index records artifact status, size, timestamp, and hash only.",
            "- It does not copy API keys, JWTs, prompts, completions, order rows, or chat transcripts.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_artifacts(json_path: Path, markdown_path: Path, report: dict[str, object]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(format_json(report), encoding="utf-8")
    markdown_path.write_text(format_markdown(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent release evidence index")
    parser.add_argument("--repo-root", type=Path, default=_repo_root())
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--json-artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "release-evidence.json",
    )
    parser.add_argument(
        "--markdown-artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "release-evidence.md",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write evidence artifacts")
    parser.add_argument(
        "--require-observability",
        action="store_true",
        help="Require the latest .acceptance/*/summary.tsv to have no failed or blocked steps",
    )
    parser.add_argument(
        "--require-observability-window",
        action="store_true",
        help="Require the latest completed 24-hour observability window to be healthy",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(
        args.repo_root,
        require_observability=args.require_observability,
        require_observability_window=args.require_observability_window,
    )
    if not args.no_artifact:
        write_artifacts(args.json_artifact, args.markdown_artifact, report)
    sys.stdout.write(format_json(report) if args.json else format_markdown(report))
    return 0 if report["status"] == READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
