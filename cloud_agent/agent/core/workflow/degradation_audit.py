import json
from typing import Any

from core.workflow.event_log import build_event, emit_event


def build_degradation_event(
    *,
    request_id: str,
    user_id_hash: str,
    component: str,
    operation: str,
    status: str = "degraded",
    error_type: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_type": "degradation",
        "request_id": request_id,
        "user_id_hash": user_id_hash,
        "component": component,
        "operation": operation,
        "status": status,
    }
    if error_type:
        event["error_type"] = error_type
    return event


def emit_degradation(event: dict[str, Any]) -> None:
    print(f"[Degradation] {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
    emit_event(
        build_event(
            event_type=str(event.get("event_type", "degradation")),
            request_id=str(event.get("request_id", "unknown")),
            user_id_hash=str(event.get("user_id_hash", "unknown")),
            component=str(event.get("component", "unknown")),
            operation=str(event.get("operation", "unknown")),
            status=event.get("status"),
            error_type=event.get("error_type"),
        )
    )
