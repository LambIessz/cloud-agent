from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent"
FRONTEND_SRC = FRONTEND_ROOT / "src"
BASE_CSS_PATH = FRONTEND_SRC / "assets" / "base.css"
LOGO_PATH = FRONTEND_SRC / "assets" / "logo.svg"

STARTER_BASE_CSS_PATTERNS = {
    "github.com/vuejs/theme",
    "--vt-c-",
    "--section-gap",
    "--color-background",
    "--color-heading",
    "--color-border",
    "prefers-color-scheme",
    "background-color 0.5s",
}

REQUIRED_BASE_CSS_PATTERNS = {
    "*::before",
    "box-sizing: border-box",
    "min-width: 320px",
    "min-height: 100vh",
    "overflow: hidden",
    "font-family:",
    "-webkit-font-smoothing: antialiased",
}


def _frontend_text_files():
    for path in FRONTEND_SRC.rglob("*"):
        if path.is_file() and path.suffix in {".vue", ".ts", ".js", ".mjs", ".css", ".html"}:
            yield path
    index_html = FRONTEND_ROOT / "index.html"
    if index_html.exists():
        yield index_html


def test_unused_vite_logo_asset_is_removed_and_unreferenced():
    assert not LOGO_PATH.exists()

    offenders = []
    for path in _frontend_text_files():
        text = path.read_text(encoding="utf-8")
        if "logo.svg" in text:
            offenders.append(path.relative_to(FRONTEND_ROOT).as_posix())

    assert offenders == []


def test_base_css_contains_only_app_level_reset_not_vue_starter_theme():
    text = BASE_CSS_PATH.read_text(encoding="utf-8")

    for pattern in STARTER_BASE_CSS_PATTERNS:
        assert pattern not in text

    for pattern in REQUIRED_BASE_CSS_PATTERNS:
        assert pattern in text
