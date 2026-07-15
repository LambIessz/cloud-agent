from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent"


def test_frontend_browser_smoke_has_playwright_assets_and_command():
    package_text = (FRONTEND_ROOT / "package.json").read_text(encoding="utf-8")
    config_text = (FRONTEND_ROOT / "playwright.config.ts").read_text(encoding="utf-8")
    mock_server_text = (
        FRONTEND_ROOT / "scripts" / "mock-chat-sse-server.mjs"
    ).read_text(encoding="utf-8")
    spec_text = (
        FRONTEND_ROOT / "src" / "smoke" / "browser-smoke.spec.ts"
    ).read_text(encoding="utf-8")

    assert '"smoke:browser"' in package_text
    assert "@playwright/test" in package_text
    assert "mock-chat-sse-server.mjs" in config_text
    assert "VITE_BACKEND_URL" in config_text
    assert "PLAYWRIGHT_FRONTEND_PORT" in config_text
    assert "PLAYWRIGHT_BACKEND_PORT" in config_text
    assert "/readyz" in mock_server_text
    assert "/api/chat" in mock_server_text
    assert "text/event-stream" in mock_server_text
    assert "page.goto" in spec_text
    assert "waitForResponse" in spec_text
    assert ".message-row.assistant" in spec_text


def test_vite_proxy_target_can_be_overridden_for_browser_smoke():
    vite_text = (FRONTEND_ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert "process.env.VITE_BACKEND_URL" in vite_text
    assert "http://localhost:5000" in vite_text


def test_browser_smoke_preserves_failure_diagnostics():
    config_text = (FRONTEND_ROOT / "playwright.config.ts").read_text(encoding="utf-8")

    assert "outputDir: 'test-results'" in config_text
    assert "reporter:" in config_text
    assert "'html'" in config_text
    assert "outputFolder: 'playwright-report'" in config_text
    assert "open: 'never'" in config_text
    assert "trace: 'retain-on-failure'" in config_text
    assert "screenshot: 'only-on-failure'" in config_text
