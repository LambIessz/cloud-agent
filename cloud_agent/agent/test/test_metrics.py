import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from router.metrics import router as metrics_router
from router.metrics import metrics_endpoint

from core.workflow.degradation_audit import build_degradation_event, emit_degradation
from core.workflow.event_log import build_event, emit_event
from core.workflow.metrics import (
    increment_counter,
    observe_histogram,
    render_prometheus_metrics,
    reset_metrics,
    snapshot_counters,
    snapshot_histograms,
)
from core.workflow.tool_audit import build_tool_audit_event, emit_tool_audit


@pytest.fixture(autouse=True)
def _reset_metrics_between_tests():
    reset_metrics()
    yield
    reset_metrics()


def _counter(name: str, **labels):
    for item in snapshot_counters():
        if item["name"] == name and item["labels"] == labels:
            return item["value"]
    return 0


def test_emit_event_records_event_status_and_error_metrics(capsys):
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_metrics",
            user_id_hash="hash_metrics",
            component="chat_service",
            operation="stream_chat",
            status="error",
            error_type="RuntimeError",
            latency_ms=0,
        )
    )

    assert _counter(
        "event_total",
        event_type="request_end",
        component="chat_service",
        operation="stream_chat",
    ) == 1
    assert _counter(
        "event_status_total",
        event_type="request_end",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 1
    assert _counter(
        "event_error_total",
        event_type="request_end",
        component="chat_service",
        operation="stream_chat",
        error_type="RuntimeError",
    ) == 1
    assert _counter(
        "event_latency_ms_count",
        event_type="request_end",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 1
    assert _counter(
        "event_latency_ms_sum",
        event_type="request_end",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 0
    assert _counter(
        "request_total",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 1
    assert _counter(
        "request_error_total",
        component="chat_service",
        operation="stream_chat",
        error_type="RuntimeError",
    ) == 1
    assert _counter(
        "request_latency_ms_count",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 1
    assert _counter(
        "request_latency_ms_sum",
        component="chat_service",
        operation="stream_chat",
        status="error",
    ) == 0

    capsys.readouterr()


def test_tool_audit_and_degradation_mirrors_record_metrics(capsys):
    emit_tool_audit(
        build_tool_audit_event(
            request_id="req_tool_metrics",
            user_id_hash="hash_tool_metrics",
            tool_name="query_user_orders",
            latency_ms=3,
            status="success",
            identity_injected=True,
        )
    )
    emit_degradation(
        build_degradation_event(
            request_id="req_degrade_metrics",
            user_id_hash="hash_degrade_metrics",
            component="redis",
            operation="short_memory_get",
            error_type="ConnectionError",
        )
    )

    assert _counter(
        "event_status_total",
        event_type="tool_call",
        component="mcp_tool",
        operation="tool_call",
        status="success",
    ) == 1
    assert _counter(
        "event_status_total",
        event_type="degradation",
        component="redis",
        operation="short_memory_get",
        status="degraded",
    ) == 1
    assert _counter(
        "event_error_total",
        event_type="degradation",
        component="redis",
        operation="short_memory_get",
        error_type="ConnectionError",
    ) == 1
    assert _counter(
        "degradation_total",
        component="redis",
        operation="short_memory_get",
        status="degraded",
    ) == 1
    assert _counter(
        "degradation_error_total",
        component="redis",
        operation="short_memory_get",
        status="degraded",
        error_type="ConnectionError",
    ) == 1
    assert _counter(
        "tool_call_total",
        component="mcp_tool",
        operation="tool_call",
        tool_name="query_user_orders",
        status="success",
    ) == 1
    assert _counter(
        "tool_latency_ms_count",
        component="mcp_tool",
        operation="tool_call",
        tool_name="query_user_orders",
        status="success",
    ) == 1
    assert _counter(
        "tool_latency_ms_sum",
        component="mcp_tool",
        operation="tool_call",
        tool_name="query_user_orders",
        status="success",
    ) == 3

    capsys.readouterr()


def test_render_prometheus_metrics_escapes_labels():
    increment_counter(
        "event_total",
        {
            "event_type": 'request"end',
            "component": "chat\\service",
            "operation": "stream\nchat",
        },
        amount=2,
    )

    text = render_prometheus_metrics()

    assert "# TYPE cloud_agent_event_total counter" in text
    assert (
        'cloud_agent_event_total{component="chat\\\\service",'
        'event_type="request\\"end",operation="stream\\nchat"} 2'
    ) in text


def test_increment_counter_drops_high_cardinality_and_sensitive_labels():
    increment_counter(
        "event_total",
        {
            "component": "chat_service",
            "operation": "stream_chat",
            "status": "success",
            "request_id": "req_should_not_export",
            "user_id": "plain_user_should_not_export",
            "user_id_hash": "hash_should_not_export",
            "tenant_id": "tenant_should_not_export",
            "session_id": "session_should_not_export",
            "thread_id": "thread_should_not_export",
            "query": "query_should_not_export",
            "prompt": "prompt_should_not_export",
            "completion": "completion_should_not_export",
            "matched_question": "matched_should_not_export",
        },
    )

    snapshot = snapshot_counters()
    text = render_prometheus_metrics()

    assert snapshot == [
        {
            "name": "event_total",
            "labels": {
                "component": "chat_service",
                "operation": "stream_chat",
                "status": "success",
            },
            "value": 1,
        }
    ]
    assert "req_should_not_export" not in text
    assert "plain_user_should_not_export" not in text
    assert "hash_should_not_export" not in text
    assert "tenant_should_not_export" not in text
    assert "session_should_not_export" not in text
    assert "thread_should_not_export" not in text
    assert "query_should_not_export" not in text
    assert "prompt_should_not_export" not in text
    assert "completion_should_not_export" not in text
    assert "matched_should_not_export" not in text


def test_observe_histogram_outputs_cumulative_buckets_and_filters_sensitive_labels():
    observe_histogram(
        "request_duration_ms",
        120,
        (100, 250),
        {
            "component": "chat_service",
            "operation": "stream_chat",
            "status": "success",
            "request_id": "req_should_not_export",
            "user_id": "plain_user_should_not_export",
            "prompt": "prompt_should_not_export",
        },
    )

    snapshot = snapshot_histograms()
    text = render_prometheus_metrics()

    assert snapshot == [
        {
            "name": "request_duration_ms",
            "labels": {
                "component": "chat_service",
                "operation": "stream_chat",
                "status": "success",
            },
            "buckets": [
                {"le": "100", "value": 0},
                {"le": "250", "value": 1},
                {"le": "+Inf", "value": 1},
            ],
            "count": 1,
            "sum": 120,
        }
    ]
    assert "# HELP cloud_agent_request_duration_ms Request duration histogram in milliseconds." in text
    assert "# TYPE cloud_agent_request_duration_ms histogram" in text
    assert (
        'cloud_agent_request_duration_ms_bucket{component="chat_service",'
        'le="100",operation="stream_chat",status="success"} 0'
    ) in text
    assert (
        'cloud_agent_request_duration_ms_bucket{component="chat_service",'
        'le="250",operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_request_duration_ms_bucket{component="chat_service",'
        'le="+Inf",operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_request_duration_ms_count{component="chat_service",'
        'operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_request_duration_ms_sum{component="chat_service",'
        'operation="stream_chat",status="success"} 120'
    ) in text
    assert "req_should_not_export" not in text
    assert "plain_user_should_not_export" not in text
    assert "prompt_should_not_export" not in text


def test_observe_histogram_ignores_negative_values_and_reset_clears_histograms():
    observe_histogram(
        "request_duration_ms",
        -1,
        (100, 250),
        {"component": "chat_service"},
    )
    assert snapshot_histograms() == []

    observe_histogram(
        "request_duration_ms",
        42,
        (100, 250),
        {"component": "chat_service"},
    )
    assert snapshot_histograms()

    reset_metrics()

    assert snapshot_counters() == []
    assert snapshot_histograms() == []


def test_render_prometheus_metrics_emits_help_and_type_per_metric_family(capsys):
    emit_event(
        build_event(
            event_type="cache_lookup",
            request_id="req_help_cache",
            user_id_hash="hash_help_cache",
            component="semantic_cache",
            operation="get_cache",
            status="hit",
        )
    )
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_help_llm",
            user_id_hash="hash_help_llm",
            component="orchestrator",
            operation="route_classification",
            status="success",
            latency_ms=5,
        )
    )

    text = render_prometheus_metrics()

    assert text.count("# HELP cloud_agent_event_total ") == 1
    assert text.count("# TYPE cloud_agent_event_total counter") == 1
    assert text.count("# HELP cloud_agent_semantic_cache_hit_total ") == 1
    assert text.count("# TYPE cloud_agent_semantic_cache_hit_total counter") == 1
    assert text.count("# HELP cloud_agent_llm_call_total ") == 1
    assert text.count("# TYPE cloud_agent_llm_call_total counter") == 1
    assert text.count("# HELP cloud_agent_llm_latency_ms_sum ") == 1
    assert text.count("# TYPE cloud_agent_llm_latency_ms_sum counter") == 1

    capsys.readouterr()


def test_latency_events_record_duration_histograms(capsys):
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_request_histogram",
            user_id_hash="hash_request_histogram",
            component="chat_service",
            operation="stream_chat",
            status="success",
            latency_ms=1200,
        )
    )
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_histogram",
            user_id_hash="hash_llm_histogram",
            component="orchestrator",
            operation="route_classification",
            status="success",
            latency_ms=3200,
        )
    )
    emit_tool_audit(
        build_tool_audit_event(
            request_id="req_tool_histogram",
            user_id_hash="hash_tool_histogram",
            tool_name="query_user_orders",
            latency_ms=700,
            status="success",
            identity_injected=True,
        )
    )

    text = render_prometheus_metrics()

    assert "# TYPE cloud_agent_request_duration_ms histogram" in text
    assert (
        'cloud_agent_request_duration_ms_bucket{component="chat_service",le="1000",'
        'operation="stream_chat",status="success"} 0'
    ) in text
    assert (
        'cloud_agent_request_duration_ms_bucket{component="chat_service",le="2000",'
        'operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_llm_duration_ms_bucket{component="orchestrator",le="5000",'
        'operation="route_classification",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_tool_duration_ms_bucket{component="mcp_tool",le="1000",'
        'operation="tool_call",status="success",tool_name="query_user_orders"} 1'
    ) in text
    assert (
        'cloud_agent_tool_duration_ms_count{component="mcp_tool",operation="tool_call",'
        'status="success",tool_name="query_user_orders"} 1'
    ) in text
    assert (
        'cloud_agent_tool_duration_ms_sum{component="mcp_tool",operation="tool_call",'
        'status="success",tool_name="query_user_orders"} 700'
    ) in text
    assert "req_request_histogram" not in text
    assert "hash_request_histogram" not in text

    capsys.readouterr()


