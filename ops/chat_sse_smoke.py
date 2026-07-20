#!/usr/bin/env python
"""Smoke test for the Cloud Agent chat SSE contract.

This script expects the FastAPI backend to be running. It can also verify the
Vite frontend proxy when --frontend-url is provided.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"
DEFAULT_QUERY = "weather today?"
REQUIRED_EVENT_TYPES = ("stream_start", "route_decision", "message_delta", "final", "done")
SUPPORTED_SSE_SCHEMA_VERSION = "1.0"


class SmokeFailure(RuntimeError):
    """Raised when a smoke target does not satisfy the SSE contract."""


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def parse_sse_lines(lines: Iterable[bytes]) -> list[dict]:
    payloads: list[dict] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        payload = json.loads(data)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def validate_payloads(payloads: list[dict], *, label: str) -> dict:
    schema_versions = {payload.get("schema_version") for payload in payloads}
    if schema_versions != {SUPPORTED_SSE_SCHEMA_VERSION}:
        observed = ", ".join(sorted(repr(version) for version in schema_versions)) or "missing"
        raise SmokeFailure(
            f"{label}: expected schema_version={SUPPORTED_SSE_SCHEMA_VERSION}, got {observed}"
        )

    event_types = [payload.get("event_type") for payload in payloads]
    missing = [
        event_type for event_type in REQUIRED_EVENT_TYPES if event_type not in event_types
    ]
    if missing:
        raise SmokeFailure(f"{label}: missing SSE event types: {', '.join(missing)}")

    deltas = [
        payload.get("content", "")
        for payload in payloads
        if payload.get("event_type") == "message_delta"
    ]
    content_chars = sum(len(delta) for delta in deltas if isinstance(delta, str))
    if content_chars <= 0:
        raise SmokeFailure(f"{label}: message_delta events did not include content")

    done_payloads = [
        payload for payload in payloads if payload.get("event_type") == "done"
    ]
    done_payload = done_payloads[-1]
    request_id = done_payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise SmokeFailure(f"{label}: done event did not include request_id")

    steps = []
    for payload in payloads:
        if payload.get("event_type") == "route_decision":
            route_target = payload.get("route_to") or payload.get("step")
            if isinstance(route_target, str) and route_target:
                steps.append(route_target)
        elif payload.get("event_type") == "agent_step" and payload.get("step"):
            steps.append(payload.get("step"))

    stream_start = next(
        payload for payload in payloads if payload.get("event_type") == "stream_start"
    )

    return {
        "label": label,
        "event_count": len(payloads),
        "request_id": request_id,
        "stream_mode": stream_start.get("stream_mode"),
        "schema_version": stream_start.get("schema_version"),
        "steps": steps,
        "content_chars": content_chars,
    }


def read_json(url: str, *, timeout: float) -> dict:
    with urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def post_chat_sse(
    base_url: str,
    *,
    query: str,
    user_id: str,
    tenant_id: str,
    session_id: str,
    timeout: float,
) -> list[dict]:
    body = json.dumps(
        {
            "query": query,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
        }
    ).encode("utf-8")
    request = Request(
        join_url(base_url, "/api/chat"),
        data=body,
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "X-User-Id": user_id,
            "X-Tenant-Id": tenant_id,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            raise SmokeFailure(
                f"{base_url}: expected text/event-stream, got {content_type or 'empty'}"
            )
        response_schema_version = response.headers.get("X-SSE-Schema-Version", "")
        if response_schema_version != SUPPORTED_SSE_SCHEMA_VERSION:
            raise SmokeFailure(
                f"{base_url}: expected X-SSE-Schema-Version={SUPPORTED_SSE_SCHEMA_VERSION}, "
                f"got {response_schema_version or 'empty'}"
            )
        payloads = parse_sse_lines(response)
    return payloads


def run_target(
    *,
    label: str,
    base_url: str,
    query: str,
    user_id: str,
    tenant_id: str,
    session_id: str,
    timeout: float,
) -> dict:
    payloads = post_chat_sse(
        base_url,
        query=query,
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        timeout=timeout,
    )
    return validate_payloads(payloads, label=label)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Cloud Agent /api/chat SSE events on a running server."
    )
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument(
        "--frontend-url",
        default="",
        help="Optional Vite frontend URL. When set, verifies /api/chat through the proxy.",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--user-id", default="smoke_user")
    parser.add_argument("--tenant-id", default="smoke_tenant")
    parser.add_argument("--session-id", default="smoke_sse_session")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = [("backend", args.backend_url)]
    if args.frontend_url:
        targets.append(("frontend_proxy", args.frontend_url))

    summaries = []
    try:
        ready = read_json(join_url(args.backend_url, "/readyz"), timeout=args.timeout)
        if ready.get("status") != "ready":
            raise SmokeFailure(f"backend: /readyz returned {ready!r}")

        for label, base_url in targets:
            summary = run_target(
                label=label,
                base_url=base_url,
                query=args.query,
                user_id=args.user_id,
                tenant_id=args.tenant_id,
                session_id=f"{args.session_id}_{label}",
                timeout=args.timeout,
            )
            summaries.append(summary)
            print(
                "[{label}] ok events={event_count} mode={stream_mode} "
                "request_id={request_id} steps={steps} content_chars={content_chars}".format(
                    **summary
                )
            )
    except (HTTPError, URLError, TimeoutError, OSError, SmokeFailure, json.JSONDecodeError) as exc:
        print(f"[chat_sse_smoke] failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "ok", "targets": summaries}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
