from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any


_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAM_BUCKETS: dict[tuple[str, tuple[tuple[str, str], ...], str], float] = {}
_HISTOGRAM_SUMS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAM_COUNTS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAM_SCHEMAS: dict[str, tuple[str, ...]] = {}
_PRICING_CONFIG_CACHE_KEY: str | None = None
_PRICING_CONFIG_CACHE: dict[str, tuple[float, float]] | None = None
_LOCK = Lock()
_BLOCKED_LABEL_KEYS = {
    "request_id",
    "user_id",
    "user_id_hash",
    "tenant_id",
    "session_id",
    "thread_id",
    "conversation_id",
    "query",
    "prompt",
    "completion",
    "message",
    "matched_question",
    "error_message",
    "preference",
}
REQUEST_DURATION_BUCKETS_MS = (100, 250, 500, 1000, 2000, 3000, 5000, 10000, 30000)
LLM_DURATION_BUCKETS_MS = (250, 500, 1000, 2000, 5000, 10000, 20000, 60000)
TOOL_DURATION_BUCKETS_MS = (50, 100, 250, 500, 1000, 2000, 3000, 5000, 10000)
_MODEL_LABEL_ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-:/")


def _normalize_labels(labels: dict[str, Any] | None = None) -> tuple[tuple[str, str], ...]:
    normalized = {
        str(key): str(value)
        for key, value in (labels or {}).items()
        if value is not None and str(key) not in _BLOCKED_LABEL_KEYS
    }
    return tuple(sorted(normalized.items()))


def _normalize_model_label(value: Any) -> str:
    if value is None:
        return "unknown"
    model = str(value).strip()
    if not model or len(model) > 80:
        return "unknown"
    if any(char not in _MODEL_LABEL_ALLOWED_CHARS for char in model):
        return "unknown"
    return model


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _load_pricing_config() -> dict[str, tuple[float, float]]:
    global _PRICING_CONFIG_CACHE_KEY, _PRICING_CONFIG_CACHE

    config_path = os.getenv("CLOUD_AGENT_LLM_PRICING_CONFIG")
    if not config_path:
        return {}

    if _PRICING_CONFIG_CACHE_KEY == config_path and _PRICING_CONFIG_CACHE is not None:
        return _PRICING_CONFIG_CACHE

    parsed: dict[str, Any] | None = None
    try:
        path = Path(config_path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            parsed = json.loads(text)
        else:
            try:
                import yaml
            except Exception:
                parsed = None
            else:
                parsed = yaml.safe_load(text)
    except Exception:
        parsed = None

    pricing: dict[str, tuple[float, float]] = {}
    raw_pricing = parsed.get("llm_pricing") if isinstance(parsed, dict) else None
    if isinstance(raw_pricing, dict):
        for raw_model, raw_prices in raw_pricing.items():
            if not isinstance(raw_prices, dict):
                continue
            prompt_price = _non_negative_number(raw_prices.get("prompt_usd_per_1k"))
            completion_price = _non_negative_number(raw_prices.get("completion_usd_per_1k"))
            if prompt_price is None or completion_price is None:
                continue
            pricing[_normalize_model_label(raw_model)] = (prompt_price, completion_price)

    _PRICING_CONFIG_CACHE_KEY = config_path
    _PRICING_CONFIG_CACHE = pricing
    return pricing


def _estimate_llm_cost_usd(
    *,
    model: str,
    prompt_tokens: float | None,
    completion_tokens: float | None,
) -> float | None:
    pricing = _load_pricing_config()
    if not pricing:
        return None

    price = pricing.get(model) or pricing.get("unknown")
    if price is None:
        return None

    prompt_price, completion_price = price
    prompt_cost = ((prompt_tokens or 0) / 1000) * prompt_price
    completion_cost = ((completion_tokens or 0) / 1000) * completion_price
    return prompt_cost + completion_cost


def normalize_model_label(value: Any) -> str:
    return _normalize_model_label(value)


def estimate_llm_cost_usd(
    *,
    model: str,
    prompt_tokens: float | None,
    completion_tokens: float | None,
) -> float | None:
    return _estimate_llm_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def increment_counter(
    name: str,
    labels: dict[str, Any] | None = None,
    *,
    amount: int | float = 1,
) -> None:
    if amount < 0:
        return

    key = (name, _normalize_labels(labels))
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + amount


def _bucket_label(boundary: int | float) -> str:
    return _format_metric_value(float(boundary))


def observe_histogram(
    name: str,
    value: int | float,
    buckets: tuple[int | float, ...],
    labels: dict[str, Any] | None = None,
) -> None:
    if value < 0:
        return

    normalized_labels = _normalize_labels(labels)
    sorted_buckets = tuple(sorted(float(bucket) for bucket in buckets))
    bucket_labels = tuple(_bucket_label(bucket) for bucket in sorted_buckets) + ("+Inf",)
    value = float(value)
    key = (name, normalized_labels)

    with _LOCK:
        _HISTOGRAM_SCHEMAS[name] = bucket_labels
        _HISTOGRAM_SUMS[key] = _HISTOGRAM_SUMS.get(key, 0) + value
        _HISTOGRAM_COUNTS[key] = _HISTOGRAM_COUNTS.get(key, 0) + 1
        for boundary, label in zip(sorted_buckets, bucket_labels[:-1]):
            bucket_key = (name, normalized_labels, label)
            _HISTOGRAM_BUCKETS.setdefault(bucket_key, 0)
            if value <= boundary:
                _HISTOGRAM_BUCKETS[bucket_key] += 1
        inf_key = (name, normalized_labels, "+Inf")
        _HISTOGRAM_BUCKETS[inf_key] = _HISTOGRAM_BUCKETS.get(inf_key, 0) + 1


def snapshot_counters() -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_COUNTERS.items())

    snapshot = []
    for (name, labels), value in sorted(items):
        snapshot.append(
            {
                "name": name,
                "labels": dict(labels),
                "value": value,
            }
        )
    return snapshot


