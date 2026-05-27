import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any


_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.@:-]+")


@dataclass(frozen=True)
class IdentityContext:
    user_id: str
    tenant_id: str
    source: str

    @property
    def user_id_hash(self) -> str:
        digest = hashlib.sha256(self.user_id.encode("utf-8")).hexdigest()
        return digest[:16]


def _safe_identifier(value: str | None, default: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = default
    return _SAFE_ID_RE.sub("_", raw)[:128]


def auth_mode() -> str:
    return os.getenv("CLOUD_AGENT_AUTH_MODE", "local").strip().lower()


def resolve_identity(
    *,
    request_user_id: str | None = None,
    request_tenant_id: str | None = None,
    authenticated_user_id: str | None = None,
    authenticated_tenant_id: str | None = None,
) -> IdentityContext:
    """
    Resolve the trusted identity used by memory, cache, and MCP tools.

    In local mode, request_user_id remains accepted for demos and tests.
    In production mode, request_user_id is ignored unless an authenticated
    identity is supplied by the API/auth layer.
    """
    mode = auth_mode()
    if authenticated_user_id:
        return IdentityContext(
            user_id=_safe_identifier(authenticated_user_id, "anonymous"),
            tenant_id=_safe_identifier(authenticated_tenant_id or request_tenant_id, "default_tenant"),
            source="authenticated",
        )

    if mode in {"prod", "production"}:
        return IdentityContext(
            user_id="anonymous",
            tenant_id=_safe_identifier(authenticated_tenant_id, "default_tenant"),
            source="anonymous",
        )

    return IdentityContext(
        user_id=_safe_identifier(request_user_id, "user_1001"),
        tenant_id=_safe_identifier(request_tenant_id, "default_tenant"),
        source="debug_request",
    )


def apply_identity_metadata(
    metadata: dict[str, Any] | None,
    identity: IdentityContext,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated["tenant_id"] = identity.tenant_id
    updated["user_id_hash"] = identity.user_id_hash
    updated["identity_source"] = identity.source
    return updated


def scoped_session_id(identity: IdentityContext, session_id: str | None) -> str:
    safe_session = _safe_identifier(session_id, "default_session")
    return f"{identity.tenant_id}:{identity.user_id_hash}:{safe_session}"