def test_render_prometheus_metrics_includes_latency_count_and_sum(capsys):
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_latency_a",
            user_id_hash="hash_latency",
            component="chat_service",
            operation="stream_chat",
            status="success",
            latency_ms=12,
        )
    )
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_latency_b",
            user_id_hash="hash_latency",
            component="chat_service",
            operation="stream_chat",
            status="success",
            latency_ms=8,
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_event_latency_ms_count{component="chat_service",'
        'event_type="request_end",operation="stream_chat",status="success"} 2'
    ) in text
    assert (
        'cloud_agent_event_latency_ms_sum{component="chat_service",'
        'event_type="request_end",operation="stream_chat",status="success"} 20'
    ) in text

    capsys.readouterr()


def test_request_end_records_dedicated_request_metrics(capsys):
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_request_success",
            user_id_hash="hash_request_metrics",
            component="chat_service",
            operation="stream_chat",
            status="success",
            latency_ms=15,
        )
    )
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_request_error",
            user_id_hash="hash_request_metrics",
            component="chat_service",
            operation="stream_chat",
            status="error",
            error_type="RuntimeError",
            latency_ms=25,
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_request_total{component="chat_service",'
        'operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_request_total{component="chat_service",'
        'operation="stream_chat",status="error"} 1'
    ) in text
    assert (
        'cloud_agent_request_success_total{component="chat_service",'
        'operation="stream_chat"} 1'
    ) in text
    assert (
        'cloud_agent_request_error_total{component="chat_service",'
        'error_type="RuntimeError",operation="stream_chat"} 1'
    ) in text
    assert (
        'cloud_agent_request_latency_ms_count{component="chat_service",'
        'operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_request_latency_ms_sum{component="chat_service",'
        'operation="stream_chat",status="error"} 25'
    ) in text
    assert "# TYPE cloud_agent_request_total counter" in text
    assert "# TYPE cloud_agent_request_error_total counter" in text
    assert "req_request_success" not in text
    assert "hash_request_metrics" not in text

    capsys.readouterr()


