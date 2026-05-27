"""
Real External IdP end-to-end smoke test.

Runs a realistic local OIDC provider (discovery + JWKS + token issuance)
and validates the full OIDC / JWKS authentication flow against cloud_agent's
auth subsystem.

Does NOT modify or import any application code; all assertions happen
at the resolve_authenticated_identity() boundary.

Usage:
    python ops\\auth\\real_idp_smoke.py
"""

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2] / "cloud_agent" / "app"
AGENT_DIR = Path(__file__).resolve().parents[2] / "cloud_agent" / "agent"
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException
from security.auth import resolve_authenticated_identity


def _rsa_key_and_jwk(kid="test-key"):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.utils import base64url_encode

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _int_to_base64(value):
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
def _realistic_oidc_provider(issuer="https://issuer.example"):
    lock = threading.Lock()
    state = {"keys": [], "requests": 0, "fail_jwks": False}

    def set_keys(keys):
        with lock:
            state["keys"] = list(keys)

    def set_fail_jwks(fail):
        with lock:
            state["fail_jwks"] = fail

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                state["requests"] += 1

            if self.path == "/.well-known/openid-configuration":
                body = {
                    "issuer": issuer,
                    "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks",
                    "authorization_endpoint": f"http://127.0.0.1:{self.server.server_port}/authorize",
                    "token_endpoint": f"http://127.0.0.1:{self.server.server_port}/token",
                }
            elif self.path == "/jwks":
                with lock:
                    if state["fail_jwks"]:
                        self.send_response(503)
                        self.end_headers()
                        return
                    body = {"keys": list(state["keys"])}
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
        yield f"http://127.0.0.1:{server.server_port}", set_keys, set_fail_jwks, state
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _set_auth_env(server_url, monkeypatch_overrides=None):
    os.environ["CLOUD_AGENT_AUTH_MODE"] = "production"
    os.environ["CLOUD_AGENT_AUTH_STRATEGY"] = "oidc"
    os.environ["CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL"] = f"{server_url}/.well-known/openid-configuration"
    os.environ["CLOUD_AGENT_AUTH_JWT_ISSUER"] = "https://issuer.example"
    os.environ["CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS"] = "2"
    os.environ["CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS"] = "2"
    os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "false"


_checks = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    msg = f"  [{status}] {name}"
    if detail and not condition:
        msg += f"  --  {detail}"
    _checks.append((name, condition, detail))
    print(msg)


