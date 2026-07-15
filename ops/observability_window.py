#!/usr/bin/env python3
"""Record a bounded, privacy-safe Cloud Agent observability window.

The monitor stores only check states, Prometheus result counts, and firing
alert counts. It never persists credentials, metric labels, chat content, or
Prometheus response bodies.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PASS = "pass"
FAIL = "fail"
RUNNING = "running"
READY = "ready"
DEGRADED = "degraded"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _request(url: str, *, timeout: int = 15) -> tuple[int, bytes]:
    with urlopen(Request(url), timeout=timeout) as response:
        return int(response.status), response.read()


def _http_check(url: str) -> None:
    status, _ = _request(url)
    if status >= 400:
        raise RuntimeError("http status is not successful")


def _prometheus_result_count(base_url: str, promql: str, minimum: int = 0) -> int:
    query = urlencode({"query": promql})
    _, body = _request(f"{base_url.rstrip('/')}/api/v1/query?{query}")
    payload = json.loads(body.decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError("prometheus query did not return success")
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise RuntimeError("prometheus query returned invalid result")
    count = len(result)
    if count < minimum:
        raise RuntimeError("prometheus result count below minimum")
    return count


def _firing_alert_count(base_url: str) -> int:
    _, body = _request(f"{base_url.rstrip('/')}/api/v1/alerts")
    payload = json.loads(body.decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError("prometheus alerts query did not return success")
    data = payload.get("data")
    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list):
        raise RuntimeError("prometheus alerts query returned invalid result")
    return sum(isinstance(alert, dict) and alert.get("state") == "firing" for alert in alerts)


def _check(checks: dict[str, str], name: str, action) -> None:
    try:
        action()
        checks[name] = PASS
    except Exception:  # Response data and error messages are intentionally not persisted.
        checks[name] = FAIL


def collect_sample(args: argparse.Namespace) -> dict[str, object]:
    checks: dict[str, str] = {}
    base_url = args.base_url.rstrip("/")
    prometheus_url = args.prometheus_url.rstrip("/")
    grafana_url = args.grafana_url.rstrip("/")

    _check(checks, "healthz", lambda: _http_check(f"{base_url}/healthz"))
    _check(checks, "readyz", lambda: _http_check(f"{base_url}/readyz"))
    _check(checks, "prometheus_ready", lambda: _http_check(f"{prometheus_url}/-/ready"))
    _check(
        checks,
        "target_up",
        lambda: _prometheus_result_count(prometheus_url, 'up{job="cloud_agent"}', minimum=1),
    )
    _check(
        checks,
        "llm_metric",
        lambda: _prometheus_result_count(
            prometheus_url,
            "cloud_agent_llm_call_total or cloud_agent_llm_estimated_cost_usd_total",
            minimum=1 if args.require_llm_metric else 0,
        ),
    )
    _check(
        checks,
        "tool_metric",
        lambda: _prometheus_result_count(
            prometheus_url,
            "cloud_agent_tool_call_total or cloud_agent_mcp_registry_initialize_total",
            minimum=1 if args.require_tool_metric else 0,
        ),
    )
    _check(checks, "grafana_health", lambda: _http_check(f"{grafana_url}/api/health"))

    firing_alert_count = 0
    try:
        firing_alert_count = _firing_alert_count(prometheus_url)
        checks["prometheus_alerts"] = PASS
    except Exception:
        checks["prometheus_alerts"] = FAIL

    status = PASS if all(value == PASS for value in checks.values()) and not firing_alert_count else DEGRADED
    return {
        "timestamp_utc": _timestamp(),
        "status": status,
        "checks": checks,
        "firing_alert_count": firing_alert_count,
    }


def create_report(args: argparse.Namespace, started_at: datetime) -> dict[str, object]:
    planned_end = started_at + timedelta(hours=args.duration_hours)
    return {
        "status": RUNNING,
        "started_at_utc": _timestamp(started_at),
        "planned_end_at_utc": _timestamp(planned_end),
        "interval_seconds": args.interval_seconds,
        "last_sample_at_utc": None,
        "last_sample_status": None,
        "last_firing_alert_count": 0,
        "summary": {
            "sample_count": 0,
            "healthy_samples": 0,
            "degraded_samples": 0,
            "alerting_samples": 0,
            "check_failures": {},
        },
    }


def record_sample(report: dict[str, object], sample: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    checks = sample["checks"]
    assert isinstance(checks, dict)

    summary["sample_count"] = int(summary["sample_count"]) + 1
    if sample["status"] == PASS:
        summary["healthy_samples"] = int(summary["healthy_samples"]) + 1
    else:
        summary["degraded_samples"] = int(summary["degraded_samples"]) + 1
    if int(sample["firing_alert_count"]) > 0:
        summary["alerting_samples"] = int(summary["alerting_samples"]) + 1

    failures = summary["check_failures"]
    assert isinstance(failures, dict)
    for name, status in checks.items():
        if status == FAIL:
            failures[name] = int(failures.get(name, 0)) + 1

    report["last_sample_at_utc"] = sample["timestamp_utc"]
    report["last_sample_status"] = sample["status"]
    report["last_firing_alert_count"] = sample["firing_alert_count"]


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def _append_sample(path: Path, sample: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_window(args: argparse.Namespace) -> dict[str, object]:
    started_at = _utc_now()
    deadline = time.monotonic() + args.duration_hours * 3600
    report = create_report(args, started_at)
    summary_path = args.output_dir / "summary.json"
    samples_path = args.output_dir / "samples.jsonl"

    while True:
        sample = collect_sample(args)
        _append_sample(samples_path, sample)
        record_sample(report, sample)
        _write_json(summary_path, report)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval_seconds, remaining))

    summary = report["summary"]
    assert isinstance(summary, dict)
    report["status"] = READY if not summary["degraded_samples"] else DEGRADED
    _write_json(summary_path, report)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(description="Record a Cloud Agent observability window")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000")
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--require-llm-metric", action="store_true")
    parser.add_argument("--require-tool-metric", action="store_true")
    parser.add_argument("--fail-on-degraded", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print an existing summary without polling")
    parser.add_argument("--json", action="store_true", help="Print only the safe aggregate summary")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_repo_root() / ".codex-run" / "observability-window" / stamp,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    summary_path = args.output_dir / "summary.json"
    if args.status:
        if not summary_path.is_file():
            raise SystemExit("observability window summary does not exist")
        print(summary_path.read_text(encoding="utf-8"), end="")
        return 0
    if args.duration_hours <= 0:
        raise SystemExit("--duration-hours must be positive")
    if args.interval_seconds < 15:
        raise SystemExit("--interval-seconds must be at least 15")

    report = run_window(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(summary_path)
    return 1 if args.fail_on_degraded and report["status"] == DEGRADED else 0


if __name__ == "__main__":
    raise SystemExit(main())
