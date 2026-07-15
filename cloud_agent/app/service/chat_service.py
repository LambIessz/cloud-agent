import asyncio
import json
import sys
import os

# 初始化 Agent 和 Graph
AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from core.workflow.graph_manager import AgentGraphManager
from core.workflow.identity_context import (
    apply_identity_metadata,
    resolve_identity,
    scoped_session_id,
)
from core.workflow.degradation_audit import build_degradation_event, emit_degradation
from core.workflow.event_log import build_event, elapsed_ms, emit_event, now_ms
from core.workflow.metrics import estimate_llm_cost_usd, normalize_model_label
from core.workflow.request_context import ensure_request_metadata, get_request_id
from core.workflow.tracing import start_stream_chat_span
from core.mcp.mcp_manager import (
    close_global_mcp_tool_registry,
    get_global_mcp_tool_registry,
)
from core.memory.memory_manager import MemoryManager
from infra.cache import semantic_cache

# Global variables for graph and memory
graph = None
memory = None
_session_turn_counts: dict[str, int] = {}
_background_extract_tasks: set[asyncio.Task] = set()
_semantic_cache_write_tasks: set[asyncio.Task] = set()


class _UnavailableMemoryStore:
    available = False


class _SmokeMemory:
    short_term = _UnavailableMemoryStore()
    long_term = _UnavailableMemoryStore()


class _SmokeChunk:
    def __init__(self, content: str):
        self.content = content


class _SmokeGraph:
    async def ainvoke(self, state: dict, config: dict | None = None):
        from langchain_core.messages import AIMessage

        query = _last_user_query(state)
        return {"messages": [AIMessage(content=f"real backend smoke reply: {query}")]}

    async def astream_events(self, state: dict, config: dict | None = None, version: str | None = None):
        from langchain_core.messages import AIMessage

        query = _last_user_query(state)
        response = f"real backend smoke reply: {query}"
        yield {"event": "on_chain_start", "name": "orchestrator", "data": {}}
        await asyncio.sleep(0)
        yield {"event": "on_chain_start", "name": "fallback_agent", "data": {}}
        for chunk in ("real backend smoke reply: ", query):
            yield {
                "event": "on_chat_model_stream",
                "name": "fallback_agent",
                "data": {"chunk": _SmokeChunk(chunk)},
            }
            await asyncio.sleep(0)
        yield {
            "event": "on_chain_end",
            "name": "graph",
            "data": {"output": {"messages": [AIMessage(content=response)]}},
        }


def _last_user_query(state: dict) -> str:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return ""
    last_message = messages[-1]
    if isinstance(last_message, tuple) and len(last_message) > 1:
        return str(last_message[1])
    return str(getattr(last_message, "content", last_message))


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


def _print_degradation_summary() -> None:
    checks = []
    if memory and not memory.short_term.available:
        checks.append(("Redis (short-term memory)", "degraded"))
    if memory and not memory.long_term.available:
        checks.append(("Milvus (long-term memory)", "degraded"))
    if semantic_cache and not semantic_cache.available:
        checks.append(("Semantic Cache", "degraded"))
    try:
        registry = get_global_mcp_tool_registry()
        if registry._client is None:
            checks.append(("MCP Registry", "not-initialized"))
    except Exception:
        pass

    if not checks:
        print("📊 Degradation 摘要: 全部依赖可用 ✅")
        return

    status = " ".join(f"{name}:unavailable" for name, _ in checks)
    print(f"📊 Degradation 摘要: {len(checks)} 个组件不可用 — {status}")
    print("   降级不影响 /healthz /readyz fallback LLM 路由和 metrics 指标采集")


def _mapping_or_empty(value):
    return value if isinstance(value, dict) else {}


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _message_content(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content or "")


def _last_message_from_output(output):
    if isinstance(output, dict):
        messages = output.get("messages")
        if messages:
            return messages[-1]
    messages = getattr(output, "messages", None)
    if messages:
        return messages[-1]
    return None


def _graph_event_name(event: dict) -> str:
    return str(event.get("event") or event.get("event_type") or "")


