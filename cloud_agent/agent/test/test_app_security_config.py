import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config.cors import get_cors_origins
from schemas.chat import ChatRequest


def test_default_cors_origins_are_explicit_dev_hosts(monkeypatch):
    monkeypatch.delenv("CLOUD_AGENT_CORS_ORIGINS", raising=False)

    origins = get_cors_origins()

    assert "*" not in origins
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins


def test_cors_origins_are_loaded_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv(
        "CLOUD_AGENT_CORS_ORIGINS",
        "https://console.example.com, https://admin.example.com",
    )

    assert get_cors_origins() == [
        "https://console.example.com",
        "https://admin.example.com",
    ]


def test_cors_origins_reject_wildcard_when_credentials_are_enabled(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_CORS_ORIGINS", "https://console.example.com,*")

    with pytest.raises(ValueError, match="wildcard"):
        get_cors_origins()


def test_chat_request_strips_and_accepts_valid_identifiers():
    request = ChatRequest(
        query="  Check ECS refund rules  ",
        user_id="user-1001@example.com",
        tenant_id="tenant_a",
        session_id="session.20260709",
    )

    assert request.query == "Check ECS refund rules"
    assert request.user_id == "user-1001@example.com"


def test_chat_request_rejects_blank_or_too_long_query():
    with pytest.raises(ValidationError):
        ChatRequest(query="   ")

    with pytest.raises(ValidationError):
        ChatRequest(query="x" * 4001)


def test_chat_request_rejects_extra_fields_and_control_characters():
    with pytest.raises(ValidationError):
        ChatRequest(query="hello", debug=True)

    with pytest.raises(ValidationError):
        ChatRequest(query="hello", user_id="bad\nuser")
