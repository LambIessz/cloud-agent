#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable


PASS = "pass"
FAIL = "fail"


APP_DIR = Path(__file__).resolve().parents[2] / "cloud_agent" / "app"
AGENT_DIR = Path(__file__).resolve().parents[2] / "cloud_agent" / "agent"
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException
from security.auth import resolve_authenticated_identity


class CheckResult:
    def __init__(self, name: str, status: str, detail: str):
        self.name = name
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


class IdpSmokeReport:
    def __init__(self, checks: list[CheckResult]):
        self.checks = checks

    @property
    def summary(self) -> dict[str, int]:
        return {
            "passed": sum(1 for check in self.checks if check.status == PASS),
            "failed": sum(1 for check in self.checks if check.status == FAIL),
        }

    @property
    def status(self) -> str:
        return "failed" if self.summary["failed"] else "ready"

    def exit_code(self) -> int:
        return 1 if self.summary["failed"] else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().lstrip("\ufeff")
        if name:
            values[name] = _strip_optional_quotes(value.strip())
    return values


def merge_env(
    env_file: Path | None,
    process_env: dict[str, str] | None = None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_file is not None:
        merged.update(load_env_file(env_file))
    for name, value in dict(os.environ if process_env is None else process_env).items():
        if str(value).strip():
            merged[name] = value
    return merged


def _rsa_key_and_jwk(kid: str):
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


def _rs256_token(payload: dict[str, Any], private_key: Any, kid: str) -> str:
    import jwt

    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@contextmanager
def _realistic_oidc_provider(issuer: str):
    lock = threading.Lock()
    state: dict[str, Any] = {"keys": [], "requests": 0, "fail_jwks": False}

    def set_keys(keys: list[dict[str, Any]]) -> None:
        with lock:
            state["keys"] = list(keys)

    def set_fail_jwks(fail: bool) -> None:
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

        def log_message(self, *_args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", set_keys, set_fail_jwks, state
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _add_check(checks: list[CheckResult], name: str, condition: bool, detail: str) -> None:
    checks.append(CheckResult(name, PASS if condition else FAIL, detail))


def _expect_401(resolver: Callable[[dict[str, str]], Any], headers: dict[str, str]) -> bool:
    try:
        resolver(headers)
    except HTTPException as error:
        return error.status_code == 401 and error.detail == "authentication_required"
    except Exception:
        return False
    return False


def _apply_auth_env(
    env: dict[str, str],
    *,
    server_url: str,
    issuer: str,
    cache_seconds: float,
    timeout_seconds: float,
) -> dict[str, str]:
    effective = dict(env)
    effective.update(
        {
            "CLOUD_AGENT_AUTH_MODE": "production",
            "CLOUD_AGENT_AUTH_STRATEGY": "oidc",
            "CLOUD_AGENT_AUTH_OIDC_DISCOVERY_URL": f"{server_url}/.well-known/openid-configuration",
            "CLOUD_AGENT_AUTH_JWT_ISSUER": issuer,
            "CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS": str(cache_seconds),
            "CLOUD_AGENT_AUTH_JWKS_TIMEOUT_SECONDS": str(timeout_seconds),
            "CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR": "false",
        }
    )
    os.environ.update(effective)
    return effective


def _token_payload(
    *,
    env: dict[str, str],
    issuer: str,
    subject: str,
    tenant: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    user_claim = env.get("CLOUD_AGENT_AUTH_JWT_USER_CLAIM", "sub").strip() or "sub"
    tenant_claim = env.get("CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM", "tenant_id").strip() or "tenant_id"
    payload: dict[str, Any] = {user_claim: subject, "iss": issuer}
    if tenant is not None:
        payload[tenant_claim] = tenant
    if audience:
        payload["aud"] = audience
    return payload


def run_smoke(
    *,
    env: dict[str, str] | None = None,
    issuer: str = "https://issuer.example",
    cache_seconds: float = 0.2,
    timeout_seconds: float = 2.0,
    resolver: Callable[[dict[str, str]], Any] = resolve_authenticated_identity,
    sleep: Callable[[float], None] = time.sleep,
) -> IdpSmokeReport:
    env = dict(os.environ if env is None else env)
    previous_env = os.environ.copy()
    checks: list[CheckResult] = []
    try:
        private_key_1, jwk_1 = _rsa_key_and_jwk(kid=f"rsa-key-1-{uuid.uuid4().hex[:8]}")
        private_key_2, jwk_2 = _rsa_key_and_jwk(kid=f"rsa-key-2-{uuid.uuid4().hex[:8]}")

        with _realistic_oidc_provider(issuer=issuer) as (server_url, set_keys, set_fail, state):
            effective_env = _apply_auth_env(
                env,
                server_url=server_url,
                issuer=issuer,
                cache_seconds=cache_seconds,
                timeout_seconds=timeout_seconds,
            )
            configured_audience = effective_env.get("CLOUD_AGENT_AUTH_JWT_AUDIENCE", "").strip() or None

            set_keys([jwk_1])
            valid_token = _rs256_token(
                _token_payload(
                    env=effective_env,
                    issuer=issuer,
                    subject="smoke_user",
                    tenant="smoke_tenant",
                    audience=configured_audience,
                ),
                private_key_1,
                kid=jwk_1["kid"],
            )
            try:
                identity = resolver({"Authorization": f"Bearer {valid_token}"})
                valid_identity = bool(identity.user_id and identity.tenant_id)
            except Exception:
                valid_identity = False
            _add_check(checks, "valid_oidc_token", valid_identity, "valid RS256 token resolves")

            wrong_kid_token = _rs256_token(
                _token_payload(
                    env=effective_env,
                    issuer=issuer,
                    subject="wrong_kid_user",
                    audience=configured_audience,
                ),
                private_key_1,
                kid="missing-key",
            )
            _add_check(
                checks,
                "wrong_kid_rejected",
                _expect_401(resolver, {"Authorization": f"Bearer {wrong_kid_token}"}),
                "unknown kid returns sanitized 401",
            )

            os.environ["CLOUD_AGENT_AUTH_JWT_AUDIENCE"] = "expected-audience"
            wrong_audience_token = _rs256_token(
                _token_payload(
                    env=effective_env,
                    issuer=issuer,
                    subject="wrong_audience_user",
                    audience="wrong-audience",
                ),
                private_key_1,
                kid=jwk_1["kid"],
            )
            _add_check(
                checks,
                "wrong_audience_rejected",
                _expect_401(resolver, {"Authorization": f"Bearer {wrong_audience_token}"}),
                "wrong audience returns sanitized 401",
            )
            if configured_audience:
                os.environ["CLOUD_AGENT_AUTH_JWT_AUDIENCE"] = configured_audience
            else:
                os.environ.pop("CLOUD_AGENT_AUTH_JWT_AUDIENCE", None)

            sleep(cache_seconds + 0.05)
            set_keys([jwk_1, jwk_2])
            sleep(0.05)
            token_v1 = _rs256_token(
                _token_payload(
                    env=effective_env,
                    issuer=issuer,
                    subject="rotation_user_v1",
                    audience=configured_audience,
                ),
                private_key_1,
                kid=jwk_1["kid"],
            )
            token_v2 = _rs256_token(
                _token_payload(
                    env=effective_env,
                    issuer=issuer,
                    subject="rotation_user_v2",
                    audience=configured_audience,
                ),
                private_key_2,
                kid=jwk_2["kid"],
            )
            try:
                id1 = resolver({"Authorization": f"Bearer {token_v1}"})
                id2 = resolver({"Authorization": f"Bearer {token_v2}"})
                rotation_transition_ok = bool(id1.user_id and id2.user_id)
            except Exception:
                rotation_transition_ok = False
            _add_check(
                checks,
                "rotation_transition_accepts_both_keys",
                rotation_transition_ok,
                "old and new keys both validate during transition",
            )

            set_keys([jwk_2])
            sleep(cache_seconds + 0.05)
            _add_check(
                checks,
                "rotation_rejects_removed_key",
                _expect_401(resolver, {"Authorization": f"Bearer {token_v1}"}),
                "removed key returns sanitized 401 after cache expiry",
            )

            new_key_ok = False
            try:
                identity = resolver({"Authorization": f"Bearer {token_v2}"})
                new_key_ok = bool(identity.user_id)
            except Exception:
                new_key_ok = False
            _add_check(
                checks,
                "rotation_accepts_new_key",
                new_key_ok,
                "new key still validates after old key removal",
            )

            os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "true"
            set_keys([jwk_2])
            set_fail(False)
            resolver({"Authorization": f"Bearer {token_v2}"})
            set_fail(True)
            sleep(cache_seconds + 0.05)
            stale_ok = False
            try:
                identity = resolver({"Authorization": f"Bearer {token_v2}"})
                stale_ok = bool(identity.user_id)
            except Exception:
                stale_ok = False
            _add_check(
                checks,
                "stale_while_error_uses_cached_jwks",
                stale_ok,
                "cached JWKS validates when remote JWKS fails and stale mode is enabled",
            )

            os.environ["CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR"] = "false"
            _add_check(
                checks,
                "stale_while_error_disabled_rejects",
                _expect_401(resolver, {"Authorization": f"Bearer {token_v2}"}),
                "remote JWKS failure returns sanitized 401 when stale mode is disabled",
            )

            _add_check(
                checks,
                "malformed_token_rejected",
                _expect_401(resolver, {"Authorization": "Bearer not.a.valid.token"}),
                "malformed bearer token returns sanitized 401",
            )
            _add_check(
                checks,
                "missing_authorization_rejected",
                _expect_401(resolver, {}),
                "missing authorization returns sanitized 401",
            )
            _add_check(
                checks,
                "provider_was_used",
                int(state["requests"]) > 0,
                "local OIDC discovery/JWKS provider received requests",
            )
    except ModuleNotFoundError as error:
        _add_check(checks, "dependencies_installed", False, f"{error.name or 'required'} package is not installed")
    except Exception as error:
        _add_check(checks, "idp_smoke_unhandled_error", False, error.__class__.__name__)
    finally:
        os.environ.clear()
        os.environ.update(previous_env)

    return IdpSmokeReport(checks)


def format_text(report: IdpSmokeReport) -> str:
    lines = [
        f"[idp-smoke] cloud_agent OIDC/JWKS auth smoke: {report.status}",
        (
            "[idp-smoke] summary: "
            f"passed={report.summary['passed']} "
            f"failed={report.summary['failed']}"
        ),
    ]
    labels = {PASS: "PASS", FAIL: "FAIL"}
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name} - {check.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: IdpSmokeReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, report: IdpSmokeReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_json(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent local OIDC/JWKS auth smoke")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file to load")
    parser.add_argument("--issuer", default="https://issuer.example", help="Synthetic issuer claim")
    parser.add_argument("--cache-seconds", type=float, default=0.2, help="JWKS cache TTL seconds")
    parser.add_argument("--timeout", type=float, default=2.0, help="JWKS HTTP timeout seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "real-idp-smoke.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write a JSON artifact")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        env = merge_env(args.env_file)
    except OSError as error:
        sys.stderr.write(f"[idp-smoke] failed to read env file: {error}\n")
        return 2

    report = run_smoke(
        env=env,
        issuer=args.issuer,
        cache_seconds=args.cache_seconds,
        timeout_seconds=args.timeout,
    )
    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
