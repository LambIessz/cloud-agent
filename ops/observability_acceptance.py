#!/usr/bin/env python3
"""Cross-platform runtime acceptance for Cloud Agent observability.

The artifact intentionally contains only step status and low-cardinality
counts. It never writes chat bodies, metrics bodies, credentials, or PromQL
sample labels to disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
FORBIDDEN_METRICS_TERMS = (
    "request_id",
    "user_id=",
    "user_id_hash",
    "tenant_id=",
    "session_id",
    "thread_id",
    "conversation_id",
    "prompt=",
    "completion=",
    "query=",
    "matched_question",
)


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 15,
) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=headers or {}, method=method)
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read()


def _http_status_detail(url: str) -> str:
    status, _ = _request(url)
    if status >= 400:
        raise RuntimeError("http status is not successful")
    return f"http_status={status}"


def metric_family_count(metrics_text: str) -> int:
    lowered = metrics_text.lower()
    if any(term in lowered for term in FORBIDDEN_METRICS_TERMS):
        raise RuntimeError("forbidden metrics field detected")
    families = re.findall(r"^# HELP (cloud_agent_[A-Za-z0-9_]+)", metrics_text, re.MULTILINE)
    return len(set(families))


def _metrics_detail(base_url: str) -> str:
    _, body = _request(f"{base_url.rstrip('/')}/api/metrics")
    return f"metric_family_count={metric_family_count(body.decode('utf-8', errors='replace'))}"


def prometheus_result_count(payload: dict[str, Any], minimum: int = 0) -> int:
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


def _prometheus_detail(base_url: str, promql: str, minimum: int = 0) -> str:
    query = urlencode({"query": promql})
    _, body = _request(f"{base_url.rstrip('/')}/api/v1/query?{query}")
    payload = json.loads(body.decode("utf-8"))
    return f"result_count={prometheus_result_count(payload, minimum)}"


def _chat_smoke_detail(base_url: str, text: str) -> str:
    body = json.dumps(
        {"query": text, "session_id": "observability_acceptance"}
    ).encode("utf-8")
    status, _ = _request(
        f"{base_url.rstrip('/')}/api/chat",
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        body=body,
        timeout=90,
    )
    if status >= 400:
        raise RuntimeError("chat smoke returned non-success status")
    return f"http_status={status}"


def _grafana_dashboard_detail(base_url: str, user: str, password: str) -> str:
    if not user or not password:
        raise ValueError("grafana credentials are not set")
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    query = urlencode({"query": "Cloud Agent Overview"})
    _, body = _request(
        f"{base_url.rstrip('/')}/api/search?{query}",
        headers={"Authorization": f"Basic {token}"},
    )
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("grafana search returned invalid response")
    matches = [item for item in payload if isinstance(item, dict) and item.get("title") == "Cloud Agent Overview"]
    if not matches:
        raise RuntimeError("grafana dashboard was not found")
    return f"dashboard_match_count={len(matches)}"


def _exception_detail(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return f"error_type=HTTPError; status={error.code}"
    if isinstance(error, URLError):
        return "error_type=URLError"
    return f"error_type={type(error).__name__}"


def run_acceptance(args: argparse.Namespace) -> list[StepResult]:
    steps: list[StepResult] = []

    def check(name: str, action) -> None:
        try:
            steps.append(StepResult(name, PASS, action()))
        except Exception as error:  # Every failure is summarized without response data.
            steps.append(StepResult(name, FAIL, _exception_detail(error)))

    check("healthz", lambda: _http_status_detail(f"{args.base_url.rstrip('/')}/healthz"))
    check("readyz", lambda: _http_status_detail(f"{args.base_url.rstrip('/')}/readyz"))
    check("metrics_summary", lambda: _metrics_detail(args.base_url))

    if args.run_chat_smoke:
        check("chat_smoke", lambda: _chat_smoke_detail(args.base_url, args.chat_smoke_text))
        if steps[-1].status == PASS and args.post_chat_wait_seconds:
            time.sleep(args.post_chat_wait_seconds)
        check("metrics_summary_after_chat", lambda: _metrics_detail(args.base_url))
    else:
        steps.append(StepResult("chat_smoke", BLOCKED, "set --run-chat-smoke to generate synthetic traffic"))

    check("prometheus_ready", lambda: _http_status_detail(f"{args.prometheus_url.rstrip('/')}/-/ready"))
    check(
        "prometheus_up_cloud_agent",
        lambda: _prometheus_detail(args.prometheus_url, 'up{job="cloud_agent"}', minimum=1),
    )
    check(
        "prometheus_request_metric",
        lambda: _prometheus_detail(
            args.prometheus_url,
            "cloud_agent_request_total",
            minimum=1 if args.run_chat_smoke else 0,
        ),
    )
    check(
        "prometheus_llm_metric",
        lambda: _prometheus_detail(
            args.prometheus_url,
            "cloud_agent_llm_call_total or cloud_agent_llm_estimated_cost_usd_total",
            minimum=1 if args.require_llm_metric else 0,
        ),
    )
    check(
        "prometheus_tool_metric",
        lambda: _prometheus_detail(
            args.prometheus_url,
            (
                "cloud_agent_tool_call_total"
                if args.require_tool_metric
                else "cloud_agent_tool_call_total or cloud_agent_mcp_registry_initialize_total"
            ),
            minimum=1 if args.require_tool_metric else 0,
        ),
    )
    check(
        "prometheus_cache_benefit_metric",
        lambda: _prometheus_detail(
            args.prometheus_url,
            "cloud_agent_semantic_cache_estimated_saved_call_total or cloud_agent_semantic_cache_hit_total",
        ),
    )
    check("grafana_health", lambda: _http_status_detail(f"{args.grafana_url.rstrip('/')}/api/health"))

    if args.grafana_user and args.grafana_password:
        check(
            "grafana_dashboard",
            lambda: _grafana_dashboard_detail(
                args.grafana_url, args.grafana_user, args.grafana_password
            ),
        )
    else:
        steps.append(
            StepResult(
                "grafana_dashboard",
                BLOCKED,
                "set --grafana-user and --grafana-password to verify dashboard API",
            )
        )
    return steps


def write_summary(path: Path, steps: list[StepResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["step\tstatus\tdetail"]
    lines.extend(f"{step.name}\t{step.status}\t{step.detail}" for step in steps)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(summary_path: Path, steps: list[StepResult]) -> dict[str, object]:
    counts = {
        "passed": sum(step.status == PASS for step in steps),
        "failed": sum(step.status == FAIL for step in steps),
        "blocked": sum(step.status == BLOCKED for step in steps),
    }
    return {
        "status": "failed" if counts["failed"] else "ready",
        "summary": {"steps": len(steps), **counts},
        "artifact": str(summary_path),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(description="Cross-platform Cloud Agent observability acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000")
    parser.add_argument("--grafana-user", default="")
    parser.add_argument("--grafana-password", default="")
    parser.add_argument("--run-chat-smoke", action="store_true")
    parser.add_argument(
        "--chat-smoke-text",
        default="observability acceptance ping",
        help="Synthetic chat text; used in memory only and never written to artifacts",
    )
    parser.add_argument(
        "--require-llm-metric",
        action="store_true",
        help="Require at least one Prometheus LLM metric sample",
    )
    parser.add_argument(
        "--require-tool-metric",
        action="store_true",
        help="Require at least one Prometheus MCP tool metric sample",
    )
    parser.add_argument(
        "--post-chat-wait-seconds",
        type=int,
        default=20,
        help="Wait for Prometheus to scrape synthetic chat metrics after a successful chat smoke",
    )
    parser.add_argument("--json", action="store_true", help="Print only a safe aggregate summary")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=_repo_root() / ".acceptance" / stamp,
        help="Directory for summary.tsv",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.post_chat_wait_seconds < 0:
        raise SystemExit("--post-chat-wait-seconds must be non-negative")
    summary_path = args.artifact_dir / "summary.tsv"
    steps = run_acceptance(args)
    write_summary(summary_path, steps)
    report = build_report(summary_path, steps)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(summary_path)
        for step in steps:
            print(f"{step.name}: {step.status}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
