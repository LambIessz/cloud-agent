import ast
import asyncio
import json
from pathlib import Path

import pytest

from core.mcp.mcp_manager import MCPManager, MCPToolRegistry

AGENT_DIR = Path(__file__).resolve().parents[1]


class _Tool:
    def __init__(self, name: str):
        self.name = name
        self.description = f"{name} description"


class _FakeClient:
    init_count = 0
    get_tools_count = 0
    init_kwargs = []

    def __init__(self, **kwargs):
        type(self).init_count += 1
        type(self).init_kwargs.append(kwargs)

    async def get_tools(self):
        type(self).get_tools_count += 1
        return [
            _Tool("query_user_orders"),
            _Tool("query_user_instances"),
            _Tool("analyze_instance_usage"),
            _Tool("get_promotable_products"),
            _Tool("search_product_catalog"),
            _Tool("get_promotion_materials"),
            _Tool("generate_ai_poster"),
            _Tool("unlisted_internal_tool"),
        ]


class _SlowFakeClient(_FakeClient):
    async def get_tools(self):
        await asyncio.sleep(0.01)
        return await super().get_tools()


class _FailingClient(_FakeClient):
    async def get_tools(self):
        raise RuntimeError("mcp backend secret leaked")


def _write_config(tmp_path):
    config_path = tmp_path / "config" / "mcp_servers.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"mcpServers": {"cloud_billing": {"transport": "stdio"}}}),
        encoding="utf-8",
    )
    return config_path


def _write_empty_config(tmp_path):
    config_path = tmp_path / "config" / "empty_mcp_servers.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    return config_path


def _write_config_with_cwd(tmp_path, cwd):
    config_path = tmp_path / "config" / "mcp_servers.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"mcpServers": {"cloud_billing": {"transport": "stdio", "cwd": cwd}}}),
        encoding="utf-8",
    )
    return config_path


def _names(tools):
    return [tool.name for tool in tools]


def _event_log_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))
    return events


def _degradation_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[Degradation] "):
            events.append(json.loads(line.removeprefix("[Degradation] ")))
    return events


def test_cloud_platform_server_has_unique_mcp_tool_function_names():
    server_path = AGENT_DIR / "mcp_servers" / "cloud_platform_server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    tool_names = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        has_mcp_tool_decorator = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
        if has_mcp_tool_decorator:
            tool_names.append(node.name)

    duplicates = {name for name in tool_names if tool_names.count(name) > 1}
    assert duplicates == set()


def test_registry_discovers_tools_once_and_applies_allowlists(tmp_path):
    _FakeClient.init_count = 0
    _FakeClient.get_tools_count = 0
    _FakeClient.init_kwargs = []
    interceptor = object()
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        tool_interceptors=[interceptor],
        client_factory=_FakeClient,
    )

    billing_tools = asyncio.run(registry.get_tools_for_agent("billing"))
    finops_tools = asyncio.run(registry.get_tools_for_agent("finops"))
    promotion_tools = asyncio.run(registry.get_tools_for_agent("promotion"))
    recommendation_tools = asyncio.run(registry.get_tools_for_agent("recommendation"))

    assert _FakeClient.init_count == 1
    assert _FakeClient.get_tools_count == 1
    assert _FakeClient.init_kwargs[0]["tool_interceptors"] == [interceptor]
    assert _names(billing_tools) == ["query_user_orders", "query_user_instances"]
    assert _names(finops_tools) == ["query_user_instances", "analyze_instance_usage"]
    assert _names(promotion_tools) == [
        "get_promotable_products",
        "search_product_catalog",
        "get_promotion_materials",
        "generate_ai_poster",
    ]
    assert _names(recommendation_tools) == [
        "get_promotable_products",
        "search_product_catalog",
        "get_promotion_materials",
    ]


def test_registry_resolves_relative_server_cwd_from_cloud_agent_root():
    registry = MCPToolRegistry("cloud_agent/agent/config/mcp_servers.json")

    connections = registry.get_server_connections()

    assert connections["cloud_billing"]["cwd"].endswith("cloud_agent\\agent") or (
        connections["cloud_billing"]["cwd"].endswith("cloud_agent/agent")
    )


def test_registry_resolves_generic_relative_server_cwd_from_config_directory(tmp_path):
    registry = MCPToolRegistry(_write_config_with_cwd(tmp_path, "server"))

    connections = registry.get_server_connections()

    assert connections["cloud_billing"]["cwd"] == str(
        (tmp_path / "config" / "server").resolve()
    )


