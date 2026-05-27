from __future__ import annotations

import json
import os
import sys
from concurrent import futures
from pathlib import Path
from threading import Lock


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "cloud_agent" / "agent"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


FORBIDDEN_TOKENS = (
    "plain_user",
    "user_id",
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


def _load_otlp_grpc():
    try:
        import grpc
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except Exception as exc:
        return None, exc
    return (
        grpc,
        trace,
        OTLPSpanExporter,
        trace_service_pb2,
        trace_service_pb2_grpc,
        Resource,
        TracerProvider,
        SimpleSpanProcessor,
    ), None


def _any_value(value):
    field = value.WhichOneof("value")
    if field is None:
        return None
    if field == "string_value":
        return value.string_value
    if field == "bool_value":
        return value.bool_value
    if field == "int_value":
        return value.int_value
    if field == "double_value":
        return value.double_value
    return f"<{field}>"


def _span_records(requests) -> list[dict]:
    records: list[dict] = []
    for request in requests:
        for resource_span in request.resource_spans:
            for scope_span in resource_span.scope_spans:
                for span in scope_span.spans:
                    records.append(
                        {
                            "name": span.name,
                            "attributes": {
                                attribute.key: _any_value(attribute.value)
                                for attribute in span.attributes
                            },
                        }
                    )
    return records


def main() -> int:
    loaded, error = _load_otlp_grpc()
    if loaded is None:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "otlp_grpc_dependencies_unavailable",
                    "error_type": error.__class__.__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2

    (
        grpc,
        trace,
        OTLPSpanExporter,
        trace_service_pb2,
        trace_service_pb2_grpc,
        Resource,
        TracerProvider,
        SimpleSpanProcessor,
    ) = loaded

    class Receiver(trace_service_pb2_grpc.TraceServiceServicer):
        def __init__(self) -> None:
            self.requests = []
            self._lock = Lock()

        def Export(self, request, _context):
            with self._lock:
                self.requests.append(request)
            return trace_service_pb2.ExportTraceServiceResponse()

    receiver = Receiver()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    try:
        provider = TracerProvider(resource=Resource.create({"service.name": "cloud_agent"}))
        exporter = OTLPSpanExporter(endpoint=f"127.0.0.1:{port}", insecure=True)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        from core.workflow.tracing import start_stream_chat_span

        os.environ["CLOUD_AGENT_TRACE_ENABLED"] = "true"

        os.environ.pop("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", None)
        with start_stream_chat_span(
            identity_source="authenticated",
            request_id="req_otlp_default_hidden",
        ) as span:
            span.set_attribute("cache.status", "miss")
            span.set_success()

        os.environ["CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED"] = "true"
        with start_stream_chat_span(
            identity_source="authenticated",
            request_id="req_otlp_trace",
        ) as span:
            span.set_attribute("cache.status", "hit")
            span.set_success()

        try:
            with start_stream_chat_span(
                identity_source="authenticated",
                request_id="req_otlp_error",
            ) as span:
                span.set_attribute("cache.status", "degraded")
                raise RuntimeError("secret exception message")
        except RuntimeError:
            pass

        provider.shutdown()
        records = _span_records(receiver.requests)
    finally:
        server.stop(grace=None)

    serialized = json.dumps(records, ensure_ascii=False, sort_keys=True)
    forbidden_hits = [token for token in FORBIDDEN_TOKENS if token in serialized]
    attributes = [record["attributes"] for record in records]
    result = {
        "status": "PASS",
        "backend": "in_process_otlp_grpc_receiver",
        "received_span_count": len(records),
        "span_names": [record["name"] for record in records],
        "default_has_request_id": "request.id" in attributes[0] if attributes else None,
        "enabled_request_id": attributes[1].get("request.id") if len(attributes) > 1 else None,
        "error_type": attributes[2].get("error.type") if len(attributes) > 2 else None,
        "forbidden_hits": forbidden_hits,
    }

    if (
        result["received_span_count"] != 3
        or any(name != "cloud_agent.stream_chat" for name in result["span_names"])
        or result["default_has_request_id"] is not False
        or result["enabled_request_id"] != "req_otlp_trace"
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
