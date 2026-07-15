from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from core.workflow.event_log import build_event, elapsed_ms, emit_event, now_ms


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_non_negative_int(*values: object) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0 and value.is_integer():
            return int(value)
    return None


def _first_generation_message(response: object) -> object:
    generations = getattr(response, "generations", None)
    if not isinstance(generations, list) or not generations:
        return response
    first_group = generations[0]
    if not isinstance(first_group, list) or not first_group:
        return response
    return getattr(first_group[0], "message", first_group[0])


def extract_llm_usage(response: object, fallback_model: object = None) -> dict[str, object]:
    message = _first_generation_message(response)
    usage_metadata = _mapping(getattr(message, "usage_metadata", None))
    response_metadata = _mapping(getattr(message, "response_metadata", None))
    llm_output = _mapping(getattr(response, "llm_output", None))
    token_usage = _mapping(
        response_metadata.get("token_usage")
        or response_metadata.get("usage")
        or llm_output.get("token_usage")
        or llm_output.get("usage")
    )
    return {
        "model": response_metadata.get("model_name")
        or response_metadata.get("model")
        or llm_output.get("model_name")
        or llm_output.get("model")
        or fallback_model,
        "prompt_tokens": _first_non_negative_int(
            usage_metadata.get("input_tokens"),
            usage_metadata.get("prompt_tokens"),
            token_usage.get("prompt_tokens"),
            token_usage.get("input_tokens"),
        ),
        "completion_tokens": _first_non_negative_int(
            usage_metadata.get("output_tokens"),
            usage_metadata.get("completion_tokens"),
            token_usage.get("completion_tokens"),
            token_usage.get("output_tokens"),
        ),
    }


class LLMCallMetricsCallback(BaseCallbackHandler):
    """Emit one low-cardinality metric event for each LangChain model run."""

    raise_error = False
    run_inline = True

    def __init__(
        self,
        *,
        request_id: str,
        user_id_hash: str,
        tenant_id: str | None,
        component: str,
        operation: str,
        fallback_model: object = None,
    ) -> None:
        self.request_id = request_id
        self.user_id_hash = user_id_hash
        self.tenant_id = tenant_id
        self.component = component
        self.operation = operation
        self.fallback_model = fallback_model
        self._starts: dict[object, float] = {}

    def _base_event(self, *, status: str, latency_ms: int, **extra: object) -> dict[str, object]:
        return build_event(
            event_type="llm_call",
            request_id=self.request_id,
            user_id_hash=self.user_id_hash,
            tenant_id=self.tenant_id,
            component=self.component,
            operation=self.operation,
            status=status,
            latency_ms=latency_ms,
            **extra,
        )

    def _latency_ms(self, run_id: object) -> int:
        start_ms = self._starts.pop(run_id, None)
        return elapsed_ms(start_ms) if start_ms is not None else 0

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: object, **_: Any) -> None:
        del serialized, prompts
        self._starts[run_id] = now_ms()

    def on_llm_end(self, response: object, *, run_id: object, **_: Any) -> None:
        emit_event(
            self._base_event(
                status="success",
                latency_ms=self._latency_ms(run_id),
                **extract_llm_usage(response, self.fallback_model),
            )
        )

    def on_llm_error(self, error: BaseException, *, run_id: object, **_: Any) -> None:
        emit_event(
            self._base_event(
                status="error",
                latency_ms=self._latency_ms(run_id),
                error_type=error.__class__.__name__,
            )
        )
