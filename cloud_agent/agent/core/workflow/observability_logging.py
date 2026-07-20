from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any


_REDACTED = "<redacted>"
_SENSITIVE_KEYS = {
    "api_key",
    "api_key_prefix",
    "authorization",
    "completion",
    "content",
    "credential",
    "credentials",
    "error_message",
    "message",
    "matched_question",
    "password",
    "preference",
    "prompt",
    "query",
    "secret",
    "session_id",
    "thread_id",
    "token",
    "user_id",
}
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{8,}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+=/]{8,}\b")


class _DynamicStdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            sys.stdout.write(f"{message}\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


def _redact_string(value: str) -> str:
    value = _API_KEY_PATTERN.sub("sk-<redacted>", value)
    value = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    return value


def sanitize_observability_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = key.lower() if isinstance(key, str) else None
    if normalized_key in _SENSITIVE_KEYS:
        if normalized_key in {"api_key", "api_key_prefix"} and isinstance(value, str):
            return _redact_string(value)
        return _REDACTED

    if isinstance(value, dict):
        return {
            str(child_key): sanitize_observability_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
            if child_value is not None
        }
    if isinstance(value, list):
        return [sanitize_observability_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_observability_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_string(str(value))


def sanitize_observability_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_observability_value(payload)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def _get_observability_logger() -> logging.Logger:
    logger = logging.getLogger("cloud_agent.observability")
    if not any(getattr(handler, "_cloud_agent_observability", False) for handler in logger.handlers):
        handler = _DynamicStdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._cloud_agent_observability = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def emit_structured_record(prefix: str, payload: dict[str, Any]) -> None:
    logger = _get_observability_logger()
    safe_payload = sanitize_observability_payload(payload)
    logger.info("[%s] %s", prefix, json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, default=str))
