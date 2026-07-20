import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "release_evidence.py"


def _load_evidence():
    spec = importlib.util.spec_from_file_location("release_evidence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(root: Path, rel_path: str, payload: dict) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_required_ready_artifacts(root: Path) -> None:
    ready_payloads = {
        ".codex-run/release-gate.json": {
            "status": "ready",
            "summary": {"passed": 8, "failed": 0, "skipped": 0},
        },
        ".codex-run/external-readonly-smoke.json": {
            "status": "ready",
            "summary": {"passed": 7, "degraded": 0, "blocked": 0, "failed": 0},
        },
        ".codex-run/mcp-billing-smoke.json": {
            "status": "ready",
            "summary": {"passed": 4, "degraded": 0, "blocked": 0, "failed": 0},
        },
        ".codex-run/memory-e2e-smoke.json": {
            "status": "ready",
            "summary": {"passed": 7, "degraded": 0, "blocked": 0, "failed": 0},
        },
        ".codex-run/real-idp-smoke.json": {
            "status": "ready",
            "summary": {"passed": 11, "failed": 0},
        },
    }
    for rel_path, payload in ready_payloads.items():
        _write_json(root, rel_path, payload)

    _write_json(
        root,
        "cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json",
        {
            "query": "secret prompt text must not be indexed",
            "response": {"status": 200, "contentType": "text/event-stream; charset=utf-8"},
            "readyz": {"ok": True, "status": 200, "body": {"status": "ready"}},
            "requestMetrics": ["cloud_agent_request_duration_ms_count 1"],
            "degradationMetrics": [],
            "frontendDiagnostics": {
                "consoleMessages": [],
                "pageErrors": [],
                "requestFailures": [],
            },
            "assistantTextPreview": "secret assistant preview must not be indexed",
        },
    )
    report = root / "cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("<html>playwright report</html>", encoding="utf-8")


def _write_observability_summary(root: Path, stamp: str, rows: list[tuple[str, str]]) -> None:
    summary = root / ".acceptance" / stamp / "summary.tsv"
    summary.parent.mkdir(parents=True, exist_ok=True)
    content = ["step\tstatus\tdetail"]
    content.extend(f"{step}\t{status}\tsafe summary" for step, status in rows)
    summary.write_text("\n".join(content) + "\n", encoding="utf-8")


def _write_observability_window_summary(
    root: Path,
    stamp: str,
    status: str,
    *,
    sample_count: int = 3,
    healthy_samples: int = 3,
    degraded_samples: int = 0,
    alerting_samples: int = 0,
    check_failures: dict[str, int] | None = None,
    last_sample_at_utc: str | None = None,
    planned_end_at_utc: str | None = None,
    interval_seconds: int | None = None,
) -> None:
    payload = {
        "status": status,
        "summary": {
            "sample_count": sample_count,
            "healthy_samples": healthy_samples,
            "degraded_samples": degraded_samples,
            "alerting_samples": alerting_samples,
            "check_failures": check_failures or {},
        },
    }
    if last_sample_at_utc is not None:
        payload["last_sample_at_utc"] = last_sample_at_utc
    if planned_end_at_utc is not None:
        payload["planned_end_at_utc"] = planned_end_at_utc
    if interval_seconds is not None:
        payload["interval_seconds"] = interval_seconds
    _write_json(
        root,
        f".codex-run/observability-window/{stamp}/summary.json",
        payload,
    )


def test_release_evidence_ready_report_summarizes_required_artifacts_without_body_text(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)

    report = evidence.build_report(tmp_path)
    rendered = evidence.format_json(report) + evidence.format_markdown(report)

    assert report["status"] == evidence.READY
    assert report["summary"]["required"] == 7
    assert report["summary"]["required_passed"] == 7
    assert report["summary"]["required_missing"] == 0
    assert report["summary"]["required_failed"] == 0
    assert "secret prompt text" not in rendered
    assert "secret assistant preview" not in rendered
    assert "text/event-stream" in rendered
    assert "release-gate.json" in rendered


def test_release_evidence_marks_missing_required_artifacts_incomplete(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    (tmp_path / ".codex-run" / "memory-e2e-smoke.json").unlink()

    report = evidence.build_report(tmp_path)

    assert report["status"] == evidence.INCOMPLETE
    assert report["summary"]["required_missing"] == 1
    assert any(
        item["name"] == "memory_e2e" and item["status"] == evidence.MISSING
        for item in report["artifacts"]
    )


def test_release_evidence_marks_failed_required_artifacts_failed(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_json(
        tmp_path,
        ".codex-run/release-gate.json",
        {"status": "failed", "summary": {"passed": 6, "failed": 2, "skipped": 0}},
    )

    report = evidence.build_report(tmp_path)

    assert report["status"] == evidence.FAILED
    assert report["summary"]["required_failed"] == 1


def test_release_evidence_writes_json_and_markdown_artifacts(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    report = evidence.build_report(tmp_path)
    json_path = tmp_path / ".codex-run" / "release-evidence.json"
    markdown_path = tmp_path / ".codex-run" / "release-evidence.md"

    evidence.write_artifacts(json_path, markdown_path, report)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["status"] == "ready"
    assert "# Cloud Agent Release Evidence" in markdown
    assert "| Artifact | Required | Status |" in markdown


def test_release_evidence_parses_observability_summary_without_copying_details(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_summary(
        tmp_path,
        "20260713T150000Z",
        [("prometheus_ready", "PASS"), ("grafana_dashboard", "BLOCKED")],
    )

    report = evidence.build_report(tmp_path)
    item = next(item for item in report["artifacts"] if item["name"].startswith("observability_"))

    assert report["status"] == evidence.READY
    assert item["required"] is False
    assert item["status"] == evidence.BLOCKED
    assert item["summary"] == {
        "steps": 2,
        "passed": 1,
        "failed": 0,
        "blocked": 1,
        "invalid": 0,
    }
    assert "safe summary" not in evidence.format_json(report)


def test_release_evidence_strict_observability_uses_only_latest_passing_summary(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_summary(tmp_path, "20260713T140000Z", [("healthz", "FAIL")])
    _write_observability_summary(tmp_path, "20260713T150000Z", [("healthz", "PASS")])

    report = evidence.build_report(tmp_path, require_observability=True)

    assert report["status"] == evidence.READY
    assert report["summary"]["required"] == 8
    assert report["summary"]["required_passed"] == 8
    assert report["summary"]["required_blocked"] == 0
    assert not any(
        item["name"] == "observability_acceptance_20260713T140000Z"
        for item in report["artifacts"]
    )


def test_release_evidence_strict_observability_marks_blocked_or_missing_incomplete(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)

    missing_report = evidence.build_report(tmp_path, require_observability=True)
    assert missing_report["status"] == evidence.INCOMPLETE
    assert missing_report["summary"]["required_missing"] == 1

    _write_observability_summary(tmp_path, "20260713T150000Z", [("grafana_dashboard", "BLOCKED")])
    blocked_report = evidence.build_report(tmp_path, require_observability=True)
    assert blocked_report["status"] == evidence.INCOMPLETE
    assert blocked_report["summary"]["required_blocked"] == 1


def test_release_evidence_strict_window_requires_completed_healthy_latest_window(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_window_summary(tmp_path, "20260714T010000Z", "running")

    running_report = evidence.build_report(tmp_path, require_observability_window=True)
    assert running_report["status"] == evidence.INCOMPLETE
    assert running_report["summary"]["required_blocked"] == 1

    _write_observability_window_summary(tmp_path, "20260714T020000Z", "ready")
    ready_report = evidence.build_report(tmp_path, require_observability_window=True)
    window_item = next(
        item for item in ready_report["artifacts"] if item["name"].startswith("observability_window_")
    )

    assert ready_report["status"] == evidence.READY
    assert window_item["status"] == evidence.PASS
    assert window_item["summary"] == {
        "sample_count": 3,
        "healthy_samples": 3,
        "degraded_samples": 0,
        "alerting_samples": 0,
        "check_failure_total": 0,
    }


def test_release_evidence_strict_window_marks_degraded_window_failed(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_window_summary(
        tmp_path,
        "20260714T010000Z",
        "degraded",
        healthy_samples=2,
        degraded_samples=1,
        alerting_samples=1,
        check_failures={"target_up": 1},
    )

    report = evidence.build_report(tmp_path, require_observability_window=True)

    assert report["status"] == evidence.FAILED
    assert report["summary"]["required_failed"] == 1


def test_release_evidence_marks_stale_running_window_failed(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_window_summary(
        tmp_path,
        "20260714T010000Z",
        "running",
        last_sample_at_utc="2026-07-14T01:00:00Z",
        planned_end_at_utc="2026-07-15T01:00:00Z",
        interval_seconds=300,
    )

    report = evidence.build_report(
        tmp_path,
        require_observability_window=True,
        now_utc=datetime(2026, 7, 14, 1, 12, tzinfo=timezone.utc),
    )
    window_item = next(
        item for item in report["artifacts"] if item["name"].startswith("observability_window_")
    )

    assert report["status"] == evidence.FAILED
    assert window_item["status"] == evidence.FAILED
    assert window_item["summary"]["reason"] == "monitor_stale"


def test_release_evidence_marks_overdue_running_window_failed(tmp_path):
    evidence = _load_evidence()
    _write_required_ready_artifacts(tmp_path)
    _write_observability_window_summary(
        tmp_path,
        "20260714T010000Z",
        "running",
        last_sample_at_utc="2026-07-15T00:59:00Z",
        planned_end_at_utc="2026-07-15T01:00:00Z",
        interval_seconds=300,
    )

    report = evidence.build_report(
        tmp_path,
        require_observability_window=True,
        now_utc=datetime(2026, 7, 15, 1, 1, tzinfo=timezone.utc),
    )
    window_item = next(
        item for item in report["artifacts"] if item["name"].startswith("observability_window_")
    )

    assert report["status"] == evidence.FAILED
    assert window_item["status"] == evidence.FAILED
    assert window_item["summary"]["reason"] == "monitor_overdue"
