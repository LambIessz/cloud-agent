from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from core.workflow.event_log import build_event, emit_event, elapsed_ms, now_ms
from core.workflow.human_checkpoint import (
    DEFAULT_RESUME_AGENT,
    build_checkpoint_record,
    build_confirmed_notice,
    build_pending_prompt,
    build_rejected_notice,
    classify_checkpoint_response,
    contains_high_risk_action,
    summarize_risk,
    update_checkpoint_record,
)
from core.workflow.request_context import ensure_request_metadata, get_request_id
from core.workflow.state import AgentState


logger = logging.getLogger(__name__)


class CheckpointAgentNode:
    """Human-in-the-loop confirmation gate for high-risk requests."""

    def _last_user_message(self, state: AgentState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return ""

        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            return str(last_msg[1])
        if hasattr(last_msg, "content"):
            return str(last_msg.content)
        return str(last_msg)

    def _current_checkpoint(self, state: AgentState) -> dict[str, Any] | None:
        metadata = state.get("metadata", {})
        checkpoint = metadata.get("human_checkpoint")
        return checkpoint if isinstance(checkpoint, dict) else None

    def _emit_checkpoint_event(
        self,
        *,
        request_id: str,
        user_id_hash: str,
        checkpoint: dict[str, Any],
        status: str,
    ) -> None:
        emit_event(
            build_event(
                event_type="human_checkpoint",
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="checkpoint_agent",
                operation="checkpoint_gate",
                status=status,
                checkpoint_id=checkpoint.get("checkpoint_id"),
                resume_agent=checkpoint.get("resume_agent"),
                risk_level=checkpoint.get("risk_level"),
                attempts=checkpoint.get("attempts"),
            )
        )

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        query = self._last_user_message(state)
        metadata = ensure_request_metadata(state.get("metadata", {}))
        metadata["handled_by"] = "checkpoint_agent"
        request_id = get_request_id(metadata)
        user_id_hash = str(metadata.get("user_id_hash", "unknown"))

        checkpoint = self._current_checkpoint(state)
        if checkpoint is None:
            resume_agent = str(metadata.get("checkpoint_resume_agent") or DEFAULT_RESUME_AGENT)
            route_reason = str(metadata.get("checkpoint_reason") or summarize_risk(query))
            if not contains_high_risk_action(query):
                route_reason = str(metadata.get("checkpoint_reason") or "高风险动作需要人工确认")
            checkpoint = build_checkpoint_record(
                query=query,
                resume_agent=resume_agent,
                route_reason=route_reason,
            )
            metadata["human_checkpoint"] = checkpoint
            self._emit_checkpoint_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                checkpoint=checkpoint,
                status="pending",
            )
            logger.info("CheckpointAgent request_id=%s created pending checkpoint", request_id)
            return {
                "messages": [AIMessage(content="\n".join(build_pending_prompt(checkpoint)))],
                "metadata": metadata,
            }

        decision = classify_checkpoint_response(query)
        attempts = int(checkpoint.get("attempts", 0)) + 1

        if decision == "confirmed":
            updated = update_checkpoint_record(
                checkpoint,
                status="confirmed",
                attempts=attempts,
            )
            metadata["human_checkpoint"] = updated
            self._emit_checkpoint_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                checkpoint=updated,
                status="confirmed",
            )
            logger.info("CheckpointAgent request_id=%s confirmed checkpoint", request_id)
            return {
                "messages": [AIMessage(content="\n".join(build_confirmed_notice(updated)))],
                "metadata": metadata,
                "next_agent": str(updated.get("resume_agent") or DEFAULT_RESUME_AGENT),
            }

        if decision == "rejected":
            updated = update_checkpoint_record(
                checkpoint,
                status="rejected",
                attempts=attempts,
            )
            metadata["human_checkpoint"] = updated
            self._emit_checkpoint_event(
                request_id=request_id,
                user_id_hash=user_id_hash,
                checkpoint=updated,
                status="rejected",
            )
            logger.info("CheckpointAgent request_id=%s rejected checkpoint", request_id)
            return {
                "messages": [AIMessage(content="\n".join(build_rejected_notice(updated)))],
                "metadata": metadata,
            }

        updated = update_checkpoint_record(
            checkpoint,
            status="pending",
            attempts=attempts,
        )
        metadata["human_checkpoint"] = updated
        self._emit_checkpoint_event(
            request_id=request_id,
            user_id_hash=user_id_hash,
            checkpoint=updated,
            status="pending",
        )
        logger.info("CheckpointAgent request_id=%s awaiting confirmation", request_id)
        return {
            "messages": [AIMessage(content="\n".join(build_pending_prompt(updated)))],
            "metadata": metadata,
        }
