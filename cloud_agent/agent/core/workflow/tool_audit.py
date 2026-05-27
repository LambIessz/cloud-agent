import json
import time
from typing import Any

from core.workflow.event_log import build_event, emit_event


def now_ms() -> float:
    return time.perf_counter() * 1000


def elapsed_ms(start_ms: float) -> int:
    return max(0, round(now_ms() - start_ms))


def build_tool_audit_event(
    *,
    request_id: str,
    user_id_hash: str,
    tool_name: str,
    latency_ms: int,
    status: str,
    error_type: str | None = None,
    identity_injected: bool = False,
    attempt: int | None = None,
    max_attempts: int | None = None,
    timeout_seconds: float | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_type": "tool_call",
        "request_id": request_id,
        "user_id_hash": user_id_hash,
        "tool_name": tool_name,
        "latency_ms": latency_ms,
        "status": status,
        "identity_injected": identity_injected,
    }
    if error_type:
        event["error_type"] = error_type
    if attempt is not None:
        event["attempt"] = attempt
    if max_attempts is not None:
        event["max_attempts"] = max_attempts
    if timeout_seconds is not None:
        event["timeout_seconds"] = timeout_seconds
    if retryable is not None:
        event["retryable"] = retryable
    return event


def emit_tool_audit(event: dict[str, Any]) -> None:
    print(f"[ToolAudit] {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
    emit_event(
        build_event(
            event_type=str(event.get("event_type", "tool_call")),
            request_id=str(event.get("request_id", "unknown")),
            user_id_hash=str(event.get("user_id_hash", "unknown")),
            component="mcp_tool",
            operation="tool_call",
            status=event.get("status"),
            latency_ms=event.get("latency_ms"),
            error_type=event.get("error_type"),
            tool_name=event.get("tool_name"),
            identity_injected=event.get("identity_injected"),
            attempt=event.get("attempt"),
            max_attempts=event.get("max_attempts"),
            timeout_seconds=event.get("timeout_seconds"),
            retryable=event.get("retryable"),
        )
    )
