import asyncio
import json

import pytest

from agents.billing_agent import UserIdInjector


class _Runtime:
    def __init__(self, config):
        self.config = config


class _Request:
    def __init__(self, name="query_user_orders", args=None, config=None):
        self.name = name
        self.args = args or {}
        self.runtime = _Runtime(config or {})

    def override(self, *, args):
        return _Request(name=self.name, args=args, config=self.runtime.config)


def _audit_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[ToolAudit] "):
            events.append(json.loads(line.removeprefix("[ToolAudit] ")))
    return events


def _event_log_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))
    return events


def test_user_id_injector_audits_success_without_plain_user_id(capsys):
    injector = UserIdInjector()
    request = _Request(
        args={"user_id": "model_supplied_user"},
        config={
            "configurable": {
                "user_id": "trusted_user",
                "user_id_hash": "hash_123",
                "request_id": "req_audit_success",
            }
        },
    )

    async def handler(received):
        assert received.args["user_id"] == "trusted_user"
        return "ok"

    result = asyncio.run(injector(request, handler))

    assert result == "ok"
    output = capsys.readouterr().out
    assert "trusted_user" not in output
    assert "model_supplied_user" not in output

    events = _audit_events(output)
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"
    assert events[0]["request_id"] == "req_audit_success"
    assert events[0]["user_id_hash"] == "hash_123"
    assert events[0]["tool_name"] == "query_user_orders"
    assert events[0]["status"] == "success"
    assert events[0]["identity_injected"] is True
    assert isinstance(events[0]["latency_ms"], int)

    event_log_events = _event_log_events(output)
    assert len(event_log_events) == 1
    assert event_log_events[0]["event_type"] == "tool_call"
    assert event_log_events[0]["request_id"] == "req_audit_success"
    assert event_log_events[0]["user_id_hash"] == "hash_123"
    assert event_log_events[0]["component"] == "mcp_tool"
    assert event_log_events[0]["operation"] == "tool_call"
    assert event_log_events[0]["tool_name"] == "query_user_orders"
    assert event_log_events[0]["status"] == "success"
    assert event_log_events[0]["identity_injected"] is True
    assert isinstance(event_log_events[0]["latency_ms"], int)


def test_user_id_injector_audits_error(capsys):
    injector = UserIdInjector()
    request = _Request(
        name="analyze_instance_usage",
        config={
            "configurable": {
                "user_id": "trusted_user",
                "user_id_hash": "hash_456",
                "request_id": "req_audit_error",
            }
        },
    )

    async def handler(_received):
        raise TimeoutError("database timed out")

    with pytest.raises(TimeoutError):
        asyncio.run(injector(request, handler))

    output = capsys.readouterr().out
    assert "trusted_user" not in output
    assert "database timed out" not in output

    events = _audit_events(output)
    assert len(events) == 1
    assert events[0]["request_id"] == "req_audit_error"
    assert events[0]["user_id_hash"] == "hash_456"
    assert events[0]["tool_name"] == "analyze_instance_usage"
    assert events[0]["status"] == "error"
    assert events[0]["error_type"] == "TimeoutError"
    assert events[0]["identity_injected"] is True
    assert events[0]["attempt"] == 1
    assert events[0]["max_attempts"] == 1


def test_user_id_injector_audits_without_identity(capsys):
    injector = UserIdInjector()
    request = _Request(config={"configurable": {"request_id": "req_no_identity"}})

    async def handler(received):
        assert "user_id" not in received.args
        return "ok"

    asyncio.run(injector(request, handler))

    events = _audit_events(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["request_id"] == "req_no_identity"
    assert events[0]["user_id_hash"] == "unknown"
    assert events[0]["status"] == "success"
    assert events[0]["identity_injected"] is False


def test_user_id_injector_times_out_slow_tool(capsys):
    injector = UserIdInjector()
    request = _Request(
        name="slow_tool",
        config={
            "configurable": {
                "request_id": "req_timeout",
                "user_id_hash": "hash_timeout",
                "tool_timeout_seconds": 0.01,
            }
        },
    )

    async def handler(_received):
        await asyncio.sleep(1)
        return "too late"

    with pytest.raises(TimeoutError):
        asyncio.run(injector(request, handler))

    events = _audit_events(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["request_id"] == "req_timeout"
    assert events[0]["tool_name"] == "slow_tool"
    assert events[0]["status"] == "error"
    assert events[0]["error_type"] == "TimeoutError"
    assert events[0]["timeout_seconds"] == 0.01
    assert events[0]["retryable"] is True


def test_user_id_injector_retries_retryable_errors(capsys):
    injector = UserIdInjector()
    request = _Request(
        name="flaky_tool",
        config={
            "configurable": {
                "request_id": "req_retry",
                "user_id_hash": "hash_retry",
                "tool_retry_count": 1,
            }
        },
    )
    calls = {"count": 0}

    async def handler(_received):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("temporary network issue")
        return "ok"

    result = asyncio.run(injector(request, handler))

    assert result == "ok"
    assert calls["count"] == 2
    output = capsys.readouterr().out
    events = _audit_events(output)
    assert [event["status"] for event in events] == ["retry", "success"]
    assert events[0]["error_type"] == "ConnectionError"
    assert events[0]["attempt"] == 1
    assert events[1]["attempt"] == 2
    assert all(event["max_attempts"] == 2 for event in events)

    event_log_events = _event_log_events(output)
    assert [event["status"] for event in event_log_events] == ["retry", "success"]
    assert event_log_events[0]["error_type"] == "ConnectionError"
    assert event_log_events[0]["retryable"] is True
    assert event_log_events[0]["attempt"] == 1
    assert event_log_events[1]["attempt"] == 2
    assert all(event["component"] == "mcp_tool" for event in event_log_events)


def test_user_id_injector_does_not_retry_non_retryable_errors(capsys):
    injector = UserIdInjector()
    request = _Request(
        name="bad_request_tool",
        config={
            "configurable": {
                "request_id": "req_no_retry",
                "user_id_hash": "hash_no_retry",
                "tool_retry_count": 3,
            }
        },
    )
    calls = {"count": 0}

    async def handler(_received):
        calls["count"] += 1
        raise ValueError("bad model args")

    with pytest.raises(ValueError):
        asyncio.run(injector(request, handler))

    assert calls["count"] == 1
    events = _audit_events(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert events[0]["error_type"] == "ValueError"
    assert events[0]["retryable"] is False
    assert events[0]["max_attempts"] == 4
