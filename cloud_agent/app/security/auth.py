import os
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping
from urllib.request import urlopen

from fastapi import HTTPException, Request, status


DEFAULT_AUTH_USER_HEADER = "X-Authenticated-User-Id"
DEFAULT_AUTH_TENANT_HEADER = "X-Authenticated-Tenant-Id"
DEFAULT_JWT_USER_CLAIM = "sub"
DEFAULT_JWT_TENANT_CLAIM = "tenant_id"
DEFAULT_JWT_ALGORITHMS = ("HS256",)
DEFAULT_OIDC_ALGORITHMS = ("RS256",)
DEFAULT_JWKS_CACHE_SECONDS = 300.0
DEFAULT_JWKS_TIMEOUT_SECONDS = 5.0

_JWK_CLIENTS: dict[tuple[str, float, float], Any] = {}
_DISCOVERY_CACHE: dict[str, tuple[float, str]] = {}
_JWKS_RAW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_AUTH_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class AuthenticatedIdentity:
    user_id: str | None = None
    tenant_id: str | None = None


def _auth_mode() -> str:
    return os.getenv("CLOUD_AGENT_AUTH_MODE", "local").strip().lower()


def _auth_strategy() -> str:
    return os.getenv("CLOUD_AGENT_AUTH_STRATEGY", "gateway").strip().lower()


def _env_header(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def _env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or default


def _env_float(name: str, default: float) -> float:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _clean_header_value(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _is_production_mode() -> bool:
    return _auth_mode() in {"prod", "production"}


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication_required",
    )


def _stale_while_error_enabled() -> bool:
    raw = _env_value("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR")
    return raw is not None and raw.lower() in {"true", "1", "yes", "on"}


def _emit_auth_degradation(component: str, operation: str, error_type: str) -> None:
    import logging

    logger = logging.getLogger("cloud_agent.auth")
    logger.warning(
        "[Degradation] event_type=degradation component=%s operation=%s status=unavailable error_type=%s",
        component,
        operation,
        error_type,
    )


def _jwt_algorithms() -> list[str]:
    raw = _env_value("CLOUD_AGENT_AUTH_JWT_ALGORITHMS")
    if raw is None:
        return list(DEFAULT_JWT_ALGORITHMS)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(DEFAULT_JWT_ALGORITHMS)


def _oidc_algorithms() -> list[str]:
    raw = _env_value("CLOUD_AGENT_AUTH_OIDC_ALGORITHMS")
    if raw is None:
        return list(DEFAULT_OIDC_ALGORITHMS)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(DEFAULT_OIDC_ALGORITHMS)


def _bearer_token(headers: Mapping[str, str]) -> str:
    authorization = _clean_header_value(
        headers.get("Authorization") or headers.get("authorization")
    )
    if authorization is None:
        raise _auth_error()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _auth_error()
    return token.strip()


def _jwt_decode_options(issuer: str | None, audience: str | None) -> dict[str, bool]:
    return {
        "verify_aud": audience is not None,
        "verify_iss": issuer is not None,
    }


def _decode_with_pyjwt(
    token: str,
    key: Any,
    *,
    algorithms: list[str],
) -> dict[str, Any]:
    try:
        import jwt
    except Exception as exc:
        raise _auth_error() from exc

    issuer = _env_value("CLOUD_AGENT_AUTH_JWT_ISSUER")
    audience = _env_value("CLOUD_AGENT_AUTH_JWT_AUDIENCE")
    try:
        decoded = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            issuer=issuer,
            audience=audience,
            options=_jwt_decode_options(issuer, audience),
        )
    except Exception as exc:
        raise _auth_error() from exc
    return decoded if isinstance(decoded, dict) else {}


def _decode_jwt(token: str) -> dict[str, Any]:
    secret = _env_value("CLOUD_AGENT_AUTH_JWT_SECRET")
    if secret is None:
        raise _auth_error()
    return _decode_with_pyjwt(token, secret, algorithms=_jwt_algorithms())