def _graph_event_step_name(event: dict) -> str | None:
    event_name = _graph_event_name(event)
    if event_name not in {"on_chain_start", "on_tool_start"}:
        return None
    metadata = _mapping_or_empty(event.get("metadata"))
    name = event.get("name") or metadata.get("langgraph_node")
    return str(name) if name else None


def _graph_event_delta(event: dict) -> str:
    event_name = _graph_event_name(event)
    if event_name not in {"on_chat_model_stream", "on_llm_stream"}:
        return ""
    data = _mapping_or_empty(event.get("data"))
    chunk = data.get("chunk")
    if isinstance(chunk, str):
        return chunk
    return _message_content(chunk)


def _graph_event_final_message(event: dict):
    event_name = _graph_event_name(event)
    if event_name not in {"on_chain_end", "on_graph_end"}:
        return None
    data = _mapping_or_empty(event.get("data"))
    return _last_message_from_output(data.get("output"))


async def _iter_graph_events(state: dict, config: dict):
    try:
        stream = graph.astream_events(state, config=config, version="v2")
    except TypeError:
        stream = graph.astream_events(state, config=config)
    async for event in stream:
        if isinstance(event, dict):
            yield event


async def _invoke_graph(state: dict, config: dict):
    if asyncio.iscoroutinefunction(graph.ainvoke):
        return await graph.ainvoke(state, config=config)
    return await asyncio.to_thread(asyncio.run, graph.ainvoke(state, config=config))


def _first_non_negative_int(*values) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0 and value.is_integer():
            return int(value)
    return None


def _extract_response_usage(response_message) -> dict[str, int | str | None]:
    usage_metadata = _mapping_or_empty(getattr(response_message, "usage_metadata", None))
    response_metadata = _mapping_or_empty(getattr(response_message, "response_metadata", None))
    token_usage = _mapping_or_empty(
        response_metadata.get("token_usage") or response_metadata.get("usage")
    )

    prompt_tokens = _first_non_negative_int(
        usage_metadata.get("input_tokens"),
        usage_metadata.get("prompt_tokens"),
        token_usage.get("prompt_tokens"),
        token_usage.get("input_tokens"),
    )
    completion_tokens = _first_non_negative_int(
        usage_metadata.get("output_tokens"),
        usage_metadata.get("completion_tokens"),
        token_usage.get("completion_tokens"),
        token_usage.get("output_tokens"),
    )
    model = (
        response_metadata.get("model_name")
        or response_metadata.get("model")
        or os.getenv("MODEL")
    )
    return {
        "model": normalize_model_label(model),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def _semantic_cache_metadata(response_message) -> dict[str, int | float | str | None]:
    usage = _extract_response_usage(response_message)
    prompt_tokens = usage["prompt_tokens"]
    completion_tokens = usage["completion_tokens"]
    model = str(usage["model"] or "unknown")
    estimated_cost_usd = None
    if prompt_tokens is not None or completion_tokens is not None:
        estimated_cost_usd = estimate_llm_cost_usd(
            model=model,
            prompt_tokens=float(prompt_tokens or 0),
            completion_tokens=float(completion_tokens or 0),
        )
    return {
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_completion_tokens": completion_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "model": model if model != "unknown" else None,
    }


def _turn_count_key(user_id_hash: str, session_id: str) -> str:
    return f"{user_id_hash}:{session_id}"


def _emit_request_end_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    request_start_ms: float,
    status: str,
    error_type: str | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="request_end",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="chat_service",
            operation="stream_chat",
            status=status,
            latency_ms=elapsed_ms(request_start_ms),
            error_type=error_type,
        )
    )


def _emit_cache_lookup_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    status: str,
    cache_level: str | None = None,
    cache_distance: float | None = None,
    error_type: str | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="cache_lookup",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="semantic_cache",
            operation="get_cache",
            status=status,
            cache_level=cache_level,
            cache_distance=cache_distance,
            error_type=error_type,
        )
    )


def _emit_cache_benefit_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    estimated_saved_prompt_tokens: int | None = None,
    estimated_saved_completion_tokens: int | None = None,
    estimated_saved_cost_usd: float | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="cache_benefit",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="semantic_cache",
            operation="stream_chat",
            status="estimated",
            estimated_saved_calls=1,
            estimated_saved_prompt_tokens=estimated_saved_prompt_tokens,
            estimated_saved_completion_tokens=estimated_saved_completion_tokens,
            estimated_saved_cost_usd=estimated_saved_cost_usd,
        )
    )