def snapshot_histograms() -> list[dict[str, Any]]:
    with _LOCK:
        schemas = dict(_HISTOGRAM_SCHEMAS)
        buckets = dict(_HISTOGRAM_BUCKETS)
        counts = dict(_HISTOGRAM_COUNTS)
        sums = dict(_HISTOGRAM_SUMS)

    snapshot = []
    for (name, labels), count in sorted(counts.items()):
        bucket_values = []
        for le in schemas.get(name, ("+Inf",)):
            bucket_values.append(
                {
                    "le": le,
                    "value": buckets.get((name, labels, le), 0),
                }
            )
        snapshot.append(
            {
                "name": name,
                "labels": dict(labels),
                "buckets": bucket_values,
                "count": count,
                "sum": sums.get((name, labels), 0),
            }
        )
    return snapshot


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_metric_value(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _metric_help(metric_name: str) -> str:
    descriptions = {
        "cloud_agent_event_total": "Count of structured events emitted by cloud_agent.",
        "cloud_agent_event_status_total": "Count of structured events by status.",
        "cloud_agent_event_error_total": "Count of structured events by error type.",
        "cloud_agent_event_latency_ms_count": "Count of structured events with latency.",
        "cloud_agent_event_latency_ms_sum": "Total structured event latency in milliseconds.",
        "cloud_agent_request_total": "Count of completed requests by component, operation, and status.",
        "cloud_agent_request_success_total": "Count of successful completed requests.",
        "cloud_agent_request_error_total": "Count of failed completed requests by error type.",
        "cloud_agent_request_latency_ms_count": "Count of completed requests with latency.",
        "cloud_agent_request_latency_ms_sum": "Total completed request latency in milliseconds.",
        "cloud_agent_request_duration_ms": "Request duration histogram in milliseconds.",
        "cloud_agent_route_total": "Count of route decisions by destination and intent.",
        "cloud_agent_route_fallback_total": "Count of route decisions that used fallback.",
        "cloud_agent_semantic_cache_lookup_total": "Count of semantic cache lookups by status.",
        "cloud_agent_semantic_cache_hit_total": "Count of semantic cache hits.",
        "cloud_agent_semantic_cache_miss_total": "Count of semantic cache misses.",
        "cloud_agent_semantic_cache_degraded_total": "Count of degraded semantic cache lookups.",
        "cloud_agent_semantic_cache_unavailable_total": "Count of unavailable semantic cache lookups.",
        "cloud_agent_semantic_cache_estimated_saved_call_total": "Estimated LLM calls saved by semantic cache hits.",
        "cloud_agent_semantic_cache_estimated_saved_token_total": "Estimated LLM tokens saved by semantic cache hits.",
        "cloud_agent_semantic_cache_estimated_saved_cost_usd_total": "Estimated LLM cost saved by semantic cache hits in USD.",
        "cloud_agent_memory_retrieve_total": "Count of memory retrieval operations by status.",
        "cloud_agent_memory_save_total": "Count of memory save operations by status.",
        "cloud_agent_memory_background_extract_total": "Count of background memory extraction operations by status.",
        "cloud_agent_memory_degraded_total": "Count of degraded or unavailable memory operations.",
        "cloud_agent_memory_retrieved_item_total": "Count of memory items retrieved.",
        "cloud_agent_memory_extracted_preference_total": "Count of preferences extracted into memory.",
        "cloud_agent_llm_call_total": "Count of LLM calls by component, operation, and status.",
        "cloud_agent_llm_error_total": "Count of LLM call errors by type.",
        "cloud_agent_llm_latency_ms_count": "Count of LLM calls with latency.",
        "cloud_agent_llm_latency_ms_sum": "Total LLM call latency in milliseconds.",
        "cloud_agent_llm_duration_ms": "LLM call duration histogram in milliseconds.",
        "cloud_agent_llm_prompt_token_total": "Total LLM prompt tokens.",
        "cloud_agent_llm_completion_token_total": "Total LLM completion tokens.",
        "cloud_agent_llm_token_total": "Total LLM tokens by token type.",
        "cloud_agent_llm_estimated_cost_usd_total": "Estimated LLM cost in USD.",
        "cloud_agent_tool_call_total": "Count of MCP tool calls by tool and status.",
        "cloud_agent_tool_error_total": "Count of MCP tool call errors by type.",
        "cloud_agent_tool_latency_ms_count": "Count of MCP tool calls with latency.",
        "cloud_agent_tool_latency_ms_sum": "Total MCP tool call latency in milliseconds.",
        "cloud_agent_tool_duration_ms": "MCP tool duration histogram in milliseconds.",
        "cloud_agent_degradation_total": "Count of degradation events by component, operation, and status.",
        "cloud_agent_degradation_error_total": "Count of degradation events by error type.",
        "cloud_agent_mcp_registry_initialize_total": "Count of MCP registry initialization events by status.",
        "cloud_agent_mcp_registry_error_total": "Count of MCP registry initialization errors by type.",
        "cloud_agent_mcp_registry_server_count_sum": "Total MCP servers seen during registry initialization.",
        "cloud_agent_mcp_registry_tool_count_sum": "Total MCP tools discovered during registry initialization.",
    }
    return descriptions.get(metric_name, f"Counter metric {metric_name}.")


def render_prometheus_metrics() -> str:
    lines = []
    emitted_metadata: set[str] = set()
    for item in snapshot_counters():
        metric_name = f'cloud_agent_{item["name"]}'
        if metric_name not in emitted_metadata:
            lines.append(f"# HELP {metric_name} {_metric_help(metric_name)}")
            lines.append(f"# TYPE {metric_name} counter")
            emitted_metadata.add(metric_name)
        labels = item["labels"]
        label_text = ""
        if labels:
            parts = [
                f'{key}="{_escape_label_value(str(value))}"'
                for key, value in sorted(labels.items())
            ]
            label_text = "{" + ",".join(parts) + "}"
        lines.append(
            f'{metric_name}{label_text} '
            f'{_format_metric_value(item["value"])}'
        )
    for item in snapshot_histograms():
        metric_name = f'cloud_agent_{item["name"]}'
        if metric_name not in emitted_metadata:
            lines.append(f"# HELP {metric_name} {_metric_help(metric_name)}")
            lines.append(f"# TYPE {metric_name} histogram")
            emitted_metadata.add(metric_name)
        labels = item["labels"]
        for bucket in item["buckets"]:
            bucket_labels = {**labels, "le": bucket["le"]}
            label_text = "{" + ",".join(
                f'{key}="{_escape_label_value(str(value))}"'
                for key, value in sorted(bucket_labels.items())
            ) + "}"
            lines.append(
                f'{metric_name}_bucket{label_text} '
                f'{_format_metric_value(bucket["value"])}'
            )
        label_text = ""
        if labels:
            label_text = "{" + ",".join(
                f'{key}="{_escape_label_value(str(value))}"'
                for key, value in sorted(labels.items())
            ) + "}"
        lines.append(f'{metric_name}_count{label_text} {_format_metric_value(item["count"])}')
        lines.append(f'{metric_name}_sum{label_text} {_format_metric_value(item["sum"])}')
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAM_BUCKETS.clear()
        _HISTOGRAM_SUMS.clear()
        _HISTOGRAM_COUNTS.clear()
        _HISTOGRAM_SCHEMAS.clear()
        global _PRICING_CONFIG_CACHE_KEY, _PRICING_CONFIG_CACHE
        _PRICING_CONFIG_CACHE_KEY = None
        _PRICING_CONFIG_CACHE = None


def record_event_metrics(event: dict[str, Any]) -> None:
    base_labels = {
        "event_type": event.get("event_type", "unknown"),
        "component": event.get("component", "unknown"),
        "operation": event.get("operation", "unknown"),
    }
    increment_counter("event_total", base_labels)

    status = event.get("status")
    if status is not None:
        increment_counter("event_status_total", {**base_labels, "status": status})

    error_type = event.get("error_type")
    if error_type is not None:
        increment_counter("event_error_total", {**base_labels, "error_type": error_type})

    latency_ms = event.get("latency_ms")
    if isinstance(latency_ms, (int, float)):
        latency_labels = dict(base_labels)
        if status is not None:
            latency_labels["status"] = status
        increment_counter("event_latency_ms_count", latency_labels)
        increment_counter("event_latency_ms_sum", latency_labels, amount=float(latency_ms))

    if event.get("event_type") == "request_end":
        request_status = event.get("status", "unknown")
        request_labels = {
            "component": event.get("component", "unknown"),
            "operation": event.get("operation", "unknown"),
            "status": request_status,
        }
        increment_counter("request_total", request_labels)
        if request_status == "success":
            increment_counter(
                "request_success_total",
                {
                    "component": request_labels["component"],
                    "operation": request_labels["operation"],
                },
            )
        if request_status == "error":
            error_labels = {
                "component": request_labels["component"],
                "operation": request_labels["operation"],
            }
            if error_type is not None:
                error_labels["error_type"] = error_type
            increment_counter("request_error_total", error_labels)
        if isinstance(latency_ms, (int, float)):
            increment_counter("request_latency_ms_count", request_labels)
            increment_counter("request_latency_ms_sum", request_labels, amount=float(latency_ms))
            observe_histogram(
                "request_duration_ms",
                latency_ms,
                REQUEST_DURATION_BUCKETS_MS,
                request_labels,
            )

    if event.get("event_type") == "route_decision":
        route_to = event.get("route_to", "unknown")
        route_labels = {
            "route_to": route_to,
            "primary_intent": event.get("primary_intent", "unknown"),
            "is_finops_workflow": event.get("is_finops_workflow", "unknown"),
        }
        increment_counter("route_total", route_labels)
        if route_to == "fallback_agent":
            increment_counter("route_fallback_total")

    if event.get("event_type") == "cache_lookup":
        status = event.get("status", "unknown")
        cache_labels = {
            "component": event.get("component", "semantic_cache"),
            "operation": event.get("operation", "get_cache"),
            "status": status,
        }
        increment_counter("semantic_cache_lookup_total", cache_labels)
        if status in {"hit", "miss", "degraded", "unavailable"}:
            increment_counter(
                f"semantic_cache_{status}_total",
                {
                    "component": cache_labels["component"],
                    "operation": cache_labels["operation"],
                },
            )

    event_type = event.get("event_type")
    if event_type == "cache_benefit":
        cache_benefit_labels = {
            "component": event.get("component", "semantic_cache"),
            "operation": event.get("operation", "unknown"),
        }
        estimated_saved_calls = _non_negative_number(event.get("estimated_saved_calls"))
        if estimated_saved_calls is not None:
            increment_counter(
                "semantic_cache_estimated_saved_call_total",
                cache_benefit_labels,
                amount=estimated_saved_calls,
            )

        estimated_saved_prompt_tokens = _non_negative_number(
            event.get("estimated_saved_prompt_tokens")
        )
        if estimated_saved_prompt_tokens is not None:
            increment_counter(
                "semantic_cache_estimated_saved_token_total",
                {**cache_benefit_labels, "token_type": "prompt"},
                amount=estimated_saved_prompt_tokens,
            )

        estimated_saved_completion_tokens = _non_negative_number(
            event.get("estimated_saved_completion_tokens")
        )
        if estimated_saved_completion_tokens is not None:
            increment_counter(
                "semantic_cache_estimated_saved_token_total",
                {**cache_benefit_labels, "token_type": "completion"},
                amount=estimated_saved_completion_tokens,
            )

        estimated_saved_cost_usd = _non_negative_number(
            event.get("estimated_saved_cost_usd")
        )
        if estimated_saved_cost_usd is not None:
            increment_counter(
                "semantic_cache_estimated_saved_cost_usd_total",
                cache_benefit_labels,
                amount=estimated_saved_cost_usd,
            )

    if event_type == "mcp_registry_initialize":
        status = event.get("status", "unknown")
        registry_labels = {
            "component": event.get("component", "mcp"),
            "operation": event.get("operation", "tool_registry_initialize"),
            "status": status,
        }
        increment_counter("mcp_registry_initialize_total", registry_labels)
        if error_type is not None:
            increment_counter(
                "mcp_registry_error_total",
                {**registry_labels, "error_type": error_type},
            )

        server_count = event.get("server_count")
        if isinstance(server_count, (int, float)):
            increment_counter(
                "mcp_registry_server_count_sum",
                registry_labels,
                amount=float(server_count),
            )

        tool_count = event.get("tool_count")
        if isinstance(tool_count, (int, float)):
            increment_counter(
                "mcp_registry_tool_count_sum",
                registry_labels,
                amount=float(tool_count),
            )

    if event_type == "degradation":
        status = event.get("status", "unknown")
        degradation_labels = {
            "component": event.get("component", "unknown"),
            "operation": event.get("operation", "unknown"),
            "status": status,
        }
        increment_counter("degradation_total", degradation_labels)
        if error_type is not None:
            increment_counter(
                "degradation_error_total",
                {**degradation_labels, "error_type": error_type},
            )

    if event_type in {"memory_retrieve", "memory_save", "background_extract"}:
        status = event.get("status", "unknown")
        memory_labels = {
            "component": event.get("component", "memory"),
            "operation": event.get("operation", "unknown"),
            "status": status,
        }
        metric_name_by_event = {
            "memory_retrieve": "memory_retrieve_total",
            "memory_save": "memory_save_total",
            "background_extract": "memory_background_extract_total",
        }
        increment_counter(metric_name_by_event[event_type], memory_labels)
        if status in {"degraded", "unavailable"}:
            increment_counter(
                "memory_degraded_total",
                {**memory_labels, "event_type": event_type},
            )

        retrieved_count = event.get("retrieved_count")
        if event_type == "memory_retrieve" and isinstance(retrieved_count, (int, float)):
            increment_counter(
                "memory_retrieved_item_total",
                {
                    "component": memory_labels["component"],
                    "operation": memory_labels["operation"],
                },
                amount=float(retrieved_count),
            )

        extracted_count = event.get("extracted_count")
        if event_type == "background_extract" and isinstance(extracted_count, (int, float)):
            increment_counter(
                "memory_extracted_preference_total",
                {
                    "component": memory_labels["component"],
                    "operation": memory_labels["operation"],
                },
                amount=float(extracted_count),
            )

    if event_type == "llm_call":
        status = event.get("status", "unknown")
        llm_labels = {
            "component": event.get("component", "unknown"),
            "operation": event.get("operation", "unknown"),
            "status": status,
        }
        increment_counter("llm_call_total", llm_labels)
        if error_type is not None:
            increment_counter("llm_error_total", {**llm_labels, "error_type": error_type})
        if isinstance(latency_ms, (int, float)):
            increment_counter("llm_latency_ms_count", llm_labels)
            increment_counter("llm_latency_ms_sum", llm_labels, amount=float(latency_ms))
            observe_histogram(
                "llm_duration_ms",
                latency_ms,
                LLM_DURATION_BUCKETS_MS,
                llm_labels,
            )
        model = _normalize_model_label(event.get("model"))
        token_labels = {**llm_labels, "model": model}
        prompt_tokens = event.get("prompt_tokens")
        prompt_token_count = _non_negative_number(prompt_tokens)
        if prompt_token_count is not None:
            increment_counter(
                "llm_prompt_token_total",
                token_labels,
                amount=prompt_token_count,
            )
            increment_counter(
                "llm_token_total",
                {**token_labels, "token_type": "prompt"},
                amount=prompt_token_count,
            )
        completion_tokens = event.get("completion_tokens")
        completion_token_count = _non_negative_number(completion_tokens)
        if completion_token_count is not None:
            increment_counter(
                "llm_completion_token_total",
                token_labels,
                amount=completion_token_count,
            )
            increment_counter(
                "llm_token_total",
                {**token_labels, "token_type": "completion"},
                amount=completion_token_count,
            )
        estimated_cost_usd = _estimate_llm_cost_usd(
            model=model,
            prompt_tokens=prompt_token_count,
            completion_tokens=completion_token_count,
        )
        if estimated_cost_usd is not None:
            increment_counter(
                "llm_estimated_cost_usd_total",
                token_labels,
                amount=estimated_cost_usd,
            )

    if event_type == "tool_call":
        status = event.get("status", "unknown")
        tool_labels = {
            "component": event.get("component", "mcp_tool"),
            "operation": event.get("operation", "tool_call"),
            "tool_name": event.get("tool_name", "unknown"),
            "status": status,
        }
        increment_counter("tool_call_total", tool_labels)
        if error_type is not None:
            increment_counter("tool_error_total", {**tool_labels, "error_type": error_type})
        if isinstance(latency_ms, (int, float)):
            increment_counter("tool_latency_ms_count", tool_labels)
            increment_counter("tool_latency_ms_sum", tool_labels, amount=float(latency_ms))
            observe_histogram(
                "tool_duration_ms",
                latency_ms,
                TOOL_DURATION_BUCKETS_MS,
                tool_labels,
            )
