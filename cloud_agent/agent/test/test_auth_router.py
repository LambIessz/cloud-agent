import sys
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


APP_DIR = Path(__file__).resolve().parents[2] / "app"
AGENT_DIR = Path(__file__).resolve().parents[1]
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from router import chat as chat_router
from security.auth import resolve_authenticated_identity


DEFAULT_TEST_JWT_SECRET = "test-secret-32-bytes-minimum-value"
CUSTOM_TEST_JWT_SECRET = "custom-secret-32-bytes-minimum-value"
WRONG_TEST_JWT_SECRET = "wrong-secret-32-bytes-minimum-value"
EXPECTED_TEST_JWT_SECRET = "expected-secret-32-bytes-minimum"


def _jwt_token(payload, secret=DEFAULT_TEST_JWT_SECRET):
    import jwt

    return jwt.encode(payload, secret, algorithm="HS256")


def _rsa_key_and_jwk(kid="test-key"):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.utils import base64url_encode

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _int_to_base64(value: int) -> str:
        data = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64url_encode(data).decode("ascii")

    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_base64(public_numbers.n),
        "e": _int_to_base64(public_numbers.e),
    }
    return private_key, jwk


def _rs256_token(payload, private_key, kid="test-key"):
    import jwt

    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@contextmanager
def _oidc_server(jwk, issuer="http://issuer.local"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                body = {
                    "issuer": issuer,
                    "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks",
                }
            elif self.path == "/jwks":
                body = {"keys": [jwk]}
            else:
                self.send_response(404)
                self.end_headers()
                return

            payload = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api")
    return app


def test_production_auth_requires_gateway_authenticated_user_header(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")

    with pytest.raises(HTTPException) as exc_info:
        resolve_authenticated_identity(
            {},
            debug_user_id="debug_user",
            debug_tenant_id="debug_tenant",
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "authentication_required"
    assert "debug_user" not in str(exc_info.value.detail)


def test_production_auth_uses_configurable_gateway_headers(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_USER_HEADER", "X-Auth-User")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_TENANT_HEADER", "X-Auth-Tenant")

    identity = resolve_authenticated_identity(
        {
            "X-Auth-User": " authenticated_user ",
            "X-Auth-Tenant": " authenticated_tenant ",
            "X-User-Id": "debug_user",
            "X-Tenant-Id": "debug_tenant",
        }
    )

    assert identity.user_id == "authenticated_user"
    assert identity.tenant_id == "authenticated_tenant"


def test_production_jwt_auth_uses_bearer_token_claims(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwt")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_SECRET", DEFAULT_TEST_JWT_SECRET)

    token = _jwt_token({"sub": "jwt_user", "tenant_id": "jwt_tenant"})
    identity = resolve_authenticated_identity(
        {
            "Authorization": f"Bearer {token}",
            "X-Authenticated-User-Id": "gateway_user",
            "X-Authenticated-Tenant-Id": "gateway_tenant",
        },
        debug_user_id="debug_user",
        debug_tenant_id="debug_tenant",
    )

    assert identity.user_id == "jwt_user"
    assert identity.tenant_id == "jwt_tenant"


def test_production_jwt_auth_supports_issuer_audience_and_custom_claims(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwt")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_SECRET", CUSTOM_TEST_JWT_SECRET)
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_AUDIENCE", "cloud-agent")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_USER_CLAIM", "uid")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM", "tid")

    token = _jwt_token(
        {
            "uid": "custom_user",
            "tid": "custom_tenant",
            "iss": "https://issuer.example",
            "aud": "cloud-agent",
        },
        secret=CUSTOM_TEST_JWT_SECRET,
    )
    identity = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

    assert identity.user_id == "custom_user"
    assert identity.tenant_id == "custom_tenant"


def test_production_jwt_auth_rejects_invalid_token_without_leaking_details(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwt")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_SECRET", EXPECTED_TEST_JWT_SECRET)
    token = _jwt_token({"sub": "jwt_user"}, secret=WRONG_TEST_JWT_SECRET)

    with pytest.raises(HTTPException) as exc_info:
        resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "authentication_required"
    assert "jwt_user" not in str(exc_info.value.detail)
    assert token not in str(exc_info.value.detail)


def test_production_oidc_auth_uses_discovery_jwks_and_kid(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="oidc-key")

    with _oidc_server(jwk, issuer="https://issuer.example") as server_url:
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv(
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
            f"{server_url}/.well-known/openid-configuration",
        )
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_AUDIENCE", "cloud-agent")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "60")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")

        token = _rs256_token(
            {
                "sub": "oidc_user",
                "tenant_id": "oidc_tenant",
                "iss": "https://issuer.example",
                "aud": "cloud-agent",
            },
            private_key,
            kid="oidc-key",
        )
        identity = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

    assert identity.user_id == "oidc_user"
    assert identity.tenant_id == "oidc_tenant"


def test_production_oidc_auth_supports_direct_jwks_url_and_custom_claims(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="direct-key")

    with _oidc_server(jwk) as server_url:
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_USER_CLAIM", "uid")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM", "tid")

        token = _rs256_token(
            {
                "uid": "direct_user",
                "tid": "direct_tenant",
            },
            private_key,
            kid="direct-key",
        )
        identity = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

    assert identity.user_id == "direct_user"
    assert identity.tenant_id == "direct_tenant"


