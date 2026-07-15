from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"
CHAT_SIDEBAR_PATH = FRONTEND_SRC / "components" / "ChatSidebar.vue"
CHAT_INPUT_PATH = FRONTEND_SRC / "components" / "ChatInput.vue"


def test_app_vue_delegates_sidebar_and_input_to_components():
    app_text = APP_VUE_PATH.read_text(encoding="utf-8")

    assert "import ChatSidebar from './components/ChatSidebar.vue'" in app_text
    assert "import ChatInput from './components/ChatInput.vue'" in app_text
    assert "<ChatSidebar" in app_text
    assert "<ChatInput" in app_text
    assert '@new-session="createNewSession"' in app_text
    assert '@switch-session="switchSession"' in app_text
    assert '@send="sendQuery"' in app_text

    assert '<el-aside width="260px" class="sidebar">' not in app_text
    assert 'class="input-area"' not in app_text
    assert "ChatDotRound" not in app_text
    assert "Position" not in app_text
    assert "ElInput" not in app_text


def test_chat_sidebar_component_owns_session_navigation_contract():
    assert CHAT_SIDEBAR_PATH.exists()
    component_text = CHAT_SIDEBAR_PATH.read_text(encoding="utf-8")

    assert 'class="sidebar"' in component_text
    assert 'v-for="session in sessions"' in component_text
    assert "type { ChatSession }" in component_text
    assert "defineProps" in component_text
    assert "defineEmits" in component_text
    assert "'new-session'" in component_text
    assert "'switch-session'" in component_text
    assert "ChatDotRound" in component_text
    assert "Plus" in component_text


def test_chat_input_component_owns_prompt_entry_contract():
    assert CHAT_INPUT_PATH.exists()
    component_text = CHAT_INPUT_PATH.read_text(encoding="utf-8")

    assert 'class="input-area"' in component_text
    assert 'v-model="query"' in component_text
    assert "defineProps" in component_text
    assert "defineEmits" in component_text
    assert "'update:modelValue'" in component_text
    assert "'send'" in component_text
    assert "handleEnter" in component_text
    assert "shiftKey" in component_text
    assert "Position" in component_text
