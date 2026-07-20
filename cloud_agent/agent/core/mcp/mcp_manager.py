
import asyncio
import json
import logging
import re
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
    "support": {"query_user_instances", "analyze_instance_usage"},
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

TOOL_DISCOVERY_CATALOG: dict[str, dict[str, list[str]]] = {
    "query_user_orders": {
        "capabilities": ["billing", "orders", "account_lookup"],
        "use_cases": ["账单", "订单", "消费", "购买记录", "明细", "账单明细", "invoice", "bill"],
        "constraints": ["requires_user_id", "read_only"],
        "keywords": ["订单", "账单", "消费", "购买", "明细", "流水", "发票"],
    },
    "query_user_instances": {
        "capabilities": ["billing", "inventory", "resource_lookup"],
        "use_cases": ["实例", "资源", "机器", "云服务器", "名下", "购买了哪些"],
        "constraints": ["requires_user_id", "read_only"],
        "keywords": ["实例", "资源", "机器", "云服务器", "ECS", "RDS"],
    },
    "analyze_instance_usage": {
        "capabilities": ["finops", "monitoring", "diagnostics"],
        "use_cases": ["降本", "成本", "优化", "闲置", "监控", "分析", "CPU", "内存"],
        "constraints": ["requires_instance_id", "requires_user_id", "read_only"],
        "keywords": ["降本", "成本", "闲置", "优化", "CPU", "内存", "监控", "诊断"],
    },
    "get_promotable_products": {
        "capabilities": ["promotion", "catalog", "recommendation"],
        "use_cases": ["推广", "可推广", "商品列表", "返佣", "活动"],
        "constraints": ["read_only"],
        "keywords": ["推广", "返佣", "商品", "活动", "可推", "货架"],
    },
    "search_product_catalog": {
        "capabilities": ["recommendation", "catalog", "search"],
        "use_cases": ["选型", "推荐", "规格", "配置", "对比", "搜索"],
        "constraints": ["read_only"],
        "keywords": ["选型", "推荐", "规格", "配置", "ECS", "RDS", "GPU", "搜索"],
    },
    "get_promotion_materials": {
        "capabilities": ["promotion", "materials", "link_generation"],
        "use_cases": ["素材", "链接", "海报", "落地页", "推广物料"],
        "constraints": ["requires_product_id", "read_only"],
        "keywords": ["素材", "链接", "海报", "推广", "物料"],
    },
    "generate_ai_poster": {
        "capabilities": ["promotion", "image_generation", "creative"],
        "use_cases": ["海报", "图片", "视觉", "生成配图", "营销物料"],
        "constraints": ["requires_prompt", "external_model"],
        "keywords": ["海报", "图片", "视觉", "配图", "生成"],
    },
}

AGENT_TOOL_DISCOVERY_PROFILES: dict[str, dict[str, Any]] = {
    "billing": {
        "keywords": ["??", "??", "??", "??", "??"],
        "preferred_tools": ["query_user_orders", "query_user_instances"],
        "max_tools": 2,
    },
    "finops": {
        "keywords": ["降本", "成本", "闲置", "优化", "CPU", "内存", "监控", "资源"],
        "preferred_tools": ["query_user_instances", "analyze_instance_usage"],
        "max_tools": 2,
    },
    "support": {
        "keywords": ["ssh", "端口", "连接", "故障", "异常", "cpu", "内存", "实例", "日志"],
        "preferred_tools": ["query_user_instances", "analyze_instance_usage"],
        "max_tools": 2,
    },
    "promotion": {
        "keywords": ["??", "??", "??", "??", "??", "??", "??", "??"],
        "preferred_tools": [
            "generate_ai_poster",
            "get_promotion_materials",
            "get_promotable_products",
            "search_product_catalog",
        ],
        "max_tools": 3,
    },
    "recommendation": {
        "keywords": ["推荐", "选型", "配置", "规格", "预算", "并发", "合适", "比较"],
        "preferred_tools": [
            "search_product_catalog",
            "get_promotable_products",
            "get_promotion_materials",
        ],
        "max_tools": 3,
    },
}

