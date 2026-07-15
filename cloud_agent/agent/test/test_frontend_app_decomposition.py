from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"


def test_app_vue_delegates_static_scenarios_and_chat_streaming():
    app_text = APP_VUE_PATH.read_text(encoding="utf-8")
    controller_text = (FRONTEND_SRC / "composables" / "useChatController.js").read_text(encoding="utf-8")

    assert "from './data/scenarios.js'" in app_text
    assert "from './composables/useChatController.js'" in app_text
    assert "scenarioGroups" in app_text
    assert "useChatController" in app_text
    assert "streamChat" in controller_text
    assert "applySsePayload" in controller_text

    assert "const response = await fetch" not in app_text
    assert "streamChat" not in app_text
    assert "applySsePayload" not in app_text
    assert "fetch('/api/chat'" not in app_text
    assert 'fetch("/api/chat"' not in app_text
    assert "sendQuery('云服务器ECS有哪些基本属性？')" not in app_text