def test_route_decision_records_route_metrics(capsys):
    emit_event(
        build_event(
            event_type="route_decision",
            request_id="req_route_metrics",
            user_id_hash="hash_route_metrics",
            component="orchestrator",
            operation="route",
            route_to="billing_agent",
            primary_intent="finops",
            is_finops_workflow=True,
        )
    )
    emit_event(
        build_event(
            event_type="route_decision",
            request_id="req_route_fallback",
            user_id_hash="hash_route_metrics",
            component="orchestrator",
            operation="route",
            route_to="fallback_agent",
            primary_intent="fallback",
            is_finops_workflow=False,
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_route_total{is_finops_workflow="True",'
        'primary_intent="finops",route_to="billing_agent"} 1'
    ) in text
    assert (
        'cloud_agent_route_total{is_finops_workflow="False",'
        'primary_intent="fallback",route_to="fallback_agent"} 1'
    ) in text
    assert "cloud_agent_route_fallback_total 1" in text

    capsys.readouterr()


def test_cache_lookup_records_dedicated_cache_metrics(capsys):
    for status in ("hit", "miss", "degraded", "unavailable"):
        emit_event(
            build_event(
                event_type="cache_lookup",
                request_id=f"req_cache_{status}",
                user_id_hash="hash_cache_metrics",
                component="semantic_cache",
                operation="get_cache",
                status=status,
            )
        )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_semantic_cache_lookup_total{component="semantic_cache",'
        'operation="get_cache",status="hit"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_lookup_total{component="semantic_cache",'
        'operation="get_cache",status="miss"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_hit_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_miss_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_degraded_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_unavailable_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in text

    capsys.readouterr()


def test_cache_benefit_records_estimated_saved_metrics(capsys):
    emit_event(
        build_event(
            event_type="cache_benefit",
            request_id="req_cache_benefit",
            user_id_hash="hash_cache_benefit",
            tenant_id="tenant_cache_benefit",
            component="semantic_cache",
            operation="stream_chat",
            status="estimated",
            estimated_saved_calls=1,
            estimated_saved_prompt_tokens=120,
            estimated_saved_completion_tokens=80,
            estimated_saved_cost_usd=0.00014,
            prompt="prompt_should_not_export",
            completion="completion_should_not_export",
            query="query_should_not_export",
            matched_question="matched_should_not_export",
            error_message="error_message_should_not_export",
            preference="preference_should_not_export",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_semantic_cache_estimated_saved_call_total{component="semantic_cache",'
        'operation="stream_chat"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_estimated_saved_token_total{component="semantic_cache",'
        'operation="stream_chat",token_type="prompt"} 120'
    ) in text
    assert (
        'cloud_agent_semantic_cache_estimated_saved_token_total{component="semantic_cache",'
        'operation="stream_chat",token_type="completion"} 80'
    ) in text
    assert (
        'cloud_agent_semantic_cache_estimated_saved_cost_usd_total{component="semantic_cache",'
        'operation="stream_chat"} 0.00014'
    ) in text
    assert "# TYPE cloud_agent_semantic_cache_estimated_saved_call_total counter" in text
    assert "# TYPE cloud_agent_semantic_cache_estimated_saved_token_total counter" in text
    assert "# TYPE cloud_agent_semantic_cache_estimated_saved_cost_usd_total counter" in text

    for forbidden in (
        "req_cache_benefit",
        "hash_cache_benefit",
        "tenant_cache_benefit",
        "prompt_should_not_export",
        "completion_should_not_export",
        "query_should_not_export",
        "matched_should_not_export",
        "error_message_should_not_export",
        "preference_should_not_export",
    ):
        assert forbidden not in text

    capsys.readouterr()


def test_cache_benefit_ignores_negative_estimates(capsys):
    emit_event(
        build_event(
            event_type="cache_benefit",
            request_id="req_cache_benefit_negative",
            user_id_hash="hash_cache_benefit_negative",
            component="semantic_cache",
            operation="stream_chat",
            status="estimated",
            estimated_saved_calls=-1,
            estimated_saved_prompt_tokens=-120,
            estimated_saved_completion_tokens=-80,
            estimated_saved_cost_usd=-0.00014,
        )
    )

    text = render_prometheus_metrics()

    assert "cloud_agent_semantic_cache_estimated_saved_call_total" not in text
    assert "cloud_agent_semantic_cache_estimated_saved_token_total" not in text
    assert "cloud_agent_semantic_cache_estimated_saved_cost_usd_total" not in text
    assert "req_cache_benefit_negative" not in text
    assert "hash_cache_benefit_negative" not in text

    capsys.readouterr()


def test_memory_events_record_dedicated_memory_metrics(capsys):
    emit_event(
        build_event(
            event_type="memory_retrieve",
            request_id="req_memory_retrieve_metrics",
            user_id_hash="hash_memory_metrics",
            component="redis",
            operation="short_memory_get",
            status="success",
            retrieved_count=3,
        )
    )
    emit_event(
        build_event(
            event_type="memory_retrieve",
            request_id="req_memory_retrieve_degraded_metrics",
            user_id_hash="hash_memory_metrics",
            component="milvus",
            operation="long_memory_retrieve",
            status="degraded",
            error_type="RuntimeError",
        )
    )
    emit_event(
        build_event(
            event_type="memory_save",
            request_id="req_memory_save_unavailable_metrics",
            user_id_hash="hash_memory_metrics",
            component="redis",
            operation="short_memory_save",
            status="unavailable",
        )
    )
    emit_event(
        build_event(
            event_type="background_extract",
            request_id="req_background_extract_metrics",
            user_id_hash="hash_memory_metrics",
            component="memory",
            operation="background_preference_extract",
            status="success",
            extracted_count=2,
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_memory_retrieve_total{component="redis",'
        'operation="short_memory_get",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_memory_retrieve_total{component="milvus",'
        'operation="long_memory_retrieve",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_memory_save_total{component="redis",'
        'operation="short_memory_save",status="unavailable"} 1'
    ) in text
    assert (
        'cloud_agent_memory_background_extract_total{component="memory",'
        'operation="background_preference_extract",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_memory_degraded_total{component="milvus",event_type="memory_retrieve",'
        'operation="long_memory_retrieve",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_memory_degraded_total{component="redis",event_type="memory_save",'
        'operation="short_memory_save",status="unavailable"} 1'
    ) in text
    assert (
        'cloud_agent_memory_retrieved_item_total{component="redis",'
        'operation="short_memory_get"} 3'
    ) in text
    assert (
        'cloud_agent_memory_extracted_preference_total{component="memory",'
        'operation="background_preference_extract"} 2'
    ) in text

    capsys.readouterr()


def test_llm_call_records_dedicated_llm_metrics(capsys):
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_success",
            user_id_hash="hash_llm_metrics",
            component="orchestrator",
            operation="route_classification",
            status="success",
            latency_ms=7,
        )
    )
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_error",
            user_id_hash="hash_llm_metrics",
            component="orchestrator",
            operation="route_classification",
            status="error",
            latency_ms=11,
            error_type="TimeoutError",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_llm_call_total{component="orchestrator",'
        'operation="route_classification",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_llm_call_total{component="orchestrator",'
        'operation="route_classification",status="error"} 1'
    ) in text
    assert (
        'cloud_agent_llm_error_total{component="orchestrator",error_type="TimeoutError",'
        'operation="route_classification",status="error"} 1'
    ) in text
    assert (
        'cloud_agent_llm_latency_ms_count{component="orchestrator",'
        'operation="route_classification",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_llm_latency_ms_sum{component="orchestrator",'
        'operation="route_classification",status="success"} 7'
    ) in text
    assert "req_llm_success" not in text
    assert "hash_llm_metrics" not in text

    capsys.readouterr()


def test_llm_call_records_token_metrics_without_prompt_or_completion_text(capsys):
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_tokens",
            user_id_hash="hash_llm_tokens",
            component="orchestrator",
            operation="route_classification",
            status="success",
            model="qwen-plus",
            prompt_tokens=120,
            completion_tokens=18,
            prompt="prompt_should_not_export",
            completion="completion_should_not_export",
            query="query_should_not_export",
            matched_question="matched_should_not_export",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_llm_prompt_token_total{component="orchestrator",model="qwen-plus",'
        'operation="route_classification",status="success"} 120'
    ) in text
    assert (
        'cloud_agent_llm_completion_token_total{component="orchestrator",model="qwen-plus",'
        'operation="route_classification",status="success"} 18'
    ) in text
    assert (
        'cloud_agent_llm_token_total{component="orchestrator",model="qwen-plus",'
        'operation="route_classification",status="success",token_type="prompt"} 120'
    ) in text
    assert (
        'cloud_agent_llm_token_total{component="orchestrator",model="qwen-plus",'
        'operation="route_classification",status="success",token_type="completion"} 18'
    ) in text
    assert "# TYPE cloud_agent_llm_prompt_token_total counter" in text
    assert "# TYPE cloud_agent_llm_completion_token_total counter" in text
    assert "# TYPE cloud_agent_llm_token_total counter" in text

    for forbidden in (
        "req_llm_tokens",
        "hash_llm_tokens",
        "prompt_should_not_export",
        "completion_should_not_export",
        "query_should_not_export",
        "matched_should_not_export",
    ):
        assert forbidden not in text

    capsys.readouterr()


def test_llm_token_metrics_ignore_negative_values_and_normalize_untrusted_model(capsys):
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_negative_tokens",
            user_id_hash="hash_llm_negative_tokens",
            component="orchestrator",
            operation="route_classification",
            status="success",
            model="model with leaked text",
            prompt_tokens=-1,
            completion_tokens=10,
        )
    )

    text = render_prometheus_metrics()

    assert "cloud_agent_llm_prompt_token_total" not in text
    assert (
        'cloud_agent_llm_completion_token_total{component="orchestrator",model="unknown",'
        'operation="route_classification",status="success"} 10'
    ) in text
    assert "model with leaked text" not in text

    capsys.readouterr()


def test_llm_call_records_estimated_cost_from_pricing_config(monkeypatch, tmp_path, capsys):
    pricing_config = tmp_path / "llm_pricing.yml"
    pricing_config.write_text(
        "\n".join(
            [
                "llm_pricing:",
                "  qwen-plus:",
                "    prompt_usd_per_1k: 0.5",
                "    completion_usd_per_1k: 1.5",
                "  unknown:",
                "    prompt_usd_per_1k: 0",
                "    completion_usd_per_1k: 0",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUD_AGENT_LLM_PRICING_CONFIG", str(pricing_config))
    reset_metrics()

    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_cost",
            user_id_hash="hash_llm_cost",
            component="orchestrator",
            operation="route_classification",
            status="success",
            model="qwen-plus",
            prompt_tokens=1000,
            completion_tokens=2000,
            prompt="prompt_should_not_export",
            completion="completion_should_not_export",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_llm_estimated_cost_usd_total{component="orchestrator",'
        'model="qwen-plus",operation="route_classification",status="success"} 3.5'
    ) in text
    assert "# TYPE cloud_agent_llm_estimated_cost_usd_total counter" in text
    assert "req_llm_cost" not in text
    assert "hash_llm_cost" not in text
    assert "prompt_should_not_export" not in text
    assert "completion_should_not_export" not in text

    capsys.readouterr()


def test_llm_estimated_cost_is_not_recorded_without_pricing_config(monkeypatch, capsys):
    monkeypatch.delenv("CLOUD_AGENT_LLM_PRICING_CONFIG", raising=False)
    reset_metrics()

    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_llm_no_cost",
            user_id_hash="hash_llm_no_cost",
            component="orchestrator",
            operation="route_classification",
            status="success",
            model="qwen-plus",
            prompt_tokens=1000,
            completion_tokens=1000,
        )
    )

    text = render_prometheus_metrics()

    assert "cloud_agent_llm_estimated_cost_usd_total" not in text
    assert "req_llm_no_cost" not in text
    assert "hash_llm_no_cost" not in text

    capsys.readouterr()


def test_tool_call_records_dedicated_tool_metrics(capsys):
    emit_tool_audit(
        build_tool_audit_event(
            request_id="req_tool_success",
            user_id_hash="hash_tool_metrics",
            tool_name="query_user_instances",
            latency_ms=9,
            status="success",
            identity_injected=True,
        )
    )
    emit_tool_audit(
        build_tool_audit_event(
            request_id="req_tool_error",
            user_id_hash="hash_tool_metrics",
            tool_name="analyze_instance_usage",
            latency_ms=13,
            status="error",
            error_type="TimeoutError",
            identity_injected=True,
            attempt=1,
            max_attempts=2,
            timeout_seconds=1.0,
            retryable=True,
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_tool_call_total{component="mcp_tool",operation="tool_call",'
        'status="success",tool_name="query_user_instances"} 1'
    ) in text
    assert (
        'cloud_agent_tool_call_total{component="mcp_tool",operation="tool_call",'
        'status="error",tool_name="analyze_instance_usage"} 1'
    ) in text
    assert (
        'cloud_agent_tool_error_total{component="mcp_tool",error_type="TimeoutError",'
        'operation="tool_call",status="error",tool_name="analyze_instance_usage"} 1'
    ) in text
    assert (
        'cloud_agent_tool_latency_ms_count{component="mcp_tool",operation="tool_call",'
        'status="success",tool_name="query_user_instances"} 1'
    ) in text
    assert (
        'cloud_agent_tool_latency_ms_sum{component="mcp_tool",operation="tool_call",'
        'status="error",tool_name="analyze_instance_usage"} 13'
    ) in text
    assert "# TYPE cloud_agent_tool_call_total counter" in text
    assert "# TYPE cloud_agent_tool_error_total counter" in text
    assert "req_tool_success" not in text
    assert "hash_tool_metrics" not in text

    capsys.readouterr()


def test_degradation_records_dedicated_degradation_metrics(capsys):
    emit_degradation(
        build_degradation_event(
            request_id="req_degradation_unavailable",
            user_id_hash="hash_degradation_metrics",
            component="semantic_cache",
            operation="get_cache",
            status="unavailable",
        )
    )
    emit_degradation(
        build_degradation_event(
            request_id="req_degradation_error",
            user_id_hash="hash_degradation_metrics",
            component="milvus",
            operation="long_memory_retrieve",
            error_type="RuntimeError",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_degradation_total{component="semantic_cache",'
        'operation="get_cache",status="unavailable"} 1'
    ) in text
    assert (
        'cloud_agent_degradation_total{component="milvus",'
        'operation="long_memory_retrieve",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_degradation_error_total{component="milvus",'
        'error_type="RuntimeError",operation="long_memory_retrieve",status="degraded"} 1'
    ) in text
    assert "# TYPE cloud_agent_degradation_total counter" in text
    assert "# TYPE cloud_agent_degradation_error_total counter" in text
    assert "req_degradation_unavailable" not in text
    assert "hash_degradation_metrics" not in text

    capsys.readouterr()


def test_mcp_registry_initialize_records_dedicated_metrics(capsys):
    emit_event(
        build_event(
            event_type="mcp_registry_initialize",
            request_id="req_mcp_metrics_success",
            user_id_hash="hash_mcp_metrics",
            component="mcp",
            operation="tool_registry_initialize",
            status="success",
            server_count=2,
            tool_count=9,
        )
    )
    emit_event(
        build_event(
            event_type="mcp_registry_initialize",
            request_id="req_mcp_metrics_error",
            user_id_hash="hash_mcp_metrics",
            component="mcp",
            operation="tool_registry_initialize",
            status="degraded",
            error_type="RuntimeError",
        )
    )

    text = render_prometheus_metrics()

    assert (
        'cloud_agent_mcp_registry_initialize_total{component="mcp",'
        'operation="tool_registry_initialize",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_mcp_registry_initialize_total{component="mcp",'
        'operation="tool_registry_initialize",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_mcp_registry_error_total{component="mcp",'
        'error_type="RuntimeError",operation="tool_registry_initialize",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_mcp_registry_server_count_sum{component="mcp",'
        'operation="tool_registry_initialize",status="success"} 2'
    ) in text
    assert (
        'cloud_agent_mcp_registry_tool_count_sum{component="mcp",'
        'operation="tool_registry_initialize",status="success"} 9'
    ) in text
    assert "# TYPE cloud_agent_mcp_registry_initialize_total counter" in text
    assert "# TYPE cloud_agent_mcp_registry_error_total counter" in text
    assert "req_mcp_metrics_success" not in text
    assert "hash_mcp_metrics" not in text

    capsys.readouterr()


def test_metrics_endpoint_returns_prometheus_text():
    increment_counter(
        "event_status_total",
        {
            "event_type": "request_end",
            "component": "chat_service",
            "operation": "stream_chat",
            "status": "success",
        },
    )

    response = asyncio.run(metrics_endpoint())
    body = response.body.decode("utf-8")

    assert response.media_type == "text/plain; version=0.0.4; charset=utf-8"
    assert (
        'cloud_agent_event_status_total{component="chat_service",'
        'event_type="request_end",operation="stream_chat",status="success"} 1'
    ) in body


def test_api_metrics_route_returns_prometheus_text():
    emit_event(
        build_event(
            event_type="cache_lookup",
            request_id="req_metrics_route",
            user_id_hash="hash_metrics_route",
            component="semantic_cache",
            operation="get_cache",
            status="hit",
        )
    )
    app = FastAPI()
    app.include_router(metrics_router, prefix="/api")

    response = TestClient(app).get("/api/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'cloud_agent_semantic_cache_hit_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in response.text
    assert "# TYPE cloud_agent_semantic_cache_hit_total counter" in response.text
    assert "req_metrics_route" not in response.text
    assert "hash_metrics_route" not in response.text


def test_api_metrics_route_returns_complete_observability_metrics(capsys):
    emit_event(
        build_event(
            event_type="request_end",
            request_id="req_endpoint_request",
            user_id_hash="hash_endpoint_request",
            tenant_id="tenant_endpoint",
            component="chat_service",
            operation="stream_chat",
            status="success",
            latency_ms=17,
        )
    )
    emit_event(
        build_event(
            event_type="route_decision",
            request_id="req_endpoint_route",
            user_id_hash="hash_endpoint_route",
            tenant_id="tenant_endpoint",
            component="orchestrator",
            operation="route",
            route_to="billing_agent",
            primary_intent="finops",
            is_finops_workflow=True,
        )
    )
    emit_event(
        build_event(
            event_type="cache_lookup",
            request_id="req_endpoint_cache",
            user_id_hash="hash_endpoint_cache",
            tenant_id="tenant_endpoint",
            component="semantic_cache",
            operation="get_cache",
            status="miss",
            matched_question="matched_endpoint_should_not_export",
        )
    )
    emit_event(
        build_event(
            event_type="memory_retrieve",
            request_id="req_endpoint_memory",
            user_id_hash="hash_endpoint_memory",
            tenant_id="tenant_endpoint",
            component="redis",
            operation="short_memory_get",
            status="success",
            retrieved_count=4,
        )
    )
    emit_event(
        build_event(
            event_type="llm_call",
            request_id="req_endpoint_llm",
            user_id_hash="hash_endpoint_llm",
            tenant_id="tenant_endpoint",
            component="orchestrator",
            operation="route_classification",
            status="success",
            latency_ms=6,
            prompt="prompt_endpoint_should_not_export",
            completion="completion_endpoint_should_not_export",
            query="query_endpoint_should_not_export",
        )
    )
    emit_tool_audit(
        build_tool_audit_event(
            request_id="req_endpoint_tool",
            user_id_hash="hash_endpoint_tool",
            tool_name="query_user_orders",
            latency_ms=5,
            status="success",
            identity_injected=True,
        )
    )
    emit_degradation(
        build_degradation_event(
            request_id="req_endpoint_degradation",
            user_id_hash="hash_endpoint_degradation",
            component="milvus",
            operation="long_memory_retrieve",
            error_type="RuntimeError",
        )
    )
    emit_event(
        build_event(
            event_type="mcp_registry_initialize",
            request_id="req_endpoint_mcp",
            user_id_hash="hash_endpoint_mcp",
            tenant_id="tenant_endpoint",
            component="mcp",
            operation="tool_registry_initialize",
            status="success",
            server_count=1,
            tool_count=8,
        )
    )

    app = FastAPI()
    app.include_router(metrics_router, prefix="/api")

    response = TestClient(app).get("/api/metrics")
    text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'cloud_agent_request_total{component="chat_service",'
        'operation="stream_chat",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_route_total{is_finops_workflow="True",'
        'primary_intent="finops",route_to="billing_agent"} 1'
    ) in text
    assert (
        'cloud_agent_semantic_cache_miss_total{component="semantic_cache",'
        'operation="get_cache"} 1'
    ) in text
    assert (
        'cloud_agent_memory_retrieve_total{component="redis",'
        'operation="short_memory_get",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_llm_call_total{component="orchestrator",'
        'operation="route_classification",status="success"} 1'
    ) in text
    assert (
        'cloud_agent_tool_call_total{component="mcp_tool",operation="tool_call",'
        'status="success",tool_name="query_user_orders"} 1'
    ) in text
    assert (
        'cloud_agent_degradation_error_total{component="milvus",'
        'error_type="RuntimeError",operation="long_memory_retrieve",status="degraded"} 1'
    ) in text
    assert (
        'cloud_agent_mcp_registry_tool_count_sum{component="mcp",'
        'operation="tool_registry_initialize",status="success"} 8'
    ) in text

    for metric_name in (
        "cloud_agent_request_total",
        "cloud_agent_route_total",
        "cloud_agent_semantic_cache_miss_total",
        "cloud_agent_memory_retrieve_total",
        "cloud_agent_llm_call_total",
        "cloud_agent_tool_call_total",
        "cloud_agent_degradation_error_total",
        "cloud_agent_mcp_registry_tool_count_sum",
    ):
        assert text.count(f"# HELP {metric_name} ") == 1
        assert text.count(f"# TYPE {metric_name} counter") == 1

    for forbidden in (
        "req_endpoint_",
        "hash_endpoint_",
        "tenant_endpoint",
        "prompt_endpoint_should_not_export",
        "completion_endpoint_should_not_export",
        "query_endpoint_should_not_export",
        "matched_endpoint_should_not_export",
    ):
        assert forbidden not in text

    capsys.readouterr()
