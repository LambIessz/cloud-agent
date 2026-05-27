import uuid
from typing import Any


def new_request_id() -> str:
    """Create a compact request id for correlating one user request."""
    return f"req_{uuid.uuid4().hex[:16]}"


def ensure_request_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return metadata with a request_id, preserving existing values."""
    updated = dict(metadata or {})
    updated.setdefault("request_id", new_request_id())
    return updated


def get_request_id(metadata: dict[str, Any] | None = None) -> str:
    return str((metadata or {}).get("request_id", "unknown"))
