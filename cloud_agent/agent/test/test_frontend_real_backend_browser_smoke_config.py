from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


def test_real_backend_browser_smoke_has_command_config_and_spec():
    package_text = (FRONTEND_ROOT / "package.json").read_text(encoding="utf-8")
    config_path = FRONTEND_ROOT / "playwright.real-backend.config.ts"
    spec_path = FRONTEND_ROOT / "src" / "smoke" / "real-backend-smoke.spec.ts"

    assert '"smoke:browser:real-backend"' in package_text
    assert "playwright.real-backend.config.ts" in package_text
    assert config_path.exists()
    assert spec_path.exists()


def test_real_backend_browser_smoke_starts_fastapi_and_vite_safely():
    config_text = (
        FRONTEND_ROOT / "playwright.real-backend.config.ts"
    ).read_text(encoding="utf-8")

    required_snippets = {
        "PLAYWRIGHT_REAL_BACKEND_PORT",
        "PLAYWRIGHT_REAL_FRONTEND_PORT",
        "python -X utf8 -m uvicorn app_main:app",
        "cwd: backendCwd",
        "DEEPSEEK_API_KEY: 'ci-placeholder'",
        "REDIS_URL: 'redis://127.0.0.1:6379'",
        "CLOUD_AGENT_SMOKE_FAKE_GRAPH: 'true'",
        "CLOUD_AGENT_AUTH_MODE: 'local'",
        "CLOUD_AGENT_CORS_ORIGINS: frontendUrl",
        "CLOUD_AGENT_SEMANTIC_CACHE_ENABLED: 'false'",
        "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED: 'false'",
        "CLOUD_AGENT_VECTOR_SEARCH_ENABLED: 'false'",
        "CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED: 'false'",
        "CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED: 'false'",
        "CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED: 'false'",
        "VITE_BACKEND_URL: backendUrl",
        "real-backend-smoke.spec.ts",
        "outputDir: 'test-results-real-backend'",
        "outputFolder: 'playwright-report-real-backend'",
        "trace: 'retain-on-failure'",
        "screenshot: 'only-on-failure'",
    }

    for snippet in required_snippets:
        assert snippet in config_text


def test_real_backend_browser_smoke_exercises_rendered_chat_without_mock_server():
    spec_text = (
        FRONTEND_ROOT / "src" / "smoke" / "real-backend-smoke.spec.ts"
    ).read_text(encoding="utf-8")

    required_snippets = {
        "今天天气怎么样",
        "page.goto('/')",
        "waitForResponse",
        "/api/chat",
        ".message-row.user",
        ".message-row.assistant",
        "not.toContainText('browser smoke reply')",
        "assistantText.length",
        "testInfo.attach('real-backend-diagnostics.json'",
        "writeFile(",
        "mkdir(",
        "real-backend-diagnostics.json",
        "response.headers()['x-request-id']",
            "/readyz",
            "/api/metrics",
            "requestMetrics",
            "degradationMetrics",
            "page.on('console'",
            "page.on('pageerror'",
            "page.on('requestfailed'",
            "frontendDiagnostics",
        }

    for snippet in required_snippets:
        assert snippet in spec_text


def test_real_backend_browser_smoke_artifacts_are_ignored():
    gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "cloud_agent/front/**/test-results-real-backend/" in gitignore
    assert "cloud_agent/front/**/playwright-report-real-backend/" in gitignore


def test_frontend_type_check_excludes_playwright_smoke_specs():
    tsconfig_text = (FRONTEND_ROOT / "tsconfig.app.json").read_text(encoding="utf-8")

    assert '"exclude": ["src/**/__tests__/*", "src/smoke/**"]' in tsconfig_text
