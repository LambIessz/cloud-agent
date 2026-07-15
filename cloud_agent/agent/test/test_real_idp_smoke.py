import importlib.util
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = PROJECT_ROOT / "ops" / "auth" / "real_idp_smoke.py"
README_PATH = PROJECT_ROOT / "README.md"
RUNBOOK_PATH = PROJECT_ROOT / "ops" / "local_dev_runbook.md"
RELEASE_CHECKLIST_PATH = PROJECT_ROOT / "ops" / "release_checklist.md"
HANDOFF_PATH = PROJECT_ROOT / "API_SWITCH_HANDOFF.md"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("real_idp_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_real_idp_smoke_passes_with_custom_claims_audience_and_restores_env(monkeypatch):
    smoke = _load_smoke()
    monkeypatch.setenv("CLOUD_AGENT_AUTH_MODE", "local")
    monkeypatch.delenv("CLOUD_AGENT_AUTH_STRATEGY", raising=False)

    report = smoke.run_smoke(
        env={
            "CLOUD_AGENT_AUTH_JWT_USER_CLAIM": "uid",
            "CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM": "tid",
            "CLOUD_AGENT_AUTH_JWT_AUDIENCE": "cloud-agent",
        },
        cache_seconds=0.05,
        timeout_seconds=1.0,
    )

    assert report.status == "ready"
    assert report.summary == {"passed": 11, "failed": 0}
    assert {check.name for check in report.checks} >= {
        "valid_oidc_token",
        "wrong_kid_rejected",
        "wrong_audience_rejected",
        "rotation_transition_accepts_both_keys",
        "rotation_rejects_removed_key",
        "rotation_accepts_new_key",
        "stale_while_error_uses_cached_jwks",
        "stale_while_error_disabled_rejects",
        "malformed_token_rejected",
        "missing_authorization_rejected",
        "provider_was_used",
    }
    assert os.environ["CLOUD_AGENT_AUTH_MODE"] == "local"
    assert "CLOUD_AGENT_AUTH_STRATEGY" not in os.environ


def test_real_idp_smoke_loads_env_file_and_writes_artifact(tmp_path):
    smoke = _load_smoke()
    env_file = tmp_path / "cloud_agent.env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUD_AGENT_AUTH_MODE=production",
                "CLOUD_AGENT_AUTH_STRATEGY=oidc",
                "CLOUD_AGENT_AUTH_JWT_USER_CLAIM=uid",
                "CLOUD_AGENT_AUTH_JWT_TENANT_CLAIM=tid",
            ]
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "real-idp-smoke.json"

    env = smoke.merge_env(env_file, process_env={})
    report = smoke.run_smoke(env=env, cache_seconds=0.05, timeout_seconds=1.0)
    smoke.write_artifact(artifact, report)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["summary"]["failed"] == 0
    assert payload["checks"][0]["name"] == "valid_oidc_token"


def test_real_idp_smoke_output_does_not_leak_token_claim_values():
    smoke = _load_smoke()
    report = smoke.run_smoke(env={}, cache_seconds=0.05, timeout_seconds=1.0)

    rendered = smoke.format_text(report) + smoke.format_json(report)
    forbidden = {
        "smoke_user",
        "smoke_tenant",
        "wrong_kid_user",
        "wrong_audience_user",
        "rotation_user_v1",
        "rotation_user_v2",
        "Bearer ",
        "eyJ",
    }
    assert not any(value in rendered for value in forbidden)


def test_real_idp_smoke_is_documented():
    texts = [
        README_PATH.read_text(encoding="utf-8"),
        RUNBOOK_PATH.read_text(encoding="utf-8"),
        RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8"),
        HANDOFF_PATH.read_text(encoding="utf-8"),
    ]

    for text in texts:
        assert "ops/auth/real_idp_smoke.py" in text
    assert ".codex-run/real-idp-smoke.json" in RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")
