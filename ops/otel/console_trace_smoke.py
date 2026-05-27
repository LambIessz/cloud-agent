from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "cloud_agent" / "agent"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


FORBIDDEN_TOKENS = (
    "plain_user",
    "user_id_hash",
    "tenant_id",
    "session_id",
    "thread_id",
    "conversation_id",
    "secret query",
    "secret prompt",
    "secret completion",
    "matched_question",
    "secret exception message",
)


def _load_sdk():
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except Exception as exc:
        return None, exc
    return (trace, Resource, TracerProvider, ConsoleSpanExporter, SimpleSpanProcessor), None


def _span_payloads(exported: str) -> list[dict]:
    decoder = json.JSONDecoder()
    payloads: list[dict] = []
    index = 0
    while index < len(exported):
        while index < len(exported) and exported[index].isspace():
            index += 1
        if index >= len(exported):
            break
        payload, index = decoder.raw_decode(exported, index)
        payloads.append(payload)
    return payloads


def main() -> int:
    loaded, error = _load_sdk()
    if loaded is None:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "opentelemetry_sdk_unavailable",
                    "error_type": error.__class__.__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2

    trace, Resource, TracerProvider, ConsoleSpanExporter, SimpleSpanProcessor = loaded

    output = io.StringIO()
    provider = TracerProvider(resource=Resource.create({"service.name": "cloud_agent"}))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=output)))
    trace.set_tracer_provider(provider)

    from core.workflow.tracing import start_stream_chat_span

    os.environ["CLOUD_AGENT_TRACE_ENABLED"] = "true"

    os.environ.pop("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", None)
    with start_stream_chat_span(
        identity_source="authenticated",
        request_id="req_default_hidden",
    ) as span:
        span.set_attribute("cache.status", "miss")
        span.set_success()

    os.environ["CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED"] = "true"
    with start_stream_chat_span(
        identity_source="authenticated",
        request_id="req_trace_smoke",
    ) as span:
        span.set_attribute("cache.status", "hit")
        span.set_success()

    try:
        with start_stream_chat_span(
            identity_source="authenticated",
            request_id="req_trace_error",
        ) as span:
            span.set_attribute("cache.status", "degraded")
            raise RuntimeError("secret exception message")
    except RuntimeError:
        pass

    provider.shutdown()
    exported = output.getvalue()
    payloads = _span_payloads(exported)
    attributes = [payload.get("attributes", {}) for payload in payloads]
    forbidden_hits = [token for token in FORBIDDEN_TOKENS if token in exported]
    result = {
        "status": "PASS",
        "span_count": len(payloads),
        "span_names": [payload.get("name") for payload in payloads],
        "default_has_request_id": "request.id" in attributes[0] if attributes else None,
        "enabled_request_id": attributes[1].get("request.id") if len(attributes) > 1 else None,
        "error_type": attributes[2].get("error.type") if len(attributes) > 2 else None,
        "forbidden_hits": forbidden_hits,
    }

    if (
        result["span_count"] != 3
        or any(name != "cloud_agent.stream_chat" for name in result["span_names"])
        or result["default_has_request_id"] is not False
        or result["enabled_request_id"] != "req_trace_smoke"
        or result["error_type"] != "RuntimeError"
        or forbidden_hits
    ):
        result["status"] = "FAIL"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
