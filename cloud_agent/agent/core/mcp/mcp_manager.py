
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.workflow.degradation_audit import build_degradation_event, emit_degradation
from core.workflow.event_log import build_event, emit_event

logger = logging.getLogger(__name__)

AGENT_TOOL_ALLOWLISTS: dict[str, set[str]] = {
    "billing": {"query_user_orders", "query_user_instances"},
    "finops": {"query_user_instances", "analyze_instance_usage"},
    "promotion": {
        "get_promotable_products",
        "search_product_catalog",
        "get_promotion_materials",
        "generate_ai_poster",
    },
    "recommendation": {
        "get_promotable_products",
        "search_product_catalog",
        "get_promotion_materials",
    },
}

_GLOBAL_MCP_TOOL_REGISTRY: "MCPToolRegistry | None" = None


def default_mcp_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "mcp_servers.json"


def _normalize_agent_name(agent_name: str) -> str:
    return agent_name.removesuffix("_agent").removesuffix("_agent_node").lower()


class MCPManager:
    """MCP 服务器连接和工具发现的管理器。
    
    该类处理：
    - 加载 MCP 服务器配置
    - 建立与多个 MCP 服务器的连接
    - 从所有服务器发现和聚合工具
    - 资源清理
    
    示例：
        manager = MCPManager("config/mcp_servers.json")
        await manager.connect()
        tools = await manager.get_tools()
        # 与 agent 一起使用工具
        await manager.close()
    """
    
    def __init__(
        self,
        config_path: str | Path,
        *,
        tool_interceptors: Sequence[Any] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        """使用配置初始化 MCP 管理器。
        
        参数：
            config_path: MCP 服务器配置 JSON 文件的路径。
        """
        self.config_path = Path(config_path)
        self._registry = MCPToolRegistry(
            self.config_path,
            tool_interceptors=tool_interceptors,
            client_factory=client_factory,
        )
        self._client: Any | None = None
        self._tools: list[BaseTool] | None = None
        self._servers_config: dict[str, Any] | None = None
    
    def _load_config(self) -> dict[str, Any]:
        """从 JSON 文件加载 MCP 服务器配置。

        返回：
            包含 mcpServers 配置的字典。

        引发：
            FileNotFoundError: 如果配置文件不存在。
            json.JSONDecodeError: 如果配置文件是无效的 JSON。
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"MCP config not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self._servers_config = config.get("mcpServers", {})
        logger.info("Loaded %d MCP server configs", len(self._servers_config))
        return self._servers_config

    async def connect(self) -> None:
        """连接到所有配置的 MCP 服务器。

        加载配置并建立连接。
        自动发现工具。
        """
        if self._tools is not None:
            logger.warning("MCPManager already connected")
            return

        self._servers_config = self._registry.get_server_connections()
        if not self._servers_config:
            logger.warning("No MCP servers configured")
            self._tools = []
            return

        self._tools = await self._registry.get_all_tools()
        self._client = self._registry.client
        logger.info("Discovered %d tools from MCP servers", len(self._tools))

        for tool in self._tools:
            logger.debug("  - %s: %s", tool.name, tool.description)

    async def close(self) -> None:
        """关闭所有 MCP 连接并清理资源。

        注意：MultiServerMCPClient v0.1.0+ 管理其自身的生命周期；
        不需要显式调用 close。
        """
        await self._registry.close()
        self._client = None
        self._tools = None
        logger.info("MCP connections cleaned up")

    async def get_tools(self) -> list[BaseTool]:
        """返回已连接的 MCP 服务器中的所有工具。

        返回：
            LangChain BaseTool 对象的列表。

        引发：
            RuntimeError: 如果尚未调用 ``connect()``。
        """
        if self._tools is None:
            raise RuntimeError(
                "MCPManager is not connected. Call connect() before get_tools()."
            )
        return self._tools

    def get_tool_names(self) -> list[str]:
        """返回所有可用工具的名称。

        返回：
            工具名称字符串的列表。

        引发：
            RuntimeError: 如果尚未调用 ``connect()``。
        """
        if self._tools is None:
            raise RuntimeError("MCPManager is not connected. Call connect() first.")
        return [tool.name for tool in self._tools]

    def get_server_names(self) -> list[str]:
        """返回已配置的 MCP 服务器名称。

        返回：
            来自配置的服务器名称字符串列表。
        """
        if self._servers_config is None:
            self._servers_config = self._registry.get_server_connections()
        return list(self._servers_config.keys()) if self._servers_config else []


class MCPToolRegistry:
    """Lazy MCP client and tool registry shared by Agent nodes.

    The registry centralizes MCP client creation, tool discovery, and
    per-agent allowlists. Tool call behavior still belongs to the MCP
    interceptors passed in by the Agent layer, especially UserIdInjector.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        tool_interceptors: Sequence[Any] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path else default_mcp_config_path()
        self._tool_interceptors = list(tool_interceptors or [])
        self._client_factory = client_factory or MultiServerMCPClient
        self._client: Any | None = None
        self._tools_by_name: dict[str, BaseTool] | None = None
        self._server_connections: dict[str, Any] | None = None
        self._init_lock: asyncio.Lock | None = None

    def configure_tool_interceptors(self, tool_interceptors: Sequence[Any]) -> None:
        """Configure interceptors before lazy initialization happens."""
        if self._client is not None:
            logger.warning("MCPToolRegistry already initialized; interceptors unchanged")
            return
        self._tool_interceptors = list(tool_interceptors)

    def _load_server_connections(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"MCP config not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self._server_connections = self._resolve_relative_cwd(
            config.get("mcpServers", {})
        )
        logger.info("Loaded %d MCP server configs", len(self._server_connections))
        return self._server_connections

    def _relative_cwd_base(self) -> Path:
        config_dir = self.config_path.parent
        if config_dir.name == "config" and config_dir.parent.name == "agent":
            return config_dir.parent.parent
        return config_dir

    def _resolve_relative_cwd(self, servers_config: dict[str, Any]) -> dict[str, Any]:
        resolved_config: dict[str, Any] = {}
        cwd_base = self._relative_cwd_base()
        for server_name, server_config in servers_config.items():
            if not isinstance(server_config, dict):
                resolved_config[server_name] = server_config
                continue

            resolved_server = dict(server_config)
            cwd = resolved_server.get("cwd")
            if isinstance(cwd, str) and cwd and not Path(cwd).is_absolute():
                resolved_server["cwd"] = str((cwd_base / cwd).resolve())
            resolved_config[server_name] = resolved_server
        return resolved_config

    @property
    def client(self) -> Any | None:
        return self._client

    def get_server_connections(self) -> dict[str, Any]:
        if self._server_connections is None:
            return self._load_server_connections()
        return self._server_connections

    def _emit_initialization_degradation(
        self,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
        error_type: str,
    ) -> None:
        emit_degradation(
            build_degradation_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="mcp",
                operation="tool_registry_initialize",
                error_type=error_type,
            )
        )

    def _emit_initialization_event(
        self,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
        status: str,
        server_count: int | None = None,
        tool_count: int | None = None,
        error_type: str | None = None,
    ) -> None:
        emit_event(
            build_event(
                event_type="mcp_registry_initialize",
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="mcp",
                operation="tool_registry_initialize",
                status=status,
                server_count=server_count,
                tool_count=tool_count,
                error_type=error_type,
            )
        )

    async def _ensure_initialized(
        self,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
    ) -> None:
        if self._tools_by_name is not None:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._tools_by_name is not None:
                return

            try:
                connections = self.get_server_connections()
                if not connections:
                    logger.warning("No MCP servers configured")
                    self._tools_by_name = {}
                    self._emit_initialization_event(
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        status="unavailable",
                        server_count=0,
                        tool_count=0,
                    )
                    return

                self._client = self._client_factory(
                    connections=connections,
                    tool_interceptors=list(self._tool_interceptors),
                )
                tools = await self._client.get_tools()
                self._tools_by_name = {tool.name: tool for tool in tools}
                self._emit_initialization_event(
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    status="success",
                    server_count=len(connections),
                    tool_count=len(tools),
                )
                logger.info("Discovered %d tools from MCP servers", len(tools))
            except Exception as exc:
                self._client = None
                self._tools_by_name = None
                self._emit_initialization_event(
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    status="degraded",
                    error_type=exc.__class__.__name__,
                )
                self._emit_initialization_degradation(
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    error_type=exc.__class__.__name__,
                )
                raise

    async def get_tools_for_agent(
        self,
        agent_name: str,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
    ) -> list[BaseTool]:
        """Return cached MCP tools allowed for the given Agent."""
        normalized_name = _normalize_agent_name(agent_name)
        allowlist = AGENT_TOOL_ALLOWLISTS.get(normalized_name)
        if allowlist is None:
            raise KeyError(f"Unknown MCP agent allowlist: {agent_name}")

        await self._ensure_initialized(request_id=request_id, user_id_hash=user_id_hash)
        tools_by_name = self._tools_by_name or {}
        return [tool for name, tool in tools_by_name.items() if name in allowlist]

    async def get_all_tools(
        self,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
    ) -> list[BaseTool]:
        """Return all cached MCP tools discovered from configured servers."""
        await self._ensure_initialized(request_id=request_id, user_id_hash=user_id_hash)
        return list((self._tools_by_name or {}).values())

    async def get_tool_names_for_agent(
        self,
        agent_name: str,
        *,
        request_id: str = "unknown",
        user_id_hash: str = "unknown",
    ) -> list[str]:
        tools = await self.get_tools_for_agent(
            agent_name,
            request_id=request_id,
            user_id_hash=user_id_hash,
        )
        return [tool.name for tool in tools]

    async def close(self) -> None:
        """Reset cached client and tools.

        MultiServerMCPClient in the current adapter version does not expose a
        stable close lifecycle for this usage, so this method only releases our
        references and allows future lazy reinitialization.
        """
        self._client = None
        self._tools_by_name = None
        self._init_lock = None
        logger.info("MCP tool registry cache cleared")


