from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = PROJECT_ROOT / "cloud_agent" / "front" / "cloud_agent" / "src"
APP_VUE_PATH = FRONTEND_SRC / "App.vue"

TEXT_SOURCE_SUFFIXES = {".vue", ".ts", ".js", ".mjs", ".css"}

EXPECTED_APP_TEXT = {
    "企业云智能客服",
    "欢迎使用云平台智能客服",
    "我是您的专属 AI 助手",
    "产品咨询与推荐",
    "账单与实例查询",
    "资源优化与降本",
    "产品推广活动",
    "正在思考与调用工具中...",
    "请输入您的问题，Shift + Enter 换行，Enter 发送",
    "请求失败",
    "请检查后端 /readyz、Nginx /api/ 反向代理和 Docker 容器日志。",
}

COMMON_MOJIBAKE_PATTERNS = {
    "\ufffd",
    "浼佷笟",
    "浜戞",
    "娆㈣繋",
    "鎴戞槸",
    "璇疯緭",
    "鍙戦",
    "姝ｅ湪",
    "甯垜",
    "鑾峰彇",
    "鏌ヨ",
    "鏂板",
    "瀹炰緥",
    "鎺ㄨ崘",
    "鉂",
}


def _frontend_text_files():
    for path in FRONTEND_SRC.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SOURCE_SUFFIXES:
            yield path


def test_frontend_sources_keep_key_user_facing_chinese_text_readable():
    text = "\n".join(path.read_text(encoding="utf-8") for path in _frontend_text_files())

    for expected in EXPECTED_APP_TEXT:
        assert expected in text


def test_frontend_sources_do_not_contain_common_mojibake_sequences():
    offenders = []

    for path in _frontend_text_files():
        text = path.read_text(encoding="utf-8")
        for pattern in COMMON_MOJIBAKE_PATTERNS:
            if pattern in text:
                relative_path = path.relative_to(PROJECT_ROOT).as_posix()
                offenders.append(f"{relative_path}: {pattern}")

    assert offenders == []
