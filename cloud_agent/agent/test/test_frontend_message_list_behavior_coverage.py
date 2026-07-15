from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
MESSAGE_LIST_TEST_PATH = FRONTEND_SRC / "components" / "MessageList.test.ts"


def test_message_list_has_component_behavior_coverage():
    assert MESSAGE_LIST_TEST_PATH.exists()

    test_text = MESSAGE_LIST_TEST_PATH.read_text(encoding="utf-8")

    for pattern in (
        "empty-actions",
        ".empty-state",
        ".message-content",
        ".message-status",
        "isLoading",
        "scrollToBottom",
    ):
        assert pattern in test_text