_TOOL_DISCOVERY_DEFAULT_MAX_TOOLS = 3

_GLOBAL_MCP_TOOL_REGISTRY: "MCPToolRegistry | None" = None


def default_mcp_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "mcp_servers.json"


def _normalize_agent_name(agent_name: str) -> str:
    return agent_name.removesuffix("_agent").removesuffix("_agent_node").lower()


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword and keyword.lower() in text for keyword in keywords)


def _count_hits(text: str, keywords: Sequence[str]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword.lower() in text)


def _get_agent_discovery_profile(agent_name: str) -> dict[str, Any]:
    normalized_name = _normalize_agent_name(agent_name)
    return dict(AGENT_TOOL_DISCOVERY_PROFILES.get(normalized_name, {}))


def _score_tool_for_query(
    *,
    agent_name: str,
    tool_name: str,
    tool: BaseTool,
    query: str,
    allowlist_index: int,
) -> tuple[int, list[str]]:
    profile = _get_agent_discovery_profile(agent_name)
    metadata = TOOL_DISCOVERY_CATALOG.get(tool_name, {})
    query_text = _normalize_text(query)
    description_text = _normalize_text(getattr(tool, "description", ""))
    haystack = " ".join(
        part for part in (query_text, tool_name.lower(), description_text) if part
    )

    score = 0
    reasons: list[str] = []
    direct_match = False
    for label, weight in (("capabilities", 3), ("use_cases", 4), ("keywords", 2)):
        keywords = metadata.get(label, [])
        hits = _count_hits(haystack, keywords)
        if hits:
            score += hits * weight
            reasons.append(f"{label}:{hits}")
            direct_match = True

    profile_keywords = profile.get("keywords", [])
    hits = _count_hits(haystack, profile_keywords)
    if hits:
        score += hits * 2
        reasons.append(f"agent:{hits}")
        direct_match = True

    if direct_match:
        preferred_tools = profile.get("preferred_tools", [])
        if tool_name in preferred_tools:
            bonus = max(1, len(preferred_tools) - preferred_tools.index(tool_name))
            score += bonus * 2
            reasons.append("preferred")

        constraints = metadata.get("constraints", [])
        if "requires_user_id" in constraints and _contains_any(
            query_text,
            ("??", "??", "??", "??", "user", "????"),
        ):
            score += 1
            reasons.append("user_bound")
        if "requires_instance_id" in constraints and _contains_any(
            query_text,
            ("i-", "??", "??", "??", "ecs"),
        ):
            score += 1
            reasons.append("instance_bound")
        if "requires_product_id" in constraints and _contains_any(
            query_text,
            ("??", "??", "??", "??", "??", "??"),
        ):
            score += 1
            reasons.append("product_bound")
        if "requires_prompt" in constraints and _contains_any(
            query_text,
            ("??", "??", "??", "??", "??"),
        ):
            score += 1
            reasons.append("prompt_bound")

    if direct_match and description_text and _contains_any(
        description_text,
        ("billing", "promotion", "recommendation", "diagnostic", "monitor"),
    ):
        score += 1

    score -= allowlist_index * 0.01
    return score, reasons


