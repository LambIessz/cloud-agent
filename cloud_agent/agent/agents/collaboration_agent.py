from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.messages import AIMessage

from core.workflow.collaboration_state import (
    finalize_collaboration_state,
    get_collaboration_state,
    has_collaboration_state,
    recent_assistant_summaries,
)
from core.workflow.event_log import build_event, emit_event
from core.workflow.request_context import ensure_request_metadata, get_request_id
from core.workflow.state import AgentState


logger = logging.getLogger(__name__)


def _cross_check_findings(findings: list[dict[str, Any]]) -> list[str]:
    resource_sets = [
        set(str(item).strip() for item in finding.get("resource_ids", []) if str(item).strip())
        for finding in findings
        if isinstance(finding, dict)
    ]
    resource_sets = [item for item in resource_sets if item]
    if len(resource_sets) < 2:
        return ["当前证据不足，暂时只能做顺序合并，无法强校验资源是否完全一致。"]

    shared = set.intersection(*resource_sets)
    if shared:
        return [f"交叉校验通过，多个 Agent 指向同一资源：{', '.join(sorted(shared))}"]
    return ["交叉校验未命中共同资源标识，请补充 instance_id 或其他唯一资源标识后再精确合并。"]


class CollaborationSynthesisAgent:
    """Merge multi-agent findings into a single final answer."""

    def _build_message(self, state: AgentState) -> tuple[str, dict[str, Any], list[str]]:
        metadata = ensure_request_metadata(state.get("metadata", {}))
        findings_state = get_collaboration_state(metadata) or {}
        findings = list(findings_state.get("findings") or [])
        fallback_summaries = recent_assistant_summaries(state.get("messages", []), limit=2)

        if not findings and fallback_summaries:
            for index, summary in enumerate(fallback_summaries, start=1):
                findings.append(
                    {
                        "agent": f"assistant_{index}",
                        "stage": f"assistant_{index}",
                        "summary": summary,
                        "resource_ids": [],
                        "status": "success",
                    }
                )

        billing_finding = next((item for item in findings if item.get("agent") == "billing_agent"), None)
        finops_finding = next((item for item in findings if item.get("agent") == "finops_agent"), None)

        lines = ["我把多 Agent 的结果合并好了。"]
        if billing_finding:
            lines.append(f"- 账单/资源侧：{billing_finding.get('summary')}")
        if finops_finding:
            lines.append(f"- 成本分析侧：{finops_finding.get('summary')}")

        if findings:
            lines.append("- 交叉校验：")
            lines.extend(f"- {line}" for line in _cross_check_findings(findings))

        if not billing_finding and not finops_finding and fallback_summaries:
            lines.append(f"- 最近结果：{'; '.join(fallback_summaries)}")

        merged_summary = "\n".join(lines)
        status = "merged" if billing_finding and finops_finding else "degraded"
        updated_metadata = finalize_collaboration_state(
            metadata,
            merged_summary=merged_summary,
            conflicts=[] if status == "merged" else ["evidence_incomplete"],
            status=status,
        )
        updated_metadata["handled_by"] = "collaboration_agent"
        return merged_summary, updated_metadata, findings

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        content, metadata, findings = self._build_message(state)
        request_id = get_request_id(metadata)
        collaboration_state = get_collaboration_state(metadata) or {}
        emit_event(
            build_event(
                event_type="collaboration_synthesis",
                request_id=request_id,
                user_id_hash=str(metadata.get("user_id_hash", "unknown")),
                tenant_id=state.get("tenant_id"),
                component="collaboration_agent",
                operation="synthesize",
                status=str(collaboration_state.get("status") or "unknown"),
                finding_count=len(findings),
            )
        )
        logger.info(
            "CollaborationAgent request_id=%s findings=%d",
            request_id,
            len(findings),
        )
        return {
            "messages": [AIMessage(content=content)],
            "metadata": metadata,
            "next_agent": "",
        }