def _emit_memory_save_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    status: str,
    error_type: str | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="memory_save",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="redis",
            operation="short_memory_save",
            status=status,
            error_type=error_type,
        )
    )


def _emit_memory_retrieve_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    component: str,
    operation: str,
    status: str,
    retrieved_count: int | None = None,
    error_type: str | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="memory_retrieve",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component=component,
            operation=operation,
            status=status,
            retrieved_count=retrieved_count,
            error_type=error_type,
        )
    )


def _emit_background_extract_event(
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str,
    status: str,
    extracted_count: int | None = None,
    error_type: str | None = None,
) -> None:
    emit_event(
        build_event(
            event_type="background_extract",
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="memory",
            operation="background_preference_extract",
            status=status,
            extracted_count=extracted_count,
            error_type=error_type,
        )
    )


def _build_preference_extraction_llm():
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    dotenv_path = os.path.join(AGENT_DIR, ".env")
    load_dotenv(dotenv_path)
    return ChatOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("MODEL", "deepseek-chat"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.1,
    )


async def _run_background_extract(
    user_id: str,
    session_id: str,
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str = "unknown",
):
    try:
        if memory is None:
            _emit_background_extract_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                tenant_id=tenant_id,
                status="skipped",
            )
            return
        llm = _build_preference_extraction_llm()
        preferences = await memory.background_extract(
            user_id,
            session_id,
            llm,
            request_id=request_id,
            user_id_hash=user_id_hash,
        )
        _emit_background_extract_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            status="success",
            extracted_count=len(preferences or []),
        )
    except Exception as exc:
        _emit_background_extract_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            status="degraded",
            error_type=exc.__class__.__name__,
        )
        emit_degradation(
            build_degradation_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="memory",
                operation="background_preference_extract",
                error_type=exc.__class__.__name__,
            )
        )


def _schedule_background_extract(
    user_id: str,
    session_id: str,
    *,
    request_id: str,
    user_id_hash: str,
    tenant_id: str = "unknown",
) -> None:
    if not _env_flag("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", True):
        return
    if memory is None or not hasattr(memory, "background_extract"):
        return

    interval = _env_int("CLOUD_AGENT_BACKGROUND_EXTRACT_TURNS", 5)
    key = _turn_count_key(user_id_hash, session_id)
    turn_count = _session_turn_counts.get(key, 0) + 1
    _session_turn_counts[key] = turn_count
    if turn_count % interval != 0:
        return

    task = asyncio.create_task(
        _run_background_extract(
            user_id,
            session_id,
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
        )
    )
    _background_extract_tasks.add(task)
    task.add_done_callback(_background_extract_tasks.discard)


async def _run_semantic_cache_write(
    query: str,
    response_text: str,
    user_id: str,
    cache_metadata: dict[str, int | float | str | None],
    *,
    request_id: str,
    user_id_hash: str,
) -> None:
    try:
        await semantic_cache.set_cache(
            query,
            response_text,
            user_id=user_id,
            estimated_prompt_tokens=cache_metadata.get("estimated_prompt_tokens"),
            estimated_completion_tokens=cache_metadata.get("estimated_completion_tokens"),
            estimated_cost_usd=cache_metadata.get("estimated_cost_usd"),
            model=cache_metadata.get("model"),
            raise_on_error=True,
        )
    except Exception as exc:
        emit_degradation(
            build_degradation_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="semantic_cache",
                operation="set_cache",
                error_type=exc.__class__.__name__,
            )
        )


def _schedule_semantic_cache_write(
    query: str,
    response_text: str,
    user_id: str,
    response_message,
    *,
    request_id: str,
    user_id_hash: str,
) -> None:
    if not _env_flag("CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED", True):
        return
    if not response_text.strip():
        return
    if not getattr(semantic_cache, "available", False):
        return
    if not hasattr(semantic_cache, "set_cache"):
        return

    task = asyncio.create_task(
        _run_semantic_cache_write(
            query,
            response_text,
            user_id,
            _semantic_cache_metadata(response_message),
            request_id=request_id,
            user_id_hash=user_id_hash,
        )
    )
    _semantic_cache_write_tasks.add(task)
    task.add_done_callback(_semantic_cache_write_tasks.discard)