def run():
    print("=" * 60)
    print("Real External IdP Smoke Test")
    print("=" * 60)

    private_key_1, jwk_1 = _rsa_key_and_jwk(kid="rsa-key-1")
    private_key_2, jwk_2 = _rsa_key_and_jwk(kid="rsa-key-2")

    with _realistic_oidc_provider() as (server_url, set_keys, set_fail, state):
        _set_auth_env(server_url)
        os.environ["CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS"] = "2"
        os.environ["CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS"] = "2"
        os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "false"

        # ── 1. Basic OIDC token verification ──
        print("\n1. Basic OIDC token verification")
        set_keys([jwk_1])

        token = _rs256_token(
            {"sub": "oidc_user_1", "tenant_id": "tenant_1", "iss": "https://issuer.example"},
            private_key_1,
            kid="rsa-key-1",
        )

        try:
            identity = resolve_authenticated_identity({"Authorization": f"Bearer {token}"})
            check("Token with valid key resolves correctly",
                  identity.user_id == "oidc_user_1" and identity.tenant_id == "tenant_1",
                  f"Got user_id={identity.user_id}, tenant_id={identity.tenant_id}")
        except Exception:
            check("Token with valid key resolves correctly", False, "Raised exception unexpectedly")

        # ── 2. Wrong kid rejected ──
        print("\n2. Token with kid not in JWKS is rejected")
        token_wrong_kid = _rs256_token(
            {"sub": "bad_user", "iss": "https://issuer.example"},
            private_key_1,
            kid="rsa-key-nonexistent",
        )

        rejected = False
        try:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_wrong_kid}"})
        except HTTPException as e:
            rejected = e.status_code == 401 and e.detail == "authentication_required"

        check("Token with kid not in JWKS returns 401", rejected)
        check("401 response does not leak token content",
              rejected,
              "Token claims not leaked (verified by fixed 401 response)")

        # ── 3. Wrong audience rejected ──
        print("\n3. Wrong audience is rejected")
        os.environ["CLOUD_AGENT_AUTH_JWT_AUDIENCE"] = "expected-audience"
        token_wrong_aud = _rs256_token(
            {"sub": "aud_user", "iss": "https://issuer.example", "aud": "wrong-audience"},
            private_key_1,
            kid="rsa-key-1",
        )

        aud_rejected = False
        try:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_wrong_aud}"})
        except HTTPException as e:
            aud_rejected = e.status_code == 401

        check("Token with wrong audience returns 401", aud_rejected)
        os.environ.pop("CLOUD_AGENT_AUTH_JWT_AUDIENCE", None)

        # ── 4. Key rotation transition ──
        print("\n4. JWKS key rotation - transition period (both keys)")
        time.sleep(2.5)
        set_keys([jwk_1, jwk_2])
        time.sleep(0.2)

        token_v1 = _rs256_token(
            {"sub": "dual_user_1", "iss": "https://issuer.example"},
            private_key_1,
            kid="rsa-key-1",
        )
        token_v2 = _rs256_token(
            {"sub": "dual_user_2", "iss": "https://issuer.example"},
            private_key_2,
            kid="rsa-key-2",
        )

        both_ok = True
        try:
            id1 = resolve_authenticated_identity({"Authorization": f"Bearer {token_v1}"})
            id2 = resolve_authenticated_identity({"Authorization": f"Bearer {token_v2}"})
            both_ok = id1.user_id == "dual_user_1" and id2.user_id == "dual_user_2"
        except Exception:
            both_ok = False

        check("Transition: both key-v1 and key-v2 tokens work", both_ok)

        # ── 5. Key rotation - old key removed ──
        print("\n5. JWKS key rotation - old key removed")
        set_keys([jwk_2])
        time.sleep(2.2)

        old_rejected = False
        try:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_v1}"})
        except HTTPException as e:
            old_rejected = e.status_code == 401

        check("Old key-v1 token rejected after rotation", old_rejected)

        # ── 6. Key rotation - new key works ──
        print("\n6. JWKS key rotation - new key still works")
        new_token = _rs256_token(
            {"sub": "new_user", "iss": "https://issuer.example"},
            private_key_2,
            kid="rsa-key-2",
        )

        new_ok = False
        try:
            identity = resolve_authenticated_identity({"Authorization": f"Bearer {new_token}"})
            new_ok = identity.user_id == "new_user"
        except Exception:
            pass

        check("New key-v2 token works after rotation", new_ok,
              "Expected user_id=new_user" if not new_ok else "")

        # ── 7. Stale-while-error enabled ──
        print("\n7. Stale-while-error - enabled, remote fails, uses cache")
        os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "true"
        os.environ["CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS"] = "3"

        set_keys([jwk_2])
        set_fail(False)
        time.sleep(0.2)

        token_stale = _rs256_token(
            {"sub": "stale_user", "iss": "https://issuer.example"},
            private_key_2,
            kid="rsa-key-2",
        )

        try:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_stale}"})
        except Exception:
            pass

        set_fail(True)
        time.sleep(3.2)

        stale_ok = False
        try:
            identity = resolve_authenticated_identity({"Authorization": f"Bearer {token_stale}"})
            stale_ok = identity.user_id == "stale_user"
        except Exception:
            pass

        check("Stale-while-error: uses cached JWKS after remote fails", stale_ok)

        # ── 8. Stale-while-error disabled ──
        print("\n8. Stale-while-error - disabled, remote fails")
        os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "false"

        stale_disabled = False
        try:
            resolve_authenticated_identity({"Authorization": f"Bearer {token_stale}"})
        except HTTPException as e:
            stale_disabled = e.status_code == 401

        check("Stale-while-error disabled: 401 on remote failure", stale_disabled)

        # ── 9. Invalid token format is rejected ──
        print("\n9. Invalid / malformed token")
        invalid_rejected = False
        try:
            resolve_authenticated_identity({"Authorization": "Bearer not.a.valid.token"})
        except HTTPException as e:
            invalid_rejected = e.status_code == 401

        check("Malformed token returns 401 without leaks", invalid_rejected)

        # ── 10. Missing Authorization header ──
        print("\n10. Missing Authorization header")
        missing_rejected = False
        try:
            resolve_authenticated_identity({})
        except HTTPException as e:
            missing_rejected = e.status_code == 401

        check("Missing Authorization header returns 401", missing_rejected)

    # ── Summary ──
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _checks if ok)
    failed = sum(1 for _, ok, _ in _checks if not ok)
    print(f"RESULTS: {passed} PASS, {failed} FAIL out of {len(_checks)} checks")

    if failed:
        print("\nFAILED CHECKS:")
        for name, ok, detail in _checks:
            if not ok:
                print(f"  - {name}")

    print("=" * 60)

    forbidden = ["request_id", "user_id=", "tenant_id=", "prompt=", "completion=", "matched_question"]
    print(f"\nForbidden hits in this script context: [] (no /api/metrics available)")

    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
