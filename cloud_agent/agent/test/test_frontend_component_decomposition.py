from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"
MESSAGE_LIST_PATH = FRONTEND_SRC / "components" / "MessageList.vue"
SCENARIO_GRID_PATH = FRONTEND_SRC / "components" / "ScenarioGrid.vue"


def test_app_vue_delegates_messages_and_scenarios_to_components():
    app_text = APP_VUE_PATH.read_text(encoding="utf-8")

    assert "import MessageList from './components/MessageList.vue'" in app_text
    assert "import ScenarioGrid from './components/ScenarioGrid.vue'" in app_text
    assert "<MessageList" in app_text
    assert "<ScenarioGrid" in app_text

    assert 'class="message-list"' not in app_text
    assert 'class="scenario-card"' not in app_text
    assert 'v-for="(msg, index) in messages"' not in app_text
    assert "renderMarkdown(msg.content)" not in app_text
    assert "scenarioIconComponents" not in app_text


def test_message_list_component_owns_chat_rendering_contract():
    assert MESSAGE_LIST_PATH.exists()
    component_text = MESSAGE_LIST_PATH.read_text(encoding="utf-8")

    assert 'class="message-list"' in component_text
    assert 'v-for="(msg, index) in messages"' in component_text
    assert "renderMarkdown(msg.content)" in component_text
    assert "defineExpose" in component_text
    assert "scrollToBottom" in component_text
    assert "<slot" in component_text


def test_scenario_grid_component_owns_scenario_card_contract():
    assert SCENARIO_GRID_PATH.exists()
    component_text = SCENARIO_GRID_PATH.read_text(encoding="utf-8")

    assert 'class="scenario-card"' in component_text
    assert 'v-for="scenario in scenarios"' in component_text
    assert "defineEmits" in component_text
    assert "'select-query'" in component_text
    assert "scenarioIconComponents" in component_text
