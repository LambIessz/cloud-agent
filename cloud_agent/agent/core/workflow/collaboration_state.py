from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Mapping, Sequence

from core.workflow.event_log import now_ms


INSTANCE_ID_RE = re.compile(r"\bi-[A-Za-z0-9][A-Za-z0-9-]{2,}\b")
DEFAULT_COLLABORATION_MODE = "billing_finops_synthesis"


def _compact_text(text: Any, limit: int = 240) -> str:
    normalized = " ".join(
        part.strip()
        for part in str(text or "").splitlines()
        if str(part).strip()
    ).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _unique_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def extract_resource_ids(text: Any) -> list[str]:
    if not text:
        return []
    return _unique_strings(INSTANCE_ID_RE.findall(str(text)))


def has_collaboration_state(metadata: Mapping[str, Any] | None) -> bool:
    state = (metadata or {}).get("collaboration_state")
    return isinstance(state, Mapping)


def get_collaboration_state(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    state = (metadata or {}).get("collaboration_state")
    return dict(state) if isinstance(state, Mapping) else None


def seed_collaboration_state(
    metadata: Mapping[str, Any] | None,
    *,
    mode: str = DEFAULT_COLLABORATION_MODE,
    participants: Sequence[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(metadata or {}))
    current = get_collaboration_state(updated) or {}
    participant_list = _unique_strings([
        *list(current.get("participants") or []),
        *(list(participants or [])),
    ])

    collaboration_id = str(
        current.get("collaboration_id")
        or updated.get("request_id")
        or f"collab_{uuid.uuid4().hex[:12]}"
    )
    current["collaboration_id"] = collaboration_id
    current["mode"] = str(mode or current.get("mode") or DEFAULT_COLLABORATION_MODE)
    current["status"] = str(current.get("status") or "collecting")
    current["participants"] = participant_list
    current.setdefault("findings", [])
    current.setdefault("conflicts", [])
    current.setdefault("merged_summary", "")
    current.setdefault("stage", "seeded")
    current.setdefault("created_at_ms", now_ms())
    current["updated_at_ms"] = now_ms()
    if reason:
        current["reason"] = _compact_text(reason, 240)
    updated["collaboration_state"] = current
    updated["collaboration_flow"] = current["mode"]
    return updated


def append_collaboration_finding(
    metadata: Mapping[str, Any] | None,
    *,
    agent_name: str,
    summary: str,
    stage: str | None = None,
    status: str = "success",
    notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    if not has_collaboration_state(metadata):
        return copy.deepcopy(dict(metadata or {}))

    updated = copy.deepcopy(dict(metadata or {}))
    current = get_collaboration_state(updated) or {}
    findings = list(current.get("findings") or [])
    finding_summary = _compact_text(summary, 320)
    finding = {
        "agent": str(agent_name).strip(),
        "stage": str(stage or agent_name).strip() or "unknown",
        "summary": finding_summary,
        "resource_ids": extract_resource_ids(summary),
        "status": str(status or "success"),
        "created_at_ms": now_ms(),
        "updated_at_ms": now_ms(),
    }
    if notes:
        finding["notes"] = _unique_strings(notes)
    findings.append(finding)
    current["findings"] = findings
    current["stage"] = finding["stage"]
    current["status"] = str(status or current.get("status") or "collecting")
    current["updated_at_ms"] = now_ms()
    updated["collaboration_state"] = current
    return updated


def finalize_collaboration_state(
    metadata: Mapping[str, Any] | None,
    *,
    merged_summary: str,
    conflicts: Sequence[str] | None = None,
    status: str = "merged",
) -> dict[str, Any]:
    if not has_collaboration_state(metadata):
        return copy.deepcopy(dict(metadata or {}))

    updated = copy.deepcopy(dict(metadata or {}))
    current = get_collaboration_state(updated) or {}
    current["status"] = str(status or current.get("status") or "merged")
    current["merged_summary"] = _compact_text(merged_summary, 600)
    if conflicts is not None:
        current["conflicts"] = _unique_strings(conflicts)
    current["updated_at_ms"] = now_ms()
    updated["collaboration_state"] = current
    return updated


def recent_assistant_summaries(messages: Sequence[Any], limit: int = 2) -> list[str]:
    collected: list[str] = []
    for message in reversed(list(messages)):
        content: str | None = None
        if isinstance(message, tuple):
            if len(message) < 2 or str(message[0]).lower() != "assistant":
                continue
            content = str(message[1])
        elif hasattr(message, "content"):
            content = str(getattr(message, "content"))

        if not content or not content.strip():
            continue

        collected.append(_compact_text(content, 260))
        if len(collected) >= max(1, limit):
            break

    return list(reversed(collected))
