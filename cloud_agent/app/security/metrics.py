from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status


METRICS_TOKEN_ENV = "CLOUD_AGENT_METRICS_TOKEN"
METRICS_AUTH_FAILURE_DETAIL = "metrics_auth_required"


def _metrics_token() -> str | None:
    value = os.getenv(METRICS_TOKEN_ENV, "").strip()
    return value or None


def _extract_bearer_token(request: Request) -> str | None:
    authorization = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    cleaned = token.strip()
    return cleaned or None


def _extract_header_token(request: Request) -> str | None:
    token = (request.headers.get("X-Metrics-Token") or request.headers.get("x-metrics-token") or "").strip()
    return token or None


def require_metrics_access(request: Request) -> None:
    expected = _metrics_token()
    if expected is None:
        return

    for candidate in (_extract_bearer_token(request), _extract_header_token(request)):
        if candidate is not None and hmac.compare_digest(candidate, expected):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=METRICS_AUTH_FAILURE_DETAIL,
    )
