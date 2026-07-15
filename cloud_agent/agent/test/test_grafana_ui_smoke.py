from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent"


def test_grafana_ui_smoke_is_repeatable_and_does_not_embed_credentials():
    script = (PROJECT_ROOT / "ops" / "grafana_ui_smoke.mjs").read_text(encoding="utf-8")
    package = (FRONTEND_ROOT / "package.json").read_text(encoding="utf-8")

    assert '"smoke:grafana"' in package
    for value in (
        "@playwright/test",
        "GRAFANA_USER",
        "GRAFANA_PASSWORD",
        "Cloud Agent Overview",
        "LLM calls",
        "MCP tool calls",
        "page.screenshot",
        "MissingGrafanaCredentials",
    ):
        assert value in script

    assert '"admin"' not in script
    assert 'GRAFANA_PASSWORD || "admin"' not in script
