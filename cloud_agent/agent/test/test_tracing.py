import sys
import types

from cloud_agent.agent.core.workflow.tracing import start_stream_chat_span


class _FakeStatusCode:
    OK = "OK"
    ERROR = "ERROR"


class _FakeStatus:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeSpan:
    def __init__(self):
        self.attributes = {}
        self.statuses = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, status):
        self.statuses.append(status)


class _FakeSpanContextManager:
    def __init__(self, span):
        self.span = span
        self.exit_args = None

    def __enter__(self):
        return self.span

    def __exit__(self, exc_type, exc, tb):
        self.exit_args = (exc_type, exc, tb)
        return False


class _FakeTracer:
    def __init__(self):
        self.started_names = []
        self.context_managers = []

    def start_as_current_span(self, name):
        self.started_names.append(name)
        cm = _FakeSpanContextManager(_FakeSpan())
        self.context_managers.append(cm)
        return cm


def _install_fake_opentelemetry(monkeypatch, tracer):
    opentelemetry_module = types.ModuleType("opentelemetry")
    trace_module = types.ModuleType("opentelemetry.trace")

    def get_tracer(name):
        tracer.tracer_name = name
        return tracer

    trace_module.get_tracer = get_tracer
    trace_module.Status = _FakeStatus
    trace_module.StatusCode = _FakeStatusCode
    opentelemetry_module.trace = trace_module

    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_module)


def test_stream_chat_trace_span_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("CLOUD_AGENT_TRACE_ENABLED", raising=False)
    monkeypatch.setenv("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", "true")

    with start_stream_chat_span(
        identity_source="debug_request",
        request_id="req_hidden_when_trace_disabled",
    ) as span:
        span.set_attribute("cache.status", "unavailable")
        span.set_success()

    assert span._span is None
    assert span._span_cm is None


def test_stream_chat_trace_span_records_only_whitelisted_low_cardinality_attributes(monkeypatch):
    tracer = _FakeTracer()
    _install_fake_opentelemetry(monkeypatch, tracer)
    monkeypatch.setenv("CLOUD_AGENT_TRACE_ENABLED", "true")
    monkeypatch.delenv("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", raising=False)

    with start_stream_chat_span(
        identity_source="debug_request",
        request_id="req_default_hidden",
    ) as span:
        span.set_attribute("cache.status", "miss")
        span.set_success()

    fake_span = tracer.context_managers[0].span

    assert tracer.tracer_name == "cloud_agent.web"
    assert tracer.started_names == ["cloud_agent.stream_chat"]
    assert fake_span.attributes == {
        "component": "chat_service",
        "operation": "stream_chat",
        "identity.source": "debug_request",
        "cache.status": "miss",
        "request.status": "success",
    }
    assert fake_span.statuses[-1].status_code == _FakeStatusCode.OK

    forbidden_keys = {
        "request.id",
        "request_id",
        "user_id",
        "user_id_hash",
        "tenant_id",
        "session_id",
        "thread_id",
        "conversation_id",
        "query",
        "prompt",
        "completion",
        "message",
        "matched_question",
    }
    assert forbidden_keys.isdisjoint(fake_span.attributes)


def test_stream_chat_trace_span_records_request_id_only_when_explicitly_enabled(monkeypatch):
    tracer = _FakeTracer()
    _install_fake_opentelemetry(monkeypatch, tracer)
    monkeypatch.setenv("CLOUD_AGENT_TRACE_ENABLED", "true")
    monkeypatch.setenv("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", "true")

    with start_stream_chat_span(
        identity_source="authenticated",
        request_id="req_trace_lookup_1234",
    ) as span:
        span.set_attribute("cache.status", "hit")
        span.set_success()

    fake_span = tracer.context_managers[0].span

    assert fake_span.attributes == {
        "component": "chat_service",
        "operation": "stream_chat",
        "identity.source": "authenticated",
        "request.id": "req_trace_lookup_1234",
        "cache.status": "hit",
        "request.status": "success",
    }

    forbidden_keys = {
        "request_id",
        "user_id",
        "user_id_hash",
        "tenant_id",
        "session_id",
        "thread_id",
        "conversation_id",
        "query",
        "prompt",
        "completion",
        "message",
        "matched_question",
        "error.message",
    }
    assert forbidden_keys.isdisjoint(fake_span.attributes)


def test_stream_chat_trace_span_ignores_empty_request_id_when_enabled(monkeypatch):
    tracer = _FakeTracer()
    _install_fake_opentelemetry(monkeypatch, tracer)
    monkeypatch.setenv("CLOUD_AGENT_TRACE_ENABLED", "true")
    monkeypatch.setenv("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", "true")

    with start_stream_chat_span(identity_source="authenticated", request_id="") as span:
        span.set_success()

    fake_span = tracer.context_managers[0].span

    assert "request.id" not in fake_span.attributes


def test_stream_chat_trace_span_error_uses_type_only_and_suppresses_exception_details(monkeypatch):
    tracer = _FakeTracer()
    _install_fake_opentelemetry(monkeypatch, tracer)
    monkeypatch.setenv("CLOUD_AGENT_TRACE_ENABLED", "true")
    monkeypatch.setenv("CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED", "true")

    class SecretError(RuntimeError):
        pass

    try:
        with start_stream_chat_span(
            identity_source="authenticated",
            request_id="req_error_lookup",
        ) as span:
            span.set_attribute("cache.status", "degraded")
            raise SecretError("secret exception message")
    except SecretError:
        pass

    context_manager = tracer.context_managers[0]
    fake_span = context_manager.span

    assert fake_span.attributes["request.status"] == "error"
    assert fake_span.attributes["request.id"] == "req_error_lookup"
    assert fake_span.attributes["error.type"] == "SecretError"
    assert "secret exception message" not in str(fake_span.attributes)
    assert fake_span.statuses[-1].status_code == _FakeStatusCode.ERROR
    assert context_manager.exit_args == (None, None, None)
