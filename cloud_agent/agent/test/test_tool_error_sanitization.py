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
    assert payload["data"] is None
    assert "user_message" in payload
    assert "error_code" in payload
    assert "message" in payload
    assert payload["message"] == payload["user_message"]
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


def test_mcp_tools_use_the_unified_contract_envelope(monkeypatch):
    success_payload = json.loads(cloud_platform_server.get_promotable_products())
    missing_payload = json.loads(cloud_platform_server.search_product_catalog("no-match-keyword"))

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return self._rows

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Connection:
        open = True

        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return _Cursor(self._rows)

        def close(self):
            self.open = False

    monkeypatch.setattr(
        cloud_platform_server,
        "get_db_connection",
        lambda: _Connection(
            [
                {
                    "order_id": "order-001",
                    "product_name": "ECS",
                    "billing_mode": "postpaid",
                    "amount": 12.5,
                    "status": "paid",
                    "created_at": "2026-07-16 10:00:00",
                }
            ]
        ),
    )
    query_payload = json.loads(cloud_platform_server.query_user_orders("user_1001"))

    for payload in (success_payload, missing_payload, query_payload):
        assert "status" in payload
        assert "data" in payload
        assert "user_message" in payload
        assert "error_code" in payload
        assert "message" in payload
        assert payload["message"] == payload["user_message"]

    assert success_payload["status"] == "success"
    assert success_payload["error_code"] == ""
    assert success_payload["data"]

    assert missing_payload["status"] == "not_found"
    assert missing_payload["error_code"] == "NO_MATCH"
    assert missing_payload["data"]["recommendation"]["product_id"] == "P_ALL_000"

    assert query_payload["status"] == "success"
    assert query_payload["error_code"] == ""
    assert query_payload["data"][0]["amount"] == 12.5