def get_global_mcp_tool_registry(
    config_path: str | Path | None = None,
    *,
    tool_interceptors: Sequence[Any] | None = None,
) -> MCPToolRegistry:
    global _GLOBAL_MCP_TOOL_REGISTRY

    resolved_config_path = Path(config_path) if config_path else default_mcp_config_path()
    if (
        _GLOBAL_MCP_TOOL_REGISTRY is None
        or _GLOBAL_MCP_TOOL_REGISTRY.config_path != resolved_config_path
    ):
        _GLOBAL_MCP_TOOL_REGISTRY = MCPToolRegistry(
            resolved_config_path,
            tool_interceptors=tool_interceptors,
        )
    elif tool_interceptors:
        _GLOBAL_MCP_TOOL_REGISTRY.configure_tool_interceptors(tool_interceptors)

    return _GLOBAL_MCP_TOOL_REGISTRY


async def close_global_mcp_tool_registry() -> None:
    """Close and reset the process-local MCP tool registry."""
    global _GLOBAL_MCP_TOOL_REGISTRY
    if _GLOBAL_MCP_TOOL_REGISTRY is not None:
        await _GLOBAL_MCP_TOOL_REGISTRY.close()
    _GLOBAL_MCP_TOOL_REGISTRY = None


def reset_global_mcp_tool_registry() -> None:
    """Reset the process-local registry singleton for tests or app shutdown."""
    global _GLOBAL_MCP_TOOL_REGISTRY
    _GLOBAL_MCP_TOOL_REGISTRY = None
