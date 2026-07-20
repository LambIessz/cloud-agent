import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
AGENT_DIR = Path(__file__).resolve().parents[1]
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.workflow.degradation_audit import build_degradation_event, emit_degradation
from core.workflow.event_log import build_event, emit_event
from core.workflow.tool_audit import build_tool_audit_event, emit_tool_audit


def _events(output: str, prefix: str):
    items = []
    for line in output.splitlines():
        marker = f"[{prefix}] "
        if line.startswith(marker):
            items.append(json.loads(line.removeprefix(marker)))
    return items


def test_emit_event_redacts_sensitive_fields_and_nested_secret_values(capsys):
    emit_event(
        build_event(
            event_type="request_start",
            request_id="req_redact",
            user_id_hash="hash_redact",
            component="chat_service",
            operation="stream_chat",
            user_id="plain_user",
            query="secret query",
            prompt="secret prompt",
            completion="secret completion",
            api_key="sk-abc1234567890",
            metadata={
                "prompt": "nested prompt",
                "api_key": "sk-nestedsecret987654321",
            },
        )
    )

    output = capsys.readouterr().out
    assert "plain_user" not in output
    assert "secret query" not in output
    assert "secret prompt" not in output
    assert "secret completion" not in output
    assert "sk-abc1234567890" not in output
    assert "sk-nestedsecret987654321" not in output
    assert "sk-<redacted>" in output

    events = _events(output, "EventLog")
    assert len(events) == 1
    event = events[0]
    assert event["user_id"] == "<redacted>"
    assert event["query"] == "<redacted>"
    assert event["prompt"] == "<redacted>"
    assert event["completion"] == "<redacted>"
    assert event["api_key"] == "sk-<redacted>"
    assert event["metadata"]["prompt"] == "<redacted>"
    assert event["metadata"]["api_key"] == "sk-<redacted>"


def test_emit_tool_audit_redacts_sensitive_fields(capsys):
    emit_tool_audit(
        {
            **build_tool_audit_event(
                request_id="req_tool_redact",
                user_id_hash="hash_tool_redact",
                tool_name="query_user_instances",
                latency_ms=12,
                status="success",
                identity_injected=True,
            ),
            "message": "secret tool message",
            "authorization": "Bearer verysecretpayload",
            "api_key_prefix": "sk-toolprefix123456",
        }
    )

    output = capsys.readouterr().out
    assert "secret tool message" not in output
    assert "Bearer verysecretpayload" not in output
    assert "sk-toolprefix123456" not in output

    tool_audit_events = _events(output, "ToolAudit")
    event_log_events = _events(output, "EventLog")
    assert len(tool_audit_events) == 1
    assert len(event_log_events) == 1
    assert tool_audit_events[0]["message"] == "<redacted>"
    assert tool_audit_events[0]["authorization"] == "<redacted>"
    assert tool_audit_events[0]["api_key_prefix"] == "sk-<redacted>"
    assert "message" not in event_log_events[0]
    assert "authorization" not in event_log_events[0]
    assert "api_key_prefix" not in event_log_events[0]


def test_emit_degradation_redacts_sensitive_fields(capsys):
    emit_degradation(
        {
            **build_degradation_event(
                request_id="req_degrade_redact",
                user_id_hash="hash_degrade_redact",
                component="semantic_cache",
                operation="get_cache",
                status="degraded",
                error_type="RuntimeError",
            ),
            "error_message": "cache backend leaked",
            "session_id": "session_secret",
        }
    )

    output = capsys.readouterr().out
    assert "cache backend leaked" not in output
    assert "session_secret" not in output

    degradation_events = _events(output, "Degradation")
    event_log_events = _events(output, "EventLog")
    assert len(degradation_events) == 1
    assert len(event_log_events) == 1
    assert degradation_events[0]["error_message"] == "<redacted>"
    assert degradation_events[0]["session_id"] == "<redacted>"
    assert "error_message" not in event_log_events[0]
    assert "session_id" not in event_log_events[0]
