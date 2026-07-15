import re
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CLOUD_AGENT_DIR = PROJECT_ROOT / "cloud_agent"
OPS_DIR = PROJECT_ROOT / "ops"

CONTAINER_FILES = [
    PROJECT_ROOT / ".dockerignore",
    CLOUD_AGENT_DIR / "Dockerfile",
    CLOUD_AGENT_DIR / "requirements-container.txt",
    OPS_DIR / "cloud_agent.env.example",
    OPS_DIR / "docker-compose.cloud-agent.yml",
]

SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"DEEPSEEK_API_KEY\s*[:=]\s*['\"]?sk-", re.IGNORECASE),
    re.compile(r"4AMDiDiWei"),
    re.compile(r"YOUR_MYSQL_PASSWORD"),
    re.compile(r"YOUR_NEO4J_PASSWORD"),
]
SECRET_ASSIGNMENT_KEYS = {
    "DEEPSEEK_API_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "NEO4J_PASSWORD",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_no_plaintext_secret_assignments(text: str) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for key in SECRET_ASSIGNMENT_KEYS:
            prefix_match = re.match(rf"^{key}[ \t]*[:=][ \t]*(.*)$", line)
            if not prefix_match:
                continue
            value = prefix_match.group(1).strip().strip("'\"")
            assert value == "" or value.startswith("${"), f"{key} has a plaintext value"


def test_container_files_exist_and_do_not_embed_secret_values():
    for path in CONTAINER_FILES:
        assert path.exists(), path
        text = _read(path)
        for pattern in SECRET_VALUE_PATTERNS:
            assert pattern.search(text) is None, f"{path} contains {pattern.pattern}"
        _assert_no_plaintext_secret_assignments(text)


def test_dockerfile_runs_fastapi_with_readyz_healthcheck_and_runtime_requirements():
    dockerfile = _read(CLOUD_AGENT_DIR / "Dockerfile")
    requirements = (
        _read(CLOUD_AGENT_DIR / "requirements-container.txt")
        + "\n"
        + _read(CLOUD_AGENT_DIR / "agent" / "requirements.txt")
    )

    assert "FROM python:3.12-slim" in dockerfile
    assert "WORKDIR /app/cloud_agent/app" in dockerfile
    assert "adduser --system --ingroup cloudagent" in dockerfile
    assert "USER cloudagent" in dockerfile
    assert "EXPOSE 5000" in dockerfile
    assert "/readyz" in dockerfile
    assert "uvicorn" in dockerfile
    assert "app_main:app" in dockerfile
    assert "CLOUD_AGENT_LLM_PRICING_CONFIG=/app/ops/prometheus/llm_pricing.example.yml" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in requirements
    assert 'torch==2.13.0+cpu ; platform_system == "Linux"' in requirements

    expected_runtime_requirements = {
        "fastapi",
        "uvicorn[standard]",
        "langchain-openai",
        "langchain-huggingface",
        "langchain-milvus",
        "langchain-neo4j",
        "sentence-transformers",
        "pymysql",
        "requests",
        "PyYAML",
        "pymilvus[milvus_lite]>=2.6.0,<3.0.0",
        "milvus-lite",
    }
    for requirement in expected_runtime_requirements:
        assert requirement in requirements


def test_compose_wires_runtime_dependencies_without_plaintext_secrets():
    compose = yaml.safe_load(_read(OPS_DIR / "docker-compose.cloud-agent.yml"))
    services = compose["services"]

    assert {"cloud_agent", "redis", "mysql", "neo4j"}.issubset(services)

    app = services["cloud_agent"]
    assert app["build"] == {"context": "..", "dockerfile": "cloud_agent/Dockerfile"}
    assert app["ports"] == ["5000:5000"]
    assert app["healthcheck"]["test"][-1].count("/readyz") == 1

    env = app["environment"]
    assert env["REDIS_URL"] == "redis://redis:6379"
    assert env["MYSQL_HOST"] == "mysql"
    assert env["MYSQL_PORT"] == "3306"
    assert env["NEO4J_URI"] == "bolt://neo4j:7687"
    assert env["CLOUD_AGENT_LLM_PRICING_CONFIG"] == "/app/ops/prometheus/llm_pricing.example.yml"
    assert env["CLOUD_AGENT_SEMANTIC_CACHE_ENABLED"] == "${CLOUD_AGENT_SEMANTIC_CACHE_ENABLED:-false}"
    assert env["CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED"] == "${CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED:-false}"
    assert env["CLOUD_AGENT_VECTOR_SEARCH_ENABLED"] == "${CLOUD_AGENT_VECTOR_SEARCH_ENABLED:-false}"
    assert env["CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED"] == "${CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED:-false}"
    assert env["CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED"] == "${CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED:-false}"
    assert env["CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED"] == "${CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED:-false}"
    assert env["CLOUD_AGENT_MCP_PRELOAD"] == "${CLOUD_AGENT_MCP_PRELOAD:-false}"
    assert env["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"
    assert env["DEEPSEEK_API_KEY_FILE"] == "${DEEPSEEK_API_KEY_FILE:-}"
    assert env["MYSQL_PASSWORD"] == "${MYSQL_PASSWORD:-}"
    assert env["MYSQL_PASSWORD_FILE"] == "${MYSQL_PASSWORD_FILE:-}"
    assert env["NEO4J_PASSWORD"] == "${NEO4J_PASSWORD:-}"
    assert env["NEO4J_PASSWORD_FILE"] == "${NEO4J_PASSWORD_FILE:-}"
    assert env["CLOUD_AGENT_AUTH_MODE"] == "${CLOUD_AGENT_AUTH_MODE:-local}"
    assert env["CLOUD_AGENT_AUTH_STRATEGY"] == "${CLOUD_AGENT_AUTH_STRATEGY:-gateway}"

    volumes = set(app["volumes"])
    assert "cloud_agent_milvus_cache:/app/cloud_agent/agent/milvus_lite_cache.db" in volumes
    assert "cloud_agent_milvus_cloud:/app/cloud_agent/agent/milvus_lite_cloud.db" in volumes
    assert "cloud_agent_milvus_memory:/app/cloud_agent/agent/milvus_lite_memory.db" in volumes

    mysql = services["mysql"]
    assert mysql["environment"]["MYSQL_ROOT_PASSWORD"] == "${MYSQL_ROOT_PASSWORD:?set MYSQL_ROOT_PASSWORD}"
    assert "../cloud_agent/agent/database/init_mock_data.sql:/docker-entrypoint-initdb.d/001-init-mock-data.sql:ro" in mysql["volumes"]

    assert services["neo4j"]["profiles"] == ["graph"]


def test_dockerignore_excludes_local_runtime_data_and_frontend_dependencies():
    dockerignore = _read(PROJECT_ROOT / ".dockerignore")

    expected_ignores = {
        ".env",
        "*.env",
        "cloud_agent/agent/.env",
        "cloud_agent/front/**/node_modules/",
        "deep_research/front/**/node_modules/",
        "cloud_agent/agent/milvus_lite_cache.db/",
        "cloud_agent/agent/milvus_lite_cloud.db/",
        "cloud_agent/agent/milvus_lite_memory.db/",
    }
    for pattern in expected_ignores:
        assert pattern in dockerignore


def test_env_example_and_readme_document_compose_doctor_preflight():
    env_example = _read(OPS_DIR / "cloud_agent.env.example")
    readme = _read(PROJECT_ROOT / "README.md")

    assert "CLOUD_AGENT_AUTH_MODE=local" in env_example
    assert "docker compose --env-file ops/cloud_agent.env -f ops/docker-compose.cloud-agent.yml up -d" in readme
    assert "python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000" in readme
