import json
import time
from typing import Any

from core.workflow.metrics import record_event_metrics


def now_ms() -> float:
    return time.perf_counter() * 1000


def elapsed_ms(start_ms: float) -> int:
    return max(0, round(now_ms() - start_ms))


def build_event(
    *,
    event_type: str,
    request_id: str,
    user_id_hash: str,
    component: str,
    operation: str,
    tenant_id: str | None = None,
    status: str | None = None,
    latency_ms: int | None = None,
    error_type: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_type": event_type,
        "request_id": request_id,
        "user_id_hash": user_id_hash,
        "component": component,
        "operation": operation,
    }
    if tenant_id:
        event["tenant_id"] = tenant_id
    if status:
        event["status"] = status
    if latency_ms is not None:
        event["latency_ms"] = latency_ms
    if error_type:
        event["error_type"] = error_type
    for key, value in extra.items():
        if value is not None:
            event[key] = value
    return event


def emit_event(event: dict[str, Any]) -> None:
    record_event_metrics(event)
    print(f"[EventLog] {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
