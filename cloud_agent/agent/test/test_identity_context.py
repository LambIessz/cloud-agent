from core.workflow.identity_context import (
    apply_identity_metadata,
    resolve_identity,
    scoped_session_id,
)


def test_local_mode_uses_request_user_id(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")

    identity = resolve_identity(request_user_id="user_1002", request_tenant_id="tenant_a")

    assert identity.user_id == "user_1002"
    assert identity.tenant_id == "tenant_a"
    assert identity.source == "debug_request"
    assert len(identity.user_id_hash) == 16


def test_authenticated_identity_overrides_request_user_id(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")

    identity = resolve_identity(
        request_user_id="user_from_body",
        request_tenant_id="tenant_from_body",
        authenticated_user_id="user_from_auth",
        authenticated_tenant_id="tenant_from_auth",
    )

    assert identity.user_id == "user_from_auth"
    assert identity.tenant_id == "tenant_from_auth"
    assert identity.source == "authenticated"


def test_production_mode_ignores_request_user_id_without_auth(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "production")

    identity = resolve_identity(request_user_id="user_from_body")

    assert identity.user_id == "anonymous"
    assert identity.source == "anonymous"


def test_apply_identity_metadata_does_not_store_plain_user_id(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")
    identity = resolve_identity(request_user_id="user_1001", request_tenant_id="tenant_a")

    metadata = apply_identity_metadata({"request_id": "req_test"}, identity)

    assert metadata["request_id"] == "req_test"
    assert metadata["tenant_id"] == "tenant_a"
    assert metadata["user_id_hash"] == identity.user_id_hash
    assert metadata["identity_source"] == "debug_request"
    assert "user_id" not in metadata


def test_scoped_session_id_includes_tenant_and_user_hash(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")
    identity = resolve_identity(request_user_id="user_1001", request_tenant_id="tenant_a")

    scoped = scoped_session_id(identity, "session_1")

    assert scoped == f"tenant_a:{identity.user_id_hash}:session_1"