def test_production_oidc_auth_rejects_wrong_audience_without_leaking_details(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="reject-key")

    with _oidc_server(jwk, issuer="https://issuer.example") as server_url:
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_AUDIENCE", "expected-audience")

        token = _rs256_token(
            {
                "sub": "oidc_user",
                "tenant_id": "oidc_tenant",
                "iss": "https://issuer.example",
                "aud": "wrong-audience",
            },
            private_key,
            kid="reject-key",
        )
        with pytest.raises(HTTPException) as exc_info:
            resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "authentication_required"
    assert "oidc_user" not in str(exc_info.value.detail)
    assert token not in str(exc_info.value.detail)


def test_chat_endpoint_production_rejects_missing_authenticated_header(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    client = TestClient(_test_app())

    response = client.post(
        "/api/chat",
        json={
            "query": "hello",
            "user_id": "body_user",
            "tenant_id": "body_tenant",
            "session_id": "session_1",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication_required"}
    assert "body_user" not in response.text
    assert "body_tenant" not in response.text


def test_chat_endpoint_production_passes_authenticated_identity_only(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    captured = {}

    async def _fake_stream_chat(
        query,
        user_id,
        session_id,
        *,
        request_id,
        request_tenant_id,
        authenticated_user_id,
        authenticated_tenant_id,
    ):
        captured.update(
            {
                "query": query,
                "user_id": user_id,
                "session_id": session_id,
                "request_id": request_id,
                "request_tenant_id": request_tenant_id,
                "authenticated_user_id": authenticated_user_id,
                "authenticated_tenant_id": authenticated_tenant_id,
            }
        )
        yield 'data: {"done": true}\n\n'

    monkeypatch.setattr(chat_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_test_app())

    response = client.post(
        "/api/chat",
        headers={
            "X-Authenticated-User-Id": "auth_user",
            "X-Authenticated-Tenant-Id": "auth_tenant",
            "X-User-Id": "debug_user",
            "X-Tenant-Id": "debug_tenant",
        },
        json={
            "query": "hello",
            "user_id": "body_user",
            "tenant_id": "body_tenant",
            "session_id": "session_1",
        },
    )

    assert response.status_code == 200
    assert captured["user_id"] == "body_user"
    assert captured["request_tenant_id"] == "body_tenant"
    assert captured["authenticated_user_id"] == "auth_user"
    assert captured["authenticated_tenant_id"] == "auth_tenant"
    assert captured["request_id"].startswith("req_")
    assert "body_user" not in response.text
    assert "auth_user" not in response.text


def test_chat_endpoint_production_jwt_passes_token_identity_only(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwt")
    monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_SECRET", DEFAULT_TEST_JWT_SECRET)
    captured = {}

    async def _fake_stream_chat(
        query,
        user_id,
        session_id,
        *,
        request_id,
        request_tenant_id,
        authenticated_user_id,
        authenticated_tenant_id,
    ):
        captured.update(
            {
                "query": query,
                "user_id": user_id,
                "session_id": session_id,
                "request_id": request_id,
                "request_tenant_id": request_tenant_id,
                "authenticated_user_id": authenticated_user_id,
                "authenticated_tenant_id": authenticated_tenant_id,
            }
        )
        yield 'data: {"done": true}\n\n'

    monkeypatch.setattr(chat_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_test_app())
    token = _jwt_token({"sub": "jwt_user", "tenant_id": "jwt_tenant"})

    response = client.post(
        "/api/chat",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Authenticated-User-Id": "gateway_user",
            "X-Authenticated-Tenant-Id": "gateway_tenant",
        },
        json={
            "query": "hello",
            "user_id": "body_user",
            "tenant_id": "body_tenant",
            "session_id": "session_1",
        },
    )

    assert response.status_code == 200
    assert captured["user_id"] == "body_user"
    assert captured["request_tenant_id"] == "body_tenant"
    assert captured["authenticated_user_id"] == "jwt_user"
    assert captured["authenticated_tenant_id"] == "jwt_tenant"
    assert captured["request_id"].startswith("req_")
    assert "jwt_user" not in response.text
    assert "body_user" not in response.text


def test_chat_endpoint_local_mode_keeps_existing_debug_header_behavior(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")
    captured = {}

    async def _fake_stream_chat(
        query,
        user_id,
        session_id,
        *,
        request_id,
        request_tenant_id,
        authenticated_user_id,
        authenticated_tenant_id,
    ):
        captured.update(
            {
                "user_id": user_id,
                "request_tenant_id": request_tenant_id,
                "authenticated_user_id": authenticated_user_id,
                "authenticated_tenant_id": authenticated_tenant_id,
            }
        )
        yield 'data: {"done": true}\n\n'

    monkeypatch.setattr(chat_router, "stream_chat", _fake_stream_chat)
    client = TestClient(_test_app())

    response = client.post(
        "/api/chat",
        headers={
            "X-User-Id": "debug_user",
            "X-Tenant-Id": "debug_tenant",
        },
        json={
            "query": "hello",
            "user_id": "body_user",
            "tenant_id": "body_tenant",
            "session_id": "session_1",
        },
    )

    assert response.status_code == 200
    assert captured["user_id"] == "body_user"
    assert captured["request_tenant_id"] == "body_tenant"
    assert captured["authenticated_user_id"] == "debug_user"
    assert captured["authenticated_tenant_id"] == "debug_tenant"


# ──────────────────────────────────────────────
#  JWKS Key Rotation fixtures and tests
# ──────────────────────────────────────────────


@contextmanager
def _rotating_oidc_server(issuer="https://issuer.example"):
    import threading

    lock = threading.Lock()
    jwks_state: dict[str, Any] = {"keys": []}

    def set_jwks(jwks_list: list):
        with lock:
            jwks_state["keys"] = jwks_list

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                body = {
                    "issuer": issuer,
                    "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks",
                }
            elif self.path == "/jwks":
                with lock:
                    body = dict(jwks_state)
            else:
                self.send_response(404)
                self.end_headers()
                return

            payload = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", set_jwks
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_oidc_jwks_key_rotation_transition_both_keys_work(monkeypatch):
    private_key_1, jwk_1 = _rsa_key_and_jwk(kid="key-v1")
    private_key_2, jwk_2 = _rsa_key_and_jwk(kid="key-v2")

    with _rotating_oidc_server(issuer="https://issuer.example") as (server_url, set_jwks):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv(
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
            f"{server_url}/.well-known/openid-configuration",
        )
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")

        set_jwks([jwk_1, jwk_2])

        token_1 = _rs256_token(
            {"sub": "user_v1", "tenant_id": "tenant_v1", "iss": "https://issuer.example"},
            private_key_1,
            kid="key-v1",
        )
        token_2 = _rs256_token(
            {"sub": "user_v2", "tenant_id": "tenant_v2", "iss": "https://issuer.example"},
            private_key_2,
            kid="key-v2",
        )

        identity_1 = resolve_authenticated_identity({"Authorization": f"Bearer {token_1}"})
        identity_2 = resolve_authenticated_identity({"Authorization": f"Bearer {token_2}"})

    assert identity_1.user_id == "user_v1"
    assert identity_1.tenant_id == "tenant_v1"
    assert identity_2.user_id == "user_v2"
    assert identity_2.tenant_id == "tenant_v2"


def test_oidc_jwks_key_rotation_old_key_rejected_after_rotation(monkeypatch):
    private_key_1, jwk_1 = _rsa_key_and_jwk(kid="key-v1")
    private_key_2, jwk_2 = _rsa_key_and_jwk(kid="key-v2")

    with _rotating_oidc_server(issuer="https://issuer.example") as (server_url, set_jwks):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv(
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
            f"{server_url}/.well-known/openid-configuration",
        )
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")

        set_jwks([jwk_1])
        import time as _time
        _time.sleep(1.2)

        token_old = _rs256_token(
            {"sub": "old_user", "tenant_id": "old_tenant", "iss": "https://issuer.example"},
            private_key_1,
            kid="key-v1",
        )

        set_jwks([jwk_2])
        _time.sleep(1.2)

        token_new = _rs256_token(
            {"sub": "new_user", "tenant_id": "new_tenant", "iss": "https://issuer.example"},
            private_key_2,
            kid="key-v2",
        )

        with pytest.raises(HTTPException) as exc_info:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_old}"})
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "authentication_required"

        identity_new = resolve_authenticated_identity({"Authorization": f"Bearer {token_new}"})
        assert identity_new.user_id == "new_user"


def test_oidc_jwks_key_rotation_kid_not_in_jwks_rejected(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="key-present")

    with _rotating_oidc_server(issuer="https://issuer.example") as (server_url, set_jwks):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv(
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
            f"{server_url}/.well-known/openid-configuration",
        )
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "60")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")

        set_jwks([jwk])
        wrong_key, _ = _rsa_key_and_jwk(kid="key-not-in-jwks")
        token = _rs256_token(
            {"sub": "user", "tenant_id": "tenant", "iss": "https://issuer.example"},
            wrong_key,
            kid="key-not-in-jwks",
        )

        with pytest.raises(HTTPException) as exc_info:
            resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "authentication_required"


# ──────────────────────────────────────────────
#  Stale-while-error fixtures and tests
# ──────────────────────────────────────────────


@contextmanager
def _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=2):
    import threading

    request_count = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                body = {
                    "issuer": issuer,
                    "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks",
                }
            elif self.path == "/jwks":
                request_count["count"] += 1
                if request_count["count"] > fail_after:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"service temporarily unavailable")
                    return
                body = {"keys": [jwk]}
            else:
                self.send_response(404)
                self.end_headers()
                return

            payload = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", request_count
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_oidc_stale_while_error_enabled_uses_cached_jwks_on_failure(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="stale-key")

    with _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=1) as (server_url, _rc):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR", "true")

        token = _rs256_token(
            {"sub": "stale_user", "tenant_id": "stale_tenant", "iss": "https://issuer.example"},
            private_key,
            kid="stale-key",
        )

        identity_first = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_first.user_id == "stale_user"
        assert identity_first.tenant_id == "stale_tenant"

        import time as _time
        _time.sleep(1.2)

        identity_second = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_second.user_id == "stale_user"
        assert identity_second.tenant_id == "stale_tenant"


