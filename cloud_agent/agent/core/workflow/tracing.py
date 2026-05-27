from __future__ import annotations

import os
from typing import Any


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class StreamChatTraceSpan:
    """Optional OpenTelemetry span wrapper for the Web stream_chat request."""

    def __init__(
        self,
        *,
        identity_source: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self._enabled = _env_flag("CLOUD_AGENT_TRACE_ENABLED", False)
        self._request_id_enabled = _env_flag(
            "CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", False
        )
        self._identity_source = identity_source or "unknown"
        self._request_id = request_id
        self._span_cm: Any | None = None
        self._span: Any | None = None
        self._status_cls: Any | None = None
        self._status_code_cls: Any | None = None
        self._error_set = False

    def __enter__(self) -> "StreamChatTraceSpan":
        if not self._enabled:
            return self
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except Exception:
            return self

        tracer = trace.get_tracer("cloud_agent.web")
        self._span_cm = tracer.start_as_current_span("cloud_agent.stream_chat")
        self._span = self._span_cm.__enter__()
        self._status_cls = Status
        self._status_code_cls = StatusCode
        self.set_attribute("component", "chat_service")
        self.set_attribute("operation", "stream_chat")
        self.set_attribute("identity.source", self._identity_source)
        if self._request_id_enabled and self._request_id:
            self.set_attribute("request.id", self._request_id)
        return self

    def __exit__(self, exc_type, _exc, _tb) -> bool:
        if exc_type is not None and not self._error_set:
            self.set_error(exc_type.__name__)
        if self._span_cm is not None:
            # Do not pass exception info to OpenTelemetry's context manager.
            # That avoids recording exception messages or stack traces in this minimal PoC.
            self._span_cm.__exit__(None, None, None)
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is None or value is None:
            return
        try:
            self._span.set_attribute(key, value)
        except Exception:
            return

    def set_success(self) -> None:
        self.set_attribute("request.status", "success")
        if self._span is None or self._status_cls is None or self._status_code_cls is None:
            return
        try:
            self._span.set_status(self._status_cls(self._status_code_cls.OK))
        except Exception:
            return

    def set_error(self, error_type: str) -> None:
        self._error_set = True
        self.set_attribute("request.status", "error")
        self.set_attribute("error.type", error_type)
        if self._span is None or self._status_cls is None or self._status_code_cls is None:
            return
        try:
            self._span.set_status(self._status_cls(self._status_code_cls.ERROR))
        except Exception:
            return


def start_stream_chat_span(
    *,
    identity_source: str | None = None,
    request_id: str | None = None,
) -> StreamChatTraceSpan:
    return StreamChatTraceSpan(identity_source=identity_source, request_id=request_id)