def _fetch_discovery_jwks_url(discovery_url: str) -> str:
    ttl = _env_float("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", DEFAULT_JWKS_CACHE_SECONDS)
    now = time.monotonic()
    with _AUTH_CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(discovery_url)
        if cached and cached[0] > now:
            return cached[1]

    try:
        with urlopen(
            discovery_url,
            timeout=_env_float("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", DEFAULT_JWKS_TIMEOUT_SECONDS),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise _auth_error() from exc

    jwks_uri = _clean_header_value(str(payload.get("jwks_uri") or ""))
    if jwks_uri is None:
        raise _auth_error()
    with _AUTH_CACHE_LOCK:
        _DISCOVERY_CACHE[discovery_url] = (now + ttl, jwks_uri)
    return jwks_uri


def _jwks_url() -> str:
    explicit_url = _env_value("CLOUD_AGENT_AUTH_JWKS_URL")
    if explicit_url:
        return explicit_url
    discovery_url = _env_value("CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL")
    if discovery_url:
        return _fetch_discovery_jwks_url(discovery_url)
    raise _auth_error()


def _jwk_client(jwks_url: str):
    try:
        from jwt import PyJWKClient
    except Exception as exc:
        raise _auth_error() from exc

    cache_seconds = _env_float("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", DEFAULT_JWKS_CACHE_SECONDS)
    timeout_seconds = _env_float("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", DEFAULT_JWKS_TIMEOUT_SECONDS)
    key = (jwks_url, cache_seconds, timeout_seconds)
    with _AUTH_CACHE_LOCK:
        client = _JWK_CLIENTS.get(key)
        if client is None:
            client = PyJWKClient(
                jwks_url,
                cache_jwk_set=True,
                lifespan=cache_seconds,
                timeout=timeout_seconds,
            )
            _JWK_CLIENTS[key] = client
        return client


def _fetch_jwks_raw(jwks_url: str) -> dict[str, Any]:
    ttl = _env_float("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", DEFAULT_JWKS_CACHE_SECONDS)
    timeout = _env_float("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", DEFAULT_JWKS_TIMEOUT_SECONDS)
    now = time.monotonic()

    with _AUTH_CACHE_LOCK:
        cached = _JWKS_RAW_CACHE.get(jwks_url)
        if cached and cached[0] > now:
            return cached[1]

    try:
        with urlopen(jwks_url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        if not _stale_while_error_enabled():
            raise

        if cached is None:
            _emit_auth_degradation("jwks", "fetch_jwks_stale_while_error_no_cache", type(exc).__name__)
            raise

        _emit_auth_degradation("jwks", "fetch_jwks_stale_while_error", type(exc).__name__)
        return cached[1]

    if not isinstance(data, dict) or not data.get("keys"):
        raise ValueError("invalid_jwks_format")

    with _AUTH_CACHE_LOCK:
        _JWKS_RAW_CACHE[jwks_url] = (now + ttl, data)
    return data


def _find_signing_key_from_stale_jwks(jwks_data: dict[str, Any], kid: str):
    try:
        from jwt import PyJWK
    except Exception as exc:
        raise _auth_error() from exc

    keys = jwks_data.get("keys")
    if not isinstance(keys, list):
        raise _auth_error()

    for key_data in keys:
        if not isinstance(key_data, dict):
            continue
        if key_data.get("kid") == kid:
            return PyJWK(key_data).key

    raise _auth_error()


def _kid_from_token_header(token: str) -> str:
    try:
        import jwt
    except Exception as exc:
        raise _auth_error() from exc

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise _auth_error() from exc

    kid = header.get("kid")
    if not kid or not isinstance(kid, str):
        raise _auth_error()
    return kid


def _decode_oidc_jwt(token: str) -> dict[str, Any]:
    jwks_url = _jwks_url()

    try:
        jwks_data = _fetch_jwks_raw(jwks_url)
    except Exception as exc:
        raise _auth_error() from exc

    kid = _kid_from_token_header(token)
    signing_key = _find_signing_key_from_stale_jwks(jwks_data, kid)

    return _decode_with_pyjwt(token, signing_key, algorithms=_oidc_algorithms())


def _identity_from_payload(payload: Mapping[str, Any]) -> AuthenticatedIdentity:
    user_claim = _env_value("CLOUD_AGENT_AUTH_JWT_USER_CLAIM", DEFAULT_JWT_USER_CLAIM)
    tenant_claim = _env_value("CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM", DEFAULT_JWT_TENANT_CLAIM)
    authenticated_user_id = _clean_header_value(str(payload.get(user_claim) or ""))
    if authenticated_user_id is None:
        raise _auth_error()
    tenant_value = payload.get(tenant_claim)
    authenticated_tenant_id = _clean_header_value(str(tenant_value or "")) or "default_tenant"
    return AuthenticatedIdentity(
        user_id=authenticated_user_id,
        tenant_id=authenticated_tenant_id,
    )


def _resolve_jwt_identity(headers: Mapping[str, str]) -> AuthenticatedIdentity:
    return _identity_from_payload(_decode_jwt(_bearer_token(headers)))


def _resolve_oidc_identity(headers: Mapping[str, str]) -> AuthenticatedIdentity:
    return _identity_from_payload(_decode_oidc_jwt(_bearer_token(headers)))


def resolve_authenticated_identity(
    headers: Mapping[str, str],
    *,
    debug_user_id: str | None = None,
    debug_tenant_id: str | None = None,
) -> AuthenticatedIdentity:
    """
    Resolve trusted identity at the HTTP boundary.

    Local mode keeps existing demo behavior: X-User-Id / X-Tenant-Id can be
    used as a debug authenticated identity, and request body user_id remains
    accepted downstream when these headers are absent.

    Production mode only trusts explicit gateway-authenticated headers. The
    request body user_id and debug headers are ignored by design.
    """
    if not _is_production_mode():
        return AuthenticatedIdentity(
            user_id=_clean_header_value(debug_user_id),
            tenant_id=_clean_header_value(debug_tenant_id),
        )

    if _auth_strategy() in {"jwt", "bearer"}:
        return _resolve_jwt_identity(headers)
    if _auth_strategy() in {"oidc", "jwks"}:
        return _resolve_oidc_identity(headers)

    user_header = _env_header("CLOUD_AGENT_AUTH_USER_HEADER", DEFAULT_AUTH_USER_HEADER)
    tenant_header = _env_header("CLOUD_AGENT_AUTH_TENANT_HEADER", DEFAULT_AUTH_TENANT_HEADER)

    authenticated_user_id = _clean_header_value(headers.get(user_header))
    if authenticated_user_id is None:
        raise _auth_error()

    return AuthenticatedIdentity(
        user_id=authenticated_user_id,
        tenant_id=_clean_header_value(headers.get(tenant_header)),
    )


def resolve_authenticated_identity_from_request(
    request: Request,
    *,
    debug_user_id: str | None = None,
    debug_tenant_id: str | None = None,
) -> AuthenticatedIdentity:
    return resolve_authenticated_identity(
        request.headers,
        debug_user_id=debug_user_id,
        debug_tenant_id=debug_tenant_id,
    )