def test_oidc_stale_while_error_disabled_fails_on_remote_failure(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="stale-key")

    with _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=1) as (server_url, _rc):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR", "false")

        token = _rs256_token(
            {"sub": "stale_user", "tenant_id": "stale_tenant", "iss": "https://issuer.example"},
            private_key,
            kid="stale-key",
        )

        identity_first = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_first.user_id == "stale_user"

        import time as _time
        _time.sleep(1.2)

        with pytest.raises(HTTPException) as exc_info:
            resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "authentication_required"


def test_oidc_stale_while_error_no_cached_keys_still_fails(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="stale-key")

    with _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=0) as (server_url, _rc):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR", "true")

        token = _rs256_token(
            {"sub": "stale_user", "tenant_id": "stale_tenant", "iss": "https://issuer.example"},
            private_key,
            kid="stale-key",
        )

        with pytest.raises(HTTPException) as exc_info:
            resolve_authenticated_identity({"Authorization": f"Bearer {token}"})

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "authentication_required"
        assert "stale_user" not in str(exc_info.value.detail)


def test_oidc_stale_while_error_preserves_default_401_no_token_leak(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="leak-key")

    with _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=1) as (server_url, _rc):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_URL", f"{server_url}/jwks")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR", "true")

        token = _rs256_token(
            {"sub": "leak_user", "tenant_id": "leak_tenant", "iss": "https://issuer.example"},
            private_key,
            kid="leak-key",
        )

        identity_first = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_first.user_id == "leak_user"

        import time as _time
        _time.sleep(1.2)

        identity_stale = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_stale.user_id == "leak_user"
        assert identity_stale.tenant_id == "leak_tenant"

        assert not isinstance(identity_stale, HTTPException)


def test_oidc_stale_while_error_works_with_discovery_url(monkeypatch):
    private_key, jwk = _rsa_key_and_jwk(kid="disc-stale-key")

    with _failable_oidc_server(jwk, issuer="https://issuer.example", fail_after=1) as (server_url, _rc):
        monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_STRATEGY", "oidc")
        monkeypatch.setenv(
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL",
            f"{server_url}/.well-known/openid-configuration",
        )
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWT_ISSUER", "https://issuer.example")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS", "1")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR", "true")

        token = _rs256_token(
            {"sub": "disc_user", "tenant_id": "disc_tenant", "iss": "https://issuer.example"},
            private_key,
            kid="disc-stale-key",
        )

        identity_first = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_first.user_id == "disc_user"

        import time as _time
        _time.sleep(1.2)

        identity_second = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
        assert identity_second.user_id == "disc_user"
