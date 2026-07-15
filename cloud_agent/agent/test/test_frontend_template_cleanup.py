from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"

STARTER_TEMPLATE_FILES = {
    "components/HelloWorld.vue",
    "components/TheWelcome.vue",
    "components/WelcomeItem.vue",
    "components/icons/IconCommunity.vue",
    "components/icons/IconDocumentation.vue",
    "components/icons/IconEcosystem.vue",
    "components/icons/IconSupport.vue",
    "components/icons/IconTooling.vue",
}

STARTER_TEMPLATE_PATTERNS = {
    "You\u2019ve successfully created a project",
    "vite.dev/guide/features.html",
    "vuejs.org/sponsor",
    "Vue Land",
    "Awesome Vue",
    "__open-in-editor?file=README.md",
}


def _frontend_text_files():
    for path in FRONTEND_SRC.rglob("*"):
        if path.is_file() and path.suffix in {".vue", ".ts", ".js", ".mjs", ".css"}:
            yield path


def test_vite_starter_components_are_removed():
    existing = [
        relative_path
        for relative_path in sorted(STARTER_TEMPLATE_FILES)
        if (FRONTEND_SRC / relative_path).exists()
    ]

    assert existing == []


def test_frontend_sources_do_not_contain_vite_starter_template_copy():
    offenders = []

    for path in _frontend_text_files():
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(FRONTEND_SRC).as_posix()
        for pattern in STARTER_TEMPLATE_PATTERNS:
            if pattern in text:
                offenders.append(f"{relative_path}: {pattern}")

    assert offenders == []