def test_registry_emits_initialization_success_event(tmp_path, capsys):
    _FakeClient.init_count = 0
    _FakeClient.get_tools_count = 0
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        client_factory=_FakeClient,
    )

    asyncio.run(
        registry.get_tools_for_agent(
            "billing",
            request_id="req_mcp_success",
            user_id_hash="hash_mcp_success",
        )
    )
    asyncio.run(
        registry.get_tools_for_agent(
            "billing",
            request_id="req_mcp_cached",
            user_id_hash="hash_mcp_cached",
        )
    )

    events = _event_log_events(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["event_type"] == "mcp_registry_initialize"
    assert events[0]["request_id"] == "req_mcp_success"
    assert events[0]["user_id_hash"] == "hash_mcp_success"
    assert events[0]["component"] == "mcp"
    assert events[0]["operation"] == "tool_registry_initialize"
    assert events[0]["status"] == "success"
    assert events[0]["server_count"] == 1
    assert events[0]["tool_count"] == 8


def test_registry_emits_initialization_unavailable_for_empty_config(tmp_path, capsys):
    registry = MCPToolRegistry(
        _write_empty_config(tmp_path),
        client_factory=_FakeClient,
    )

    tools = asyncio.run(
        registry.get_tools_for_agent(
            "billing",
            request_id="req_mcp_empty",
            user_id_hash="hash_mcp_empty",
        )
    )

    assert tools == []
    events = _event_log_events(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["event_type"] == "mcp_registry_initialize"
    assert events[0]["request_id"] == "req_mcp_empty"
    assert events[0]["user_id_hash"] == "hash_mcp_empty"
    assert events[0]["component"] == "mcp"
    assert events[0]["operation"] == "tool_registry_initialize"
    assert events[0]["status"] == "unavailable"
    assert events[0]["server_count"] == 0
    assert events[0]["tool_count"] == 0


def test_registry_rejects_unknown_agent_allowlist(tmp_path):
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        client_factory=_FakeClient,
    )

    with pytest.raises(KeyError):
        asyncio.run(registry.get_tools_for_agent("support"))


def test_registry_initializes_once_for_concurrent_first_requests(tmp_path):
    _SlowFakeClient.init_count = 0
    _SlowFakeClient.get_tools_count = 0
    _SlowFakeClient.init_kwargs = []
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        client_factory=_SlowFakeClient,
    )

    async def get_billing_tools():
        results = await asyncio.gather(
            registry.get_tools_for_agent("billing"),
            registry.get_tools_for_agent("billing"),
            registry.get_tools_for_agent("billing"),
        )
        return results

    results = asyncio.run(get_billing_tools())

    assert _SlowFakeClient.init_count == 1
    assert _SlowFakeClient.get_tools_count == 1
    assert all(_names(tools) == ["query_user_orders", "query_user_instances"] for tools in results)


def test_registry_close_allows_lazy_reinitialization(tmp_path):
    _FakeClient.init_count = 0
    _FakeClient.get_tools_count = 0
    _FakeClient.init_kwargs = []
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        client_factory=_FakeClient,
    )

    asyncio.run(registry.get_tools_for_agent("billing"))
    asyncio.run(registry.close())
    asyncio.run(registry.get_tools_for_agent("billing"))

    assert _FakeClient.init_count == 2
    assert _FakeClient.get_tools_count == 2


def test_registry_emits_degradation_on_tool_discovery_failure(tmp_path, capsys):
    registry = MCPToolRegistry(
        _write_config(tmp_path),
        client_factory=_FailingClient,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            registry.get_tools_for_agent(
                "billing",
                request_id="req_mcp_fail",
                user_id_hash="hash_mcp_fail",
            )
        )

    output = capsys.readouterr().out
    assert "mcp backend secret leaked" not in output
    event_log_events = [
        event
        for event in _event_log_events(output)
        if event["event_type"] == "mcp_registry_initialize"
    ]
    assert len(event_log_events) == 1
    assert event_log_events[0]["request_id"] == "req_mcp_fail"
    assert event_log_events[0]["user_id_hash"] == "hash_mcp_fail"
    assert event_log_events[0]["component"] == "mcp"
    assert event_log_events[0]["operation"] == "tool_registry_initialize"
    assert event_log_events[0]["status"] == "degraded"
    assert event_log_events[0]["error_type"] == "RuntimeError"
    events = _degradation_events(output)
    assert len(events) == 1
    assert events[0]["event_type"] == "degradation"
    assert events[0]["request_id"] == "req_mcp_fail"
    assert events[0]["user_id_hash"] == "hash_mcp_fail"
    assert events[0]["component"] == "mcp"
    assert events[0]["operation"] == "tool_registry_initialize"
    assert events[0]["status"] == "degraded"
    assert events[0]["error_type"] == "RuntimeError"


def test_registry_emits_degradation_on_missing_config(tmp_path, capsys):
    registry = MCPToolRegistry(
        tmp_path / "missing_mcp_servers.json",
        client_factory=_FakeClient,
    )

    with pytest.raises(FileNotFoundError):
        asyncio.run(registry.get_tools_for_agent("billing", request_id="req_missing"))

    output = capsys.readouterr().out
    assert "missing_mcp_servers" not in output
    event_log_events = [
        event
        for event in _event_log_events(output)
        if event["event_type"] == "mcp_registry_initialize"
    ]
    assert len(event_log_events) == 1
    assert event_log_events[0]["request_id"] == "req_missing"
    assert event_log_events[0]["user_id_hash"] == "unknown"
    assert event_log_events[0]["component"] == "mcp"
    assert event_log_events[0]["operation"] == "tool_registry_initialize"
    assert event_log_events[0]["status"] == "degraded"
    assert event_log_events[0]["error_type"] == "FileNotFoundError"
    events = _degradation_events(output)
    assert len(events) == 1
    assert events[0]["request_id"] == "req_missing"
    assert events[0]["user_id_hash"] == "unknown"
    assert events[0]["component"] == "mcp"
    assert events[0]["operation"] == "tool_registry_initialize"
    assert events[0]["error_type"] == "FileNotFoundError"


def test_legacy_mcp_manager_uses_registry_cache(tmp_path):
    _FakeClient.init_count = 0
    _FakeClient.get_tools_count = 0
    _FakeClient.init_kwargs = []
    manager = MCPManager(
        _write_config(tmp_path),
        client_factory=_FakeClient,
    )

    assert manager.get_server_names() == ["cloud_billing"]
    assert _FakeClient.init_count == 0

    with pytest.raises(RuntimeError):
        asyncio.run(manager.get_tools())

    asyncio.run(manager.connect())
    tools = asyncio.run(manager.get_tools())

    assert _FakeClient.init_count == 1
    assert _FakeClient.get_tools_count == 1
    assert "query_user_orders" in _names(tools)
    assert "unlisted_internal_tool" in manager.get_tool_names()

    asyncio.run(manager.close())
    with pytest.raises(RuntimeError):
        asyncio.run(manager.get_tools())
