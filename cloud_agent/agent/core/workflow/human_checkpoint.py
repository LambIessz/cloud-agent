from __future__ import annotations

import re
import time
import uuid
from typing import Any


DEFAULT_RESUME_AGENT = "support_agent"
CHECKPOINT_AGENT = "checkpoint_agent"

_RISK_KEYWORDS = (
    ("删除", "delete"),
    ("释放", "release"),
    ("停机", "stop"),
    ("停止", "stop"),
    ("关机", "shutdown"),
    ("重启", "restart"),
    ("重置", "reset"),
    ("修改", "modify"),
    ("变更", "modify"),
    ("扩容", "scale_up"),
    ("缩容", "scale_down"),
    ("迁移", "migrate"),
    ("回滚", "rollback"),
    ("重建", "rebuild"),
    ("解绑", "detach"),
    ("切换", "switch"),
    ("清空", "wipe"),
    ("开放", "expose"),
    ("放通", "allow"),
)
_RESOURCE_HINTS = (
    "ecs",
    "rds",
    "安全组",
    "白名单",
    "公网",
    "内网",
    "vpc",
    "实例",
    "服务器",
    "数据库",
    "云盘",
    "磁盘",
    "端口",
    "ip",
    "slb",
    "alb",
    "nat",
    "redis",
)
_CONFIRM_HINTS = ("确认", "继续", "执行", "同意", "可以", "开始", "好的", "ok", "yes", "y")
_REJECT_HINTS = ("取消", "不要", "停止", "算了", "放弃", "不执行", "no", "n")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.strip().lower())


def contains_high_risk_action(query: str) -> bool:
    normalized = _normalize(query)
    if not normalized:
        return False
    has_risk = any(keyword in normalized for keyword, _label in _RISK_KEYWORDS)
    has_resource = any(hint in normalized for hint in _RESOURCE_HINTS)
    return has_risk and has_resource


def classify_checkpoint_response(query: str) -> str:
    normalized = _normalize(query)
    if not normalized:
        return "pending"
    if any(hint in normalized for hint in _REJECT_HINTS):
        return "rejected"
    if any(hint in normalized for hint in _CONFIRM_HINTS):
        return "confirmed"
    return "pending"


def summarize_risk(query: str) -> str:
    normalized = _normalize(query)
    matched = [label for keyword, label in _RISK_KEYWORDS if keyword in normalized]
    if not matched:
        return "检测到高风险动作，需要人工确认。"
    readable = "、".join(dict.fromkeys(matched))
    return f"检测到高风险动作：{readable}"


def build_checkpoint_record(
    *,
    query: str,
    resume_agent: str = DEFAULT_RESUME_AGENT,
    route_reason: str = "",
    risk_level: str = "high",
    attempts: int = 0,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "checkpoint_id": f"chk_{uuid.uuid4().hex[:12]}",
        "status": "pending",
        "resume_agent": resume_agent or DEFAULT_RESUME_AGENT,
        "risk_level": risk_level,
        "action_summary": query.strip()[:160],
        "source_query": query.strip(),
        "route_reason": route_reason or summarize_risk(query),
        "attempts": attempts,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }


def update_checkpoint_record(
    checkpoint: dict[str, Any],
    *,
    status: str,
    attempts: int | None = None,
    route_reason: str | None = None,
) -> dict[str, Any]:
    updated = dict(checkpoint)
    updated["status"] = status
    updated["updated_at_ms"] = int(time.time() * 1000)
    if attempts is not None:
        updated["attempts"] = attempts
    if route_reason is not None:
        updated["route_reason"] = route_reason
    return updated


def build_pending_prompt(checkpoint: dict[str, Any]) -> list[str]:
    action = str(checkpoint.get("action_summary") or checkpoint.get("source_query") or "该动作")
    reason = str(checkpoint.get("route_reason") or "高风险动作")
    return [
        "这一步会影响线上资源，我先停下来等你确认。",
        f"- 待确认动作：{action}",
        f"- 风险说明：{reason}",
        "- 回复“确认”继续，回复“取消”终止。",
    ]


def build_confirmed_notice(checkpoint: dict[str, Any]) -> list[str]:
    action = str(checkpoint.get("action_summary") or checkpoint.get("source_query") or "该动作")
    return [
        "已收到确认，我会继续处理刚才的高风险请求。",
        f"- 原始动作：{action}",
    ]


def build_rejected_notice(checkpoint: dict[str, Any]) -> list[str]:
    action = str(checkpoint.get("action_summary") or checkpoint.get("source_query") or "该动作")
    return [
        "已取消刚才的高风险请求。",
        f"- 取消动作：{action}",
    ]
