from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Mapping, Sequence, TypedDict


DEFAULT_SYSTEM_CONSTRAINTS = [
    "只使用当前会话和已返回的工具证据。",
    "缺少实例 ID、地域、订单号或明确目标时先追问，不要猜测。",
    "高风险动作必须经过人工确认。",
    "不要编造不存在的实例、产品、金额或状态。",
]

DEFAULT_CONTEXT_PROFILES: dict[str, dict[str, Any]] = {
    "orchestrator": {
        "budget_tokens": 900,
        "recent_messages": 8,
        "preference_items": 2,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "product_agent": {
        "budget_tokens": 1200,
        "recent_messages": 12,
        "preference_items": 3,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "billing_agent": {
        "budget_tokens": 1200,
        "recent_messages": 12,
        "preference_items": 3,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "promotion_agent": {
        "budget_tokens": 900,
        "recent_messages": 8,
        "preference_items": 2,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "recommendation_agent": {
        "budget_tokens": 1300,
        "recent_messages": 12,
        "preference_items": 4,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "support_agent": {
        "budget_tokens": 1000,
        "recent_messages": 10,
        "preference_items": 2,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "checkpoint_agent": {
        "budget_tokens": 700,
        "recent_messages": 4,
        "preference_items": 1,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": False,
        "include_tool_notes": True,
    },
    "planner_agent": {
        "budget_tokens": 1000,
        "recent_messages": 8,
        "preference_items": 2,
        "include_control_notes": True,
        "include_summary": True,
        "include_recent_history": True,
        "include_preferences": True,
        "include_tool_notes": True,
    },
    "fallback_agent": {
        "budget_tokens": 500,
        "recent_messages": 4,
        "preference_items": 1,
        "include_control_notes": True,
        "include_summary": False,
        "include_recent_history": True,
        "include_preferences": False,
        "include_tool_notes": False,
    },
}

_TOPIC_HINTS = [
    "ecs",
    "rds",
    "vpc",
    "eip",
    "安全组",
    "公网",
    "内网",
    "实例",
    "服务器",
    "账单",
    "订单",
    "推广",
    "推荐",
    "成本",
    "降本",
    "cpu",
    "内存",
    "网络",
    "端口",
    "ssh",
    "重启",
    "删除",
    "释放",
    "迁移",
    "回滚",
    "监控",
    "日志",
]


class ContextSection(TypedDict, total=False):
    name: str
    title: str
    content: str
    priority: int
    token_estimate: int
    kept: bool
    truncated: bool


class ContextBundle(TypedDict, total=False):
    version: int
    default_agent: str
    query: str
    summary: dict[str, Any]
    sections: list[ContextSection]
    profiles: dict[str, dict[str, Any]]
    agent_contexts: dict[str, str]
    task_plan: dict[str, Any]


def get_context_profile(agent_name: str) -> dict[str, Any]:
    profile = DEFAULT_CONTEXT_PROFILES.get(agent_name)
    if profile is None:
        profile = DEFAULT_CONTEXT_PROFILES["orchestrator"]
    return dict(profile)


def select_agent_memory_context(state: Mapping[str, Any], agent_name: str, default: str = "") -> str:
    bundle = state.get("context_bundle")
    if isinstance(bundle, Mapping):
        agent_contexts = bundle.get("agent_contexts")
        if isinstance(agent_contexts, Mapping):
            context = agent_contexts.get(agent_name)
            if isinstance(context, str) and context.strip():
                return context
        default_agent = bundle.get("default_agent")
        if isinstance(default_agent, str):
            fallback_contexts = bundle.get("agent_contexts")
            if isinstance(fallback_contexts, Mapping):
                context = fallback_contexts.get(default_agent)
                if isinstance(context, str) and context.strip():
                    return context
    context = state.get("memory_context")
    if isinstance(context, str) and context.strip():
        return context
    return default


def estimate_context_tokens(text: str) -> int:
    normalized = _normalize_text(text)
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / 2))


def build_context_bundle(
    *,
    query: str,
    history: Sequence[Mapping[str, Any]] | None,
    preferences: Sequence[str] | None,
    metadata: Mapping[str, Any] | None = None,
    agent_names: Sequence[str] | None = None,
    system_constraints: Sequence[str] | None = None,
) -> ContextBundle:
    history_items = [item for item in (history or []) if isinstance(item, Mapping)]
    preference_items = [str(item).strip() for item in (preferences or []) if str(item).strip()]
    metadata_map = dict(metadata or {})
    agents = list(agent_names or DEFAULT_CONTEXT_PROFILES.keys())
    constraints = list(system_constraints or DEFAULT_SYSTEM_CONSTRAINTS)

    profiles = {agent: get_context_profile(agent) for agent in agents}
    default_agent = agents[0] if agents else "orchestrator"

    base_sections = _build_sections_for_profile(
        query=query,
        history=history_items,
        preferences=preference_items,
        metadata=metadata_map,
        profile=profiles.get(default_agent, get_context_profile(default_agent)),
        system_constraints=constraints,
    )

    agent_contexts: dict[str, str] = {}
    agent_stats: dict[str, dict[str, Any]] = {}
    for agent_name, profile in profiles.items():
        sections = _build_sections_for_profile(
            query=query,
            history=history_items,
            preferences=preference_items,
            metadata=metadata_map,
            profile=profile,
            system_constraints=constraints,
        )
        rendered, used_tokens, trimmed_sections = _render_sections(
            sections,
            profile["budget_tokens"],
        )
        agent_contexts[agent_name] = rendered
        agent_stats[agent_name] = {
            "budget_tokens": profile["budget_tokens"],
            "used_tokens": used_tokens,
            "trimmed_sections": trimmed_sections,
        }

    summary = {
        "history_messages": len(history_items),
        "preferences": len(preference_items),
        "agent_profiles": agent_stats,
        "tool_notes": bool(_build_tool_notes(metadata_map)),
        "omitted_messages": _count_omitted_messages(history_items, profiles.get(default_agent, {}).get("recent_messages", 0)),
    }

    return {
        "version": 1,
        "default_agent": default_agent,
        "query": query,
        "summary": summary,
        "sections": base_sections,
        "profiles": profiles,
        "agent_contexts": agent_contexts,
    }


def _build_sections_for_profile(
    *,
    query: str,
    history: Sequence[Mapping[str, Any]],
    preferences: Sequence[str],
    metadata: Mapping[str, Any],
    profile: Mapping[str, Any],
    system_constraints: Sequence[str],
) -> list[ContextSection]:
    sections: list[ContextSection] = []

    if profile.get("include_control_notes", True):
        control_notes = _build_control_notes(metadata)
        control_notes = list(dict.fromkeys([line for line in control_notes if line.strip()]))
        if control_notes:
            sections.append(
                _make_section(
                    "system_constraints",
                    "系统约束与控制信息",
                    "\n".join(f"- {line}" for line in control_notes),
                    priority=100,
                )
            )

    if profile.get("include_summary", True):
        summary = _build_history_summary(history, int(profile.get("recent_messages", 0)))
        if summary:
            sections.append(
                _make_section(
                    "history_summary",
                    "历史摘要",
                    summary,
                    priority=90,
                )
            )

    if profile.get("include_recent_history", True):
        recent_history = _build_recent_history(history, int(profile.get("recent_messages", 0)))
        if recent_history:
            sections.append(
                _make_section(
                    "recent_history",
                    "近期原文",
                    recent_history,
                    priority=80,
                )
            )

    if profile.get("include_preferences", True):
        preference_text = _build_preferences_section(
            preferences,
            int(profile.get("preference_items", 0)),
        )
        if preference_text:
            sections.append(
                _make_section(
                    "long_term_memory",
                    "长期背景",
                    preference_text,
                    priority=70,
                )
            )

    if profile.get("include_tool_notes", True):
        tool_notes = _build_tool_notes(metadata)
        if tool_notes:
            sections.append(
                _make_section(
                    "tool_notes",
                    "工具/诊断结果",
                    "\n".join(f"- {line}" for line in tool_notes),
                    priority=60,
                )
            )

    return sections


def _build_control_notes(metadata: Mapping[str, Any]) -> list[str]:
    notes = list(DEFAULT_SYSTEM_CONSTRAINTS)
    planner_mode = str(metadata.get("planner_mode") or "").strip()
    planner_status = str(metadata.get("planner_status") or "").strip()
    planner_target = str(metadata.get("planner_target_agent") or "").strip()
    planner_followup = str(metadata.get("planner_followup_agent") or "").strip()
    planner_reason = str(metadata.get("planner_reason") or "").strip()
    if planner_mode or planner_status or planner_target or planner_followup or planner_reason:
        planner_parts: list[str] = []
        if planner_mode:
            planner_parts.append(f"模式：{planner_mode}")
        if planner_status:
            planner_parts.append(f"状态：{planner_status}")
        if planner_target:
            planner_parts.append(f"首个执行：{planner_target}")
        if planner_followup:
            planner_parts.append(f"后续：{planner_followup}")
        if planner_reason:
            planner_parts.append(f"原因：{planner_reason}")
        notes.append("任务规划：" + "；".join(planner_parts))
    checkpoint = metadata.get("human_checkpoint")
    if isinstance(checkpoint, Mapping):
        action = str(checkpoint.get("action_summary") or checkpoint.get("source_query") or "").strip()
        status = str(checkpoint.get("status") or "").lower()
        if status == "pending":
            notes.append(f"当前会话存在待确认的高风险动作：{action or '未知动作'}")
        elif status == "confirmed":
            notes.append(f"上一轮高风险动作已确认：{action or '未知动作'}")
        elif status == "rejected":
            notes.append(f"上一轮高风险动作已拒绝：{action or '未知动作'}")

    if metadata.get("is_finops_workflow"):
        notes.append("当前属于成本优化工作流，优先基于实例事实和账单数据回答。")

    route_reason = str(metadata.get("route_reason") or "").strip()
    if route_reason:
        notes.append(f"当前路由原因：{route_reason}")

    return notes


def _build_tool_notes(metadata: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    support = metadata.get("support_diagnostics")
    if isinstance(support, Mapping):
        summary = str(support.get("summary") or "").strip()
        evidence_count = support.get("evidence_count")
        if summary:
            notes.append(f"只读诊断摘要：{summary}")
        if isinstance(evidence_count, int):
            notes.append(f"只读诊断证据数：{evidence_count}")
    checkpoint = metadata.get("human_checkpoint")
    if isinstance(checkpoint, Mapping):
        action = str(checkpoint.get("action_summary") or checkpoint.get("source_query") or "").strip()
        status = str(checkpoint.get("status") or "").lower()
        if action:
            notes.append(f"高风险动作：{action}")
        if status:
            notes.append(f"高风险动作状态：{status}")
    return notes


def _build_history_summary(history: Sequence[Mapping[str, Any]], recent_messages: int) -> str:
    omitted = _omitted_history(history, recent_messages)
    if not omitted:
        return "暂无可摘要的较早历史。"

    omitted_text = " ".join(_message_content(item) for item in omitted)
    topics = _extract_topics(omitted_text)
    if topics:
        return f"已省略 {len(omitted)} 条较早消息，主要涉及：{'、'.join(topics)}"
    return f"已省略 {len(omitted)} 条较早消息，保留最近 {max(1, recent_messages)} 条原文。"


def _build_recent_history(history: Sequence[Mapping[str, Any]], recent_messages: int) -> str:
    recent = list(history[-max(0, recent_messages):]) if recent_messages > 0 else []
    if not recent:
        return ""
    lines = []
    for message in recent:
        role = _message_role(message)
        content = _normalize_text(_message_content(message))
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _build_preferences_section(preferences: Sequence[str], preference_items: int) -> str:
    selected = [item for item in preferences if item][: max(0, preference_items)] if preference_items > 0 else list(preferences)
    if not selected:
        return ""
    return "\n".join(f"- {item}" for item in selected)


def _render_sections(
    sections: Sequence[ContextSection],
    budget_tokens: int,
) -> tuple[str, int, list[str]]:
    ordered = sorted(sections, key=lambda item: (-int(item.get("priority", 0)), item.get("name", "")))
    rendered: list[str] = []
    used_tokens = estimate_context_tokens("【上下文预算】")
    trimmed_sections: list[str] = []

    for section in ordered:
        title = str(section.get("title") or section.get("name") or "上下文")
        content = _compact_multiline_text(str(section.get("content") or ""))
        if not content:
            continue

        section_tokens = estimate_context_tokens(title) + estimate_context_tokens(content)
        remaining = budget_tokens - used_tokens
        if remaining <= 0:
            trimmed_sections.append(str(section.get("name") or title))
            break

        if section_tokens <= remaining:
            rendered.extend([f"【{title}】", content, ""])
            used_tokens += section_tokens
            continue

        truncated_content = _truncate_to_budget(content, max(0, remaining - estimate_context_tokens(title)))
        if truncated_content:
            rendered.extend([f"【{title}】", truncated_content, ""])
            used_tokens += estimate_context_tokens(title) + estimate_context_tokens(truncated_content)
        trimmed_sections.append(str(section.get("name") or title))
        break

    header = f"【上下文预算】约 {budget_tokens} tokens，已用 {used_tokens}"
    body = "\n".join([header, *rendered]).strip()
    return body, used_tokens, trimmed_sections


def _make_section(name: str, title: str, content: str, *, priority: int) -> ContextSection:
    normalized = _compact_multiline_text(content)
    return {
        "name": name,
        "title": title,
        "content": normalized,
        "priority": priority,
        "token_estimate": estimate_context_tokens(normalized),
        "kept": True,
        "truncated": False,
    }


def _truncate_to_budget(text: str, remaining_tokens: int) -> str:
    if remaining_tokens <= 0:
        return ""
    max_chars = max(12, remaining_tokens * 2)
    if len(text) <= max_chars:
        return text
    suffix = " ...（已截断）"
    keep = max(0, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def _message_content(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content or "")


def _message_role(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "message").lower()
    if role == "user":
        return "User"
    if role == "assistant":
        return "Assistant"
    if role == "tool":
        return "Tool"
    if role == "system":
        return "System"
    return role.title()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _compact_multiline_text(text: str) -> str:
    lines = []
    for raw_line in str(text or "").splitlines():
        line = _normalize_text(raw_line)
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _omitted_history(history: Sequence[Mapping[str, Any]], recent_messages: int) -> list[Mapping[str, Any]]:
    recent_messages = max(0, recent_messages)
    if recent_messages <= 0 or len(history) <= recent_messages:
        return []
    return list(history[:-recent_messages])


def _count_omitted_messages(history: Sequence[Mapping[str, Any]], recent_messages: int) -> int:
    return len(_omitted_history(history, recent_messages))


def _extract_topics(text: str) -> list[str]:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return []

    hits: list[str] = []
    for hint in _TOPIC_HINTS:
        if hint in normalized:
            hits.append(hint.upper() if hint.isascii() else hint)

    if hits:
        return list(dict.fromkeys(hits))[:4]

    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{3,}", normalized)
    counts = Counter(token for token in tokens if token not in {"assistant", "user", "system"})
    return [token for token, _count in counts.most_common(3)]
