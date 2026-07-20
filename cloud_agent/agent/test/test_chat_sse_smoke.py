import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = PROJECT_ROOT / "ops" / "chat_sse_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("chat_sse_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_join_url_handles_trailing_and_leading_slashes():
    smoke = _load_smoke_module()

    assert smoke.join_url("http://127.0.0.1:5000/", "/api/chat") == (
        "http://127.0.0.1:5000/api/chat"
    )


def test_parse_sse_lines_ignores_blank_lines_and_done_sentinel():
    smoke = _load_smoke_module()

    payloads = smoke.parse_sse_lines(
        [
            b'data: {"schema_version": "1.0", "event_type": "stream_start", "stream_mode": "native"}\n',
            b"\n",
            b'data: {"schema_version": "1.0", "event_type": "route_decision", "step": "orchestrator"}\n',
            b"data: [DONE]\n",
        ]
    )

    assert payloads == [
        {"schema_version": "1.0", "event_type": "stream_start", "stream_mode": "native"},
        {"schema_version": "1.0", "event_type": "route_decision", "step": "orchestrator"},
    ]


def test_validate_payloads_requires_full_sse_contract():
    smoke = _load_smoke_module()

    summary = smoke.validate_payloads(
        [
            {"schema_version": "1.0", "event_type": "stream_start", "stream_mode": "native"},
            {"schema_version": "1.0", "event_type": "route_decision", "step": "orchestrator"},
            {"schema_version": "1.0", "event_type": "message_delta", "content": "hello"},
            {"schema_version": "1.0", "event_type": "final", "final": "hello"},
            {"schema_version": "1.0", "event_type": "done", "done": True, "request_id": "req_smoke"},
        ],
        label="backend",
    )

    assert summary["label"] == "backend"
    assert summary["request_id"] == "req_smoke"
    assert summary["schema_version"] == "1.0"
    assert summary["steps"] == ["orchestrator"]
    assert summary["content_chars"] == 5


def test_validate_payloads_rejects_missing_route_decision():
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match="route_decision"):
        smoke.validate_payloads(
            [
                {"schema_version": "1.0", "event_type": "stream_start", "stream_mode": "native"},
                {"schema_version": "1.0", "event_type": "message_delta", "content": "hello"},
                {"schema_version": "1.0", "event_type": "final", "final": "hello"},
                {"schema_version": "1.0", "event_type": "done", "done": True, "request_id": "req_smoke"},
            ],
            label="backend",
        )


def test_validate_payloads_rejects_missing_schema_version():
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match="schema_version"):
        smoke.validate_payloads(
            [
                {"event_type": "stream_start", "stream_mode": "native"},
                {"event_type": "route_decision", "step": "orchestrator"},
                {"event_type": "message_delta", "content": "hello"},
                {"event_type": "final", "final": "hello"},
                {"event_type": "done", "done": True, "request_id": "req_smoke"},
            ],
            label="backend",
        )
