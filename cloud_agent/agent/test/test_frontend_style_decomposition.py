from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"
CHAT_CSS_PATH = FRONTEND_SRC / "assets" / "chat.css"
MAIN_CSS_PATH = FRONTEND_SRC / "assets" / "main.css"

CHAT_STYLE_SELECTORS = {
    ".chat-container",
    ".app-shell",
    ".sidebar",
    ".message-list",
    ".scenario-card",
    ".message-row",
    ".message-bubble",
    ".input-area",
}

STARTER_GLOBAL_CSS_PATTERNS = {
    "max-width: 1280px",
    "grid-template-columns: 1fr 1fr",
    "place-items: center",
    "hsla(160, 100%, 37%",
    ".green",
}


def test_app_vue_uses_external_scoped_chat_stylesheet():
    app_text = APP_VUE_PATH.read_text(encoding="utf-8")

    assert '<style scoped src="./assets/chat.css"></style>' in app_text
    assert "<style scoped>\n.chat-container" not in app_text
    assert "radial-gradient(circle at 10% 20%" not in app_text
    assert ".message-bubble :deep" not in app_text


def test_chat_stylesheet_contains_chat_layout_and_message_styles():
    assert CHAT_CSS_PATH.exists()
    css_text = CHAT_CSS_PATH.read_text(encoding="utf-8")

    for selector in CHAT_STYLE_SELECTORS:
        assert re.search(rf"{re.escape(selector)}\s*\{{", css_text)

    assert "radial-gradient(circle at 10% 20%" in css_text
    assert ".message-bubble :deep(p)" in css_text


def test_global_main_css_does_not_keep_vite_starter_layout():
    main_text = MAIN_CSS_PATH.read_text(encoding="utf-8")

    for pattern in STARTER_GLOBAL_CSS_PATTERNS:
        assert pattern not in main_text

    assert "@import './base.css';" in main_text
