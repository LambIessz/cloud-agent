import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]
MCP_DIR = AGENT_DIR / "mcp_servers"
for path in (AGENT_DIR, MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools import graph_tool, vector_tool
import cloud_platform_server


SECRET_ERROR_TEXT = "mysql://root:super-secret-password@db.internal leaked"


def _assert_error_payload_is_sanitized(raw: str):
    payload = json.loads(raw)
    text = json.dumps(payload, ensure_ascii=False)

    assert payload["status"] == "error"
    assert "RuntimeError" in text
    assert "super-secret-password" not in text
    assert "db.internal" not in text
    assert "leaked" not in text


def test_vector_tool_error_uses_error_type_without_backend_message(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_VECTOR_SEARCH_ENABLED", "true")

    def _raise_backend_error():
        raise RuntimeError(SECRET_ERROR_TEXT)

    monkeypatch.setattr(vector_tool, "_get_milvus_store", _raise_backend_error)

    result = vector_tool.query_vector_db.invoke({"query": "ecs refund"})

    assert "RuntimeError" in result
    assert "super-secret-password" not in result
    assert "db.internal" not in result
    assert "leaked" not in result


def test_graph_tool_error_uses_error_type_without_backend_message(monkeypatch):
    monkeypatch.setenv("CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED", "true")

    def _raise_backend_error():
        raise RuntimeError(SECRET_ERROR_TEXT)

    monkeypatch.setattr(graph_tool, "_get_graph_chain", _raise_backend_error)
    monkeypatch.setattr(graph_tool, "_fallback_graph_keyword_search", lambda _query: "")

    result = graph_tool.query_knowledge_graph.invoke({"query": "ecs graph"})

    assert "RuntimeError" in result
    assert "super-secret-password" not in result
    assert "db.internal" not in result
    assert "leaked" not in result


def test_mcp_database_tools_error_use_error_type_without_backend_message(monkeypatch):
    def _raise_backend_error():
        raise RuntimeError(SECRET_ERROR_TEXT)

    monkeypatch.setattr(cloud_platform_server, "get_db_connection", _raise_backend_error)

    _assert_error_payload_is_sanitized(cloud_platform_server.query_user_orders("user_1001"))
    _assert_error_payload_is_sanitized(cloud_platform_server.query_user_instances("user_1001"))
    _assert_error_payload_is_sanitized(
        cloud_platform_server.analyze_instance_usage("i-bp1abcdefg", user_id="user_1001")
    )
