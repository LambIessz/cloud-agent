import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
VITE_CONFIG_PATH = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "vite.config.ts"
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"


def _vite_config_text() -> str:
    return VITE_CONFIG_PATH.read_text(encoding="utf-8")


def test_vue_devtools_is_enabled_for_dev_server_only():
    text = _vite_config_text()
    assert "vueDevTools" in text
    assert re.search(r"command\s*===\s*['\"]serve['\"]", text)
    assert not re.search(
        r"plugins\s*:\s*\[\s*vue\(\),\s*vueDevTools\(\),?\s*\]",
        text,
        re.DOTALL,
    )


def test_vite_build_has_explicit_vendor_code_splitting():
    text = _vite_config_text()
    assert "rolldownOptions" in text
    assert "codeSplitting" in text

    for chunk_name in ("vue-vendor", "element-plus", "markdown"):
        assert chunk_name in text

    for package_name in ("vue", "@vue", "element-plus", "@element-plus", "icons-vue", "marked"):
        assert package_name in text


def test_element_plus_is_imported_on_demand():
    main_text = (FRONTEND_SRC / "main.ts").read_text(encoding="utf-8")
    vue_component_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FRONTEND_SRC.rglob("*.vue")
    )

    assert "app.use(ElementPlus)" not in main_text
    assert "import ElementPlus from 'element-plus'" not in main_text
    assert "element-plus/dist/index.css" not in main_text

    for style_name in (
        "base",
        "el-aside",
        "el-button",
        "el-container",
        "el-icon",
        "el-row",
        "el-col",
        "el-input",
        "el-main",
    ):
        assert f"element-plus/theme-chalk/{style_name}.css" in main_text

    for component_name in (
        "ElAside",
        "ElButton",
        "ElCol",
        "ElContainer",
        "ElIcon",
        "ElInput",
        "ElMain",
        "ElRow",
    ):
        assert component_name in vue_component_text
