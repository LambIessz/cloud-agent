from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AGENT_REQUIREMENTS = PROJECT_ROOT / "cloud_agent" / "agent" / "requirements.txt"
CONTAINER_REQUIREMENTS = PROJECT_ROOT / "cloud_agent" / "requirements-container.txt"
DEEP_RESEARCH_REQUIREMENTS = PROJECT_ROOT / "deep_research" / "requirements.txt"

EXPECTED_AGENT_REQUIREMENTS = {
    "langchain",
    "langchain-core",
    "langchain-community",
    "langgraph",
    "langgraph-prebuilt",
    "langchain-openai",
    "langchain-huggingface",
    "langchain-milvus",
    "langchain-neo4j",
    "sentence-transformers",
    "fastapi",
    "uvicorn[standard]",
    "PyJWT[crypto]",
    "mcp",
    "langchain-mcp-adapters",
    "dashscope",
    "pydantic",
    "pydantic-settings",
    "python-dotenv",
    "httpx",
    "requests",
    "PyYAML",
    "asyncio-mqtt",
    "aiofiles",
    "neo4j",
    "redis",
    "pymysql",
    "pymilvus[milvus_lite]",
    "milvus-lite",
    "structlog",
}

CRITICAL_CONSTRAINTS = {
    "pymilvus[milvus_lite]": "pymilvus[milvus_lite]>=2.6.0,<3.0.0",
    "milvus-lite": "milvus-lite>=3.0.0,<4.0.0",
    "langchain-milvus": "langchain-milvus>=0.3.0,<0.4.0",
    "langchain-openai": "langchain-openai>=1.0.0,<2.0.0",
    "langchain-huggingface": "langchain-huggingface>=1.0.0,<2.0.0",
    "langchain-mcp-adapters": "langchain-mcp-adapters>=0.2.0,<0.3.0",
}

DEEP_RESEARCH_FOUNDATION_SECURITY_PINS = {
    "aiohttp==3.14.1",
    "click==8.4.2",
    "Flask==3.1.3",
    "idna==3.15",
    "marshmallow==3.26.2",
    "pip==26.1.2",
    "protobuf==6.33.5",
    "pydantic-settings==2.14.2",
    "Pygments==2.20.0",
    "python-dotenv==1.2.2",
    "python-multipart==0.0.31",
    "requests==2.33.0",
    "urllib3==2.7.0",
    "Werkzeug==3.1.6",
    "wheel==0.46.2",
}

DEEP_RESEARCH_AGENT_COMPATIBILITY_PINS = {
    "langchain==1.3.9",
    "langchain-classic==1.0.7",
    "langchain-core==1.4.9",
    "langchain-mcp-adapters==0.3.0",
    "langchain-text-splitters==1.1.2",
    "langgraph==1.2.9",
    "langgraph-checkpoint==4.1.1",
    "langgraph-checkpoint-postgres==3.1.0",
    "langgraph-checkpoint-redis==0.5.0",
    "langgraph-prebuilt==1.1.0",
    "langgraph-sdk==0.4.2",
    "langsmith==0.8.18",
    "mcp==1.23.0",
    "langchain-openai==1.3.5",
    "openai==2.45.0",
}

DEEP_RESEARCH_MAJOR_SECURITY_PINS = {
    "cryptography==49.0.0",
    "fastapi==0.139.0",
    "starlette==1.3.1",
}


def _requirement_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _requirement_name(line: str) -> str:
    for marker in (">=", "==", "<=", "~=", ">", "<"):
        if marker in line:
            return line.split(marker, 1)[0]
    return line


def test_agent_requirements_are_complete_and_have_upper_bounds():
    lines = _requirement_lines(AGENT_REQUIREMENTS)
    names = {_requirement_name(line) for line in lines}

    assert EXPECTED_AGENT_REQUIREMENTS.issubset(names)

    unconstrained = [
        line
        for line in lines
        if not line.startswith("-r ") and ("<" not in line or ">=" not in line)
    ]
    assert unconstrained == []


def test_known_fragile_dependencies_are_pinned_to_safe_major_ranges():
    text = AGENT_REQUIREMENTS.read_text(encoding="utf-8")

    for requirement in CRITICAL_CONSTRAINTS.values():
        assert requirement in text
    assert "pymilvus>=2.4.0" not in text
    assert "pymilvus>=3" not in text
    assert "pymilvus==3" not in text


def test_container_requirements_delegate_to_agent_runtime_with_cpu_only_torch():
    lines = _requirement_lines(CONTAINER_REQUIREMENTS)

    assert lines == [
        "--extra-index-url https://download.pytorch.org/whl/cpu",
        'torch==2.13.0+cpu ; platform_system == "Linux"',
        "-r agent/requirements.txt",
    ]


def test_deep_research_foundation_dependencies_keep_security_fix_pins():
    lines = set(_requirement_lines(DEEP_RESEARCH_REQUIREMENTS))

    assert DEEP_RESEARCH_FOUNDATION_SECURITY_PINS.issubset(lines)
    assert DEEP_RESEARCH_AGENT_COMPATIBILITY_PINS.issubset(lines)
    assert DEEP_RESEARCH_MAJOR_SECURITY_PINS.issubset(lines)

    source_paths = (
        PROJECT_ROOT / "deep_research" / "app" / "mult_agents" / "main.py",
        PROJECT_ROOT / "deep_research" / "app" / "mult_agents" / "rag" / "core.py",
        PROJECT_ROOT / "deep_research" / "app" / "mult_agents" / "memory" / "manager.py",
    )
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    adapter = (
        PROJECT_ROOT / "deep_research" / "app" / "mult_agents" / "dashscope_compatible.py"
    ).read_text(encoding="utf-8")

    assert "langchain_community" not in source_text
    assert "ChatTongyi" not in source_text
    assert "DashScopeEmbeddings" not in source_text
    assert "from langchain_openai import ChatOpenAI, OpenAIEmbeddings" in adapter