async def init_agent_system():
    global graph, memory
    if graph is None:
        if _env_flag("CLOUD_AGENT_SMOKE_FAKE_GRAPH"):
            print("Initializing browser smoke fake graph...")
            graph = _SmokeGraph()
            memory = _SmokeMemory()
            await semantic_cache.initialize()
            _print_degradation_summary()
            print("Browser smoke fake graph initialized.")
            return
        print("🚀 初始化 Multi-Agent 图编排...")
        graph_manager = AgentGraphManager()
        graph = graph_manager.build_graph()
        
        print("🧠 初始化 Memory 系统...")
        from config import get_settings
        settings = get_settings()
        memory = MemoryManager(
            redis_url=settings.redis_url,
            redis_ttl=settings.redis_ttl,
            milvus_host=settings.milvus_host,
            milvus_port=settings.milvus_port,
            milvus_api_key=settings.milvus_api_key,
        )
        await memory.initialize()
        await semantic_cache.initialize()
        if _env_flag("CLOUD_AGENT_MCP_PRELOAD"):
            print("🔌 预热 MCP 工具注册表...")
            registry = get_global_mcp_tool_registry()
            tool_names = await registry.get_tool_names_for_agent("billing")
            print(f"✅ MCP 工具注册表预热完成，billing tools={','.join(tool_names)}")
        _print_degradation_summary()
        print("✅ Agent 系统初始化完成！")


async def shutdown_agent_system():
    if _semantic_cache_write_tasks:
        await asyncio.gather(*list(_semantic_cache_write_tasks), return_exceptions=True)
        _semantic_cache_write_tasks.clear()
    if _background_extract_tasks:
        await asyncio.gather(*list(_background_extract_tasks), return_exceptions=True)
        _background_extract_tasks.clear()
    await close_global_mcp_tool_registry()
    print("✅ Agent 系统资源清理完成！")

async def _extract_memory_context(
    user_id: str,
    session_id: str,
    query: str,
    *,
    request_id: str = "unknown",
    user_id_hash: str = "unknown",
    tenant_id: str = "unknown",
) -> str:
    context_parts = []
    if memory and memory.short_term.available:
        try:
            history = await memory.short_term.get_messages(user_id, session_id)
            _emit_memory_retrieve_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                tenant_id=tenant_id,
                component="redis",
                operation="short_memory_get",
                status="success",
                retrieved_count=len(history or []),
            )
            if history:
                recent_history = history[-10:] if len(history) > 10 else history
                context_parts.append("【近期对话历史】:")
                for msg in recent_history:
                    role = "User" if msg["role"] == "user" else "Assistant"
                    context_parts.append(f"{role}: {msg['content']}")
        except Exception as exc:
            _emit_memory_retrieve_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                tenant_id=tenant_id,
                component="redis",
                operation="short_memory_get",
                status="degraded",
                error_type=exc.__class__.__name__,
            )
            emit_degradation(
                build_degradation_event(
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    component="redis",
                    operation="short_memory_get",
                    error_type=exc.__class__.__name__,
                )
            )
    else:
        _emit_memory_retrieve_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="redis",
            operation="short_memory_get",
            status="unavailable",
        )
        emit_degradation(
            build_degradation_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="redis",
                operation="short_memory_get",
                status="unavailable",
            )
        )

    if memory and memory.long_term.available:
        try:
            prefs = await memory.long_term.retrieve_relevant(user_id, query)
            _emit_memory_retrieve_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                tenant_id=tenant_id,
                component="milvus",
                operation="long_memory_retrieve",
                status="success",
                retrieved_count=len(prefs or []),
            )
            if prefs:
                context_parts.append("\n【用户长期偏好/背景】:")
                for p in prefs:
                    context_parts.append(f"- {p}")
        except Exception as exc:
            _emit_memory_retrieve_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                tenant_id=tenant_id,
                component="milvus",
                operation="long_memory_retrieve",
                status="degraded",
                error_type=exc.__class__.__name__,
            )
            emit_degradation(
                build_degradation_event(
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    component="milvus",
                    operation="long_memory_retrieve",
                    error_type=exc.__class__.__name__,
                )
            )
    else:
        _emit_memory_retrieve_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            tenant_id=tenant_id,
            component="milvus",
            operation="long_memory_retrieve",
            status="unavailable",
        )
        emit_degradation(
            build_degradation_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="milvus",
                operation="long_memory_retrieve",
                status="unavailable",
            )
        )
                
    return "\n".join(context_parts)

