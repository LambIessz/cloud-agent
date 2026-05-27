from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AGENT_REQUIREMENTS = PROJECT_ROOT / "cloud_agent" / "agent" / "requirements.txt"
CONTAINER_REQUIREMENTS = PROJECT_ROOT / "cloud_agent" / "requirements-container.txt"

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


def test_container_requirements_delegate_to_agent_runtime_requirements():
    lines = _requirement_lines(CONTAINER_REQUIREMENTS)

    assert lines == ["-r agent/requirements.txt"]
