from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent"
APP_TEST_PATH = FRONTEND_ROOT / "src" / "App.test.ts"
VITEST_CONFIG_PATH = FRONTEND_ROOT / "vitest.config.ts"


def test_app_vue_has_page_wiring_integration_coverage():
    assert APP_TEST_PATH.exists()

    test_text = APP_TEST_PATH.read_text(encoding="utf-8")

    for pattern in (
        "mount(App",
        "ChatInput",
        "ScenarioGrid",
        "ChatSidebar",
        "fetch",
        ".scenario-item",
        ".session-item",
    ):
        assert pattern in test_text


def test_vitest_runs_app_wiring_tests():
    config_text = VITEST_CONFIG_PATH.read_text(encoding="utf-8")

    assert "src/App.test.ts" in config_text