async def stream_chat(
    query: str,
    user_id: str,
    session_id: str,
    request_id: str | None = None,
    request_tenant_id: str | None = None,
    authenticated_user_id: str | None = None,
    authenticated_tenant_id: str | None = None,
):
    identity = resolve_identity(
        request_user_id=user_id,
        request_tenant_id=request_tenant_id,
        authenticated_user_id=authenticated_user_id,
        authenticated_tenant_id=authenticated_tenant_id,
    )
    scoped_session = scoped_session_id(identity, session_id)
    metadata = ensure_request_metadata({"request_id": request_id} if request_id else None)
    metadata = apply_identity_metadata(metadata, identity)
    request_id = get_request_id(metadata)
    request_start_ms = now_ms()
    with start_stream_chat_span(
        identity_source=identity.source,
        request_id=request_id,
    ) as trace_span:
        try:
            emit_event(
                build_event(
                    event_type="request_start",
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                    tenant_id=identity.tenant_id,
                    component="chat_service",
                    operation="stream_chat",
                )
            )
            cache_hit = None
            if getattr(semantic_cache, "available", False):
                try:
                    cache_hit = await semantic_cache.get_cache(query, identity.user_id)
                except Exception as exc:
                    trace_span.set_attribute("cache.status", "degraded")
                    _emit_cache_lookup_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                        status="degraded",
                        error_type=exc.__class__.__name__,
                    )
                    emit_degradation(
                        build_degradation_event(
                            request_id=request_id,
                            user_id_hash=identity.user_id_hash,
                            component="semantic_cache",
                            operation="get_cache",
                            error_type=exc.__class__.__name__,
                        )
                    )
                else:
                    cache_status = "hit" if cache_hit else "miss"
                    trace_span.set_attribute("cache.status", cache_status)
                    _emit_cache_lookup_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                        status=cache_status,
                        cache_level=cache_hit.get("level") if cache_hit else None,
                        cache_distance=cache_hit.get("distance") if cache_hit else None,
                    )
            else:
                trace_span.set_attribute("cache.status", "unavailable")
                _emit_cache_lookup_event(
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                    tenant_id=identity.tenant_id,
                    status="unavailable",
                )
                emit_degradation(
                    build_degradation_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        component="semantic_cache",
                        operation="get_cache",
                        status="unavailable",
                    )
                )
            stream_mode = "fallback"
            streamed_response_text = False
            response_message = None
            if cache_hit:
                _emit_cache_benefit_event(
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                    tenant_id=identity.tenant_id,
                    estimated_saved_prompt_tokens=cache_hit.get("estimated_prompt_tokens"),
                    estimated_saved_completion_tokens=cache_hit.get("estimated_completion_tokens"),
                    estimated_saved_cost_usd=cache_hit.get("estimated_cost_usd"),
                )
                response_text = cache_hit["answer"]
                print(
                    f"[ChatService] request_id={request_id} semantic_cache_hit level={cache_hit['level']} "
                    f"distance={cache_hit['distance']:.4f}"
                )
                stream_mode = "cache"
                yield _sse_data(
                    {
                        "event_type": "stream_start",
                        "stream_mode": stream_mode,
                        "request_id": request_id,
                    }
                )
            else:
                print(
                    f"[ChatService] request_id={request_id} identity_source={identity.source} "
                    f"user_id_hash={identity.user_id_hash} entering_agent_workflow"
                )
                mem_context = await _extract_memory_context(
                    identity.user_id,
                    scoped_session,
                    query,
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                    tenant_id=identity.tenant_id,
                )
                state = {
                    "messages": [("user", query)],
                    "user_id": identity.user_id,
                    "tenant_id": identity.tenant_id,
                    "session_id": scoped_session,
                    "memory_context": mem_context,
                    "next_agent": "",
                    "metadata": metadata
                }
                config = {
                    "configurable": {
                        "user_id": identity.user_id,
                        "tenant_id": identity.tenant_id,
                        "user_id_hash": identity.user_id_hash,
                        "request_id": request_id,
                    }
                }
                stream_mode = "native" if hasattr(graph, "astream_events") else "fallback"
                yield _sse_data(
                    {
                        "event_type": "stream_start",
                        "stream_mode": stream_mode,
                        "request_id": request_id,
                    }
                )
                try:
                    if stream_mode == "native":
                        streamed_parts = []
                        async for event in _iter_graph_events(state, config):
                            step_name = _graph_event_step_name(event)
                            if step_name:
                                yield _sse_data(
                                    {
                                        "event_type": "agent_step",
                                        "stream_mode": stream_mode,
                                        "step": step_name,
                                    }
                                )
                            delta = _graph_event_delta(event)
                            if delta:
                                streamed_parts.append(delta)
                                streamed_response_text = True
                                yield _sse_data(
                                    {
                                        "event_type": "message_delta",
                                        "stream_mode": stream_mode,
                                        "content": delta,
                                    }
                                )
                            final_message = _graph_event_final_message(event)
                            if final_message is not None:
                                response_message = final_message
                        if response_message is None:
                            from langchain_core.messages import AIMessage

                            response_message = AIMessage(content="".join(streamed_parts))
                    else:
                        result = await _invoke_graph(state, config)
                        response_message = result["messages"][-1]
                except Exception as exc:
                    _emit_request_end_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                        request_start_ms=request_start_ms,
                        status="error",
                        error_type=exc.__class__.__name__,
                    )
                    raise
                response_text = _message_content(response_message)
                _schedule_semantic_cache_write(
                    query,
                    response_text,
                    identity.user_id,
                    response_message,
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                )
            
            # 保存短时记忆
            if memory and memory.short_term.available:
                turn = [
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": response_text},
                ]
                try:
                    await memory.save_conversation(identity.user_id, scoped_session, turn)
                    _schedule_background_extract(
                        identity.user_id,
                        scoped_session,
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                    )
                    _emit_memory_save_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                        status="success",
                    )
                except Exception as exc:
                    _emit_memory_save_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        tenant_id=identity.tenant_id,
                        status="degraded",
                        error_type=exc.__class__.__name__,
                    )
                    emit_degradation(
                        build_degradation_event(
                            request_id=request_id,
                            user_id_hash=identity.user_id_hash,
                            component="redis",
                            operation="short_memory_save",
                            error_type=exc.__class__.__name__,
                        )
                    )
            else:
                _emit_memory_save_event(
                    request_id=request_id,
                    user_id_hash=identity.user_id_hash,
                    tenant_id=identity.tenant_id,
                    status="unavailable",
                )
                emit_degradation(
                    build_degradation_event(
                        request_id=request_id,
                        user_id_hash=identity.user_id_hash,
                        component="redis",
                        operation="short_memory_save",
                        status="unavailable",
                    )
                )
                
            # 流式返回大模型结果
            chunk_size = 5
            if not streamed_response_text:
                chunk_size = 5
                for i in range(0, len(response_text), chunk_size):
                    chunk = response_text[i:i+chunk_size]
                    yield _sse_data(
                        {
                            "event_type": "message_delta",
                            "stream_mode": stream_mode,
                            "content": chunk,
                        }
                    )
                    await asyncio.sleep(0.02)

            trace_span.set_success()
            _emit_request_end_event(
                request_id=request_id,
                user_id_hash=identity.user_id_hash,
                tenant_id=identity.tenant_id,
                request_start_ms=request_start_ms,
                status="success",
            )
            yield _sse_data(
                {
                    "event_type": "done",
                    "done": True,
                    "request_id": request_id,
                    "stream_mode": stream_mode,
                }
            )
        except Exception as exc:
            trace_span.set_error(exc.__class__.__name__)
            raise