def _select_ranked_tools(
    *,
    agent_name: str,
    tools_by_name: dict[str, BaseTool],
    allowlist: set[str],
    query: str | None,
    max_tools: int | None = None,
) -> tuple[list[BaseTool], list[dict[str, Any]]]:
    ordered_tools = [
        (index, name, tool)
        for index, (name, tool) in enumerate(tools_by_name.items())
        if name in allowlist
    ]
    if not ordered_tools:
        return [], []

    query_text = _normalize_text(query)
    if not query_text:
        selected = [tool for _index, _name, tool in ordered_tools]
        ranked = [
            {
                "tool_name": name,
                "score": 0,
                "reasons": [],
                "original_index": index,
            }
            for index, name, _tool in ordered_tools
        ]
        return selected, ranked

    profile = _get_agent_discovery_profile(agent_name)
    limit = max_tools if max_tools is not None else int(profile.get("max_tools") or _TOOL_DISCOVERY_DEFAULT_MAX_TOOLS)
    limit = max(1, min(limit, len(ordered_tools)))

    scored = []
    for index, name, tool in ordered_tools:
        score, reasons = _score_tool_for_query(
            agent_name=agent_name,
            tool_name=name,
            tool=tool,
            query=query_text,
            allowlist_index=index,
        )
        scored.append(
            {
                "tool_name": name,
                "tool": tool,
                "score": score,
                "reasons": reasons,
                "original_index": index,
            }
        )

    ranked = sorted(scored, key=lambda item: (-item["score"], item["original_index"], item["tool_name"]))
    selected_ranked = [item for item in ranked if item["score"] > 0][:limit]
    if not selected_ranked:
        selected_names = {item["tool_name"] for item in selected_ranked}
        for item in ranked:
            if item["tool_name"] in selected_names:
                continue
            selected_ranked.append(item)
            if len(selected_ranked) >= limit:
                break

    selected_tools = [item["tool"] for item in selected_ranked]
    return selected_tools, ranked


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

    def _emit_tool_selection_event(
        self,
        *,
        request_id: str,
        user_id_hash: str,
        agent_name: str,
        selected_tools: list[BaseTool],
        ranked_tools: list[dict[str, Any]],
        max_tools: int,
    ) -> None:
        if not ranked_tools:
            return
        emit_event(
            build_event(
                event_type="mcp_tool_selection",
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="mcp",
                operation="tool_selection",
                status="success",
                agent_name=agent_name,
                candidate_count=len(ranked_tools),
                selected_count=len(selected_tools),
                max_tools=max_tools,
                selected_tools=[tool.name for tool in selected_tools],
                top_tool=selected_tools[0].name if selected_tools else None,
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
        query: str | None = None,
        max_tools: int | None = None,
    ) -> list[BaseTool]:
        """Return cached MCP tools allowed for the given Agent.

        When a query is supplied, the allowlisted tools are ranked by a small
        deterministic discovery profile so the agent receives a tighter
        candidate set first.
        """
        normalized_name = _normalize_agent_name(agent_name)
        allowlist = AGENT_TOOL_ALLOWLISTS.get(normalized_name)
        if allowlist is None:
            raise KeyError(f"Unknown MCP agent allowlist: {agent_name}")

        await self._ensure_initialized(request_id=request_id, user_id_hash=user_id_hash)
        tools_by_name = self._tools_by_name or {}
        if query is None or not str(query).strip():
            return [tool for name, tool in tools_by_name.items() if name in allowlist]

        selected_tools, ranked_tools = _select_ranked_tools(
            agent_name=normalized_name,
            tools_by_name=tools_by_name,
            allowlist=allowlist,
            query=query,
            max_tools=max_tools,
        )
        profile = _get_agent_discovery_profile(normalized_name)
        limit = max_tools if max_tools is not None else int(profile.get("max_tools") or _TOOL_DISCOVERY_DEFAULT_MAX_TOOLS)
        limit = max(1, min(limit, len([name for name in tools_by_name if name in allowlist])))
        self._emit_tool_selection_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            agent_name=normalized_name,
            selected_tools=selected_tools,
            ranked_tools=ranked_tools,
            max_tools=limit,
        )
        return selected_tools

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
        query: str | None = None,
        max_tools: int | None = None,
    ) -> list[str]:
        tools = await self.get_tools_for_agent(
            agent_name,
            request_id=request_id,
            user_id_hash=user_id_hash,
            query=query,
            max_tools=max_tools,
        )
        return [tool.name for tool in tools]

    def get_tool_metadata(self, tool_name: str) -> dict[str, Any]:
        return dict(TOOL_DISCOVERY_CATALOG.get(tool_name, {}))

    def get_agent_discovery_profile(self, agent_name: str) -> dict[str, Any]:
        return _get_agent_discovery_profile(agent_name)

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
