from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"


def test_app_vue_delegates_session_state_to_composable():
    app_text = APP_VUE_PATH.read_text(encoding="utf-8")

    assert "from './composables/useChatSessions.js'" in app_text
    assert "useChatSessions()" in app_text

    assert "const currentSessionId = ref(" not in app_text
    assert "const messages = ref(" not in app_text
    assert "const sessions = ref(" not in app_text
    assert "const createNewSession = ()" not in app_text
    assert "const switchSession = (id: string)" not in app_text


def test_frontend_session_persistence_tests_are_registered():
    package_json = (
        PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "package.json"
    ).read_text(encoding="utf-8")

    assert "test:sessions" in package_json
    assert "src/composables/useChatSessions.test.mjs" in package_json
