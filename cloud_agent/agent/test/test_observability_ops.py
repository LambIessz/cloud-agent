import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPS_DIR = PROJECT_ROOT / "ops"

SENSITIVE_TERMS = {
    "request_id",
    "user_id",
    "user_id_hash",
    "tenant_id",
    "session_id",
    "thread_id",
    "conversation_id",
    "prompt",
    "completion",
    "matched_question",
}

EXPECTED_ALERTS = {
    "CloudAgentMetricsScrapeDown",
    "CloudAgentRequestErrorRateHigh",
    "CloudAgentRequestAverageLatencyHigh",
    "CloudAgentFallbackRouteRateHigh",
    "CloudAgentSemanticCacheUnavailable",
    "CloudAgentMemoryDegraded",
    "CloudAgentLLMErrorRateHigh",
    "CloudAgentLLMAverageLatencyHigh",
    "CloudAgentMCPToolErrorRateHigh",
    "CloudAgentMCPToolAverageLatencyHigh",
    "CloudAgentRequestP95LatencyHigh",
    "CloudAgentLLMP95LatencyHigh",
    "CloudAgentMCPToolP95LatencyHigh",
    "CloudAgentDegradationBurst",
    "CloudAgentMCPRegistryInitializeFailed",
}

EXPECTED_DASHBOARD_ROWS = {
    "Request",
    "Routing",
    "Cache & Memory",
    "LLM",
    "MCP Tool",
    "Latency Percentiles",
    "LLM Cost & Cache Benefit",
    "Degradation & MCP Registry",
}

EXPECTED_HISTOGRAM_QUANTILE_ALERTS = {
    "CloudAgentRequestP95LatencyHigh",
    "CloudAgentLLMP95LatencyHigh",
    "CloudAgentMCPToolP95LatencyHigh",
}

EXPECTED_HISTOGRAM_QUANTILE_PANELS = {
    "Request p95 latency",
    "Request p99 latency",
    "LLM p95 latency by operation",
    "MCP tool p95 latency by tool",
}

EXPECTED_COST_CACHE_PANELS = {
    "LLM token rate by operation model and type",
    "LLM estimated cost per hour",
    "Net estimated LLM cost per hour",
    "Semantic cache estimated saved calls",
    "Semantic cache estimated saved tokens",
    "Semantic cache estimated saved cost per hour",
}

EXPECTED_COST_CACHE_METRICS = {
    "cloud_agent_llm_token_total",
    "cloud_agent_llm_estimated_cost_usd_total",
    "cloud_agent_semantic_cache_estimated_saved_call_total",
    "cloud_agent_semantic_cache_estimated_saved_token_total",
    "cloud_agent_semantic_cache_estimated_saved_cost_usd_total",
}


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_dashboard_exprs(value):
    if isinstance(value, dict):
        expr = value.get("expr")
        if isinstance(expr, str):
            yield expr
        for child in value.values():
            yield from _iter_dashboard_exprs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dashboard_exprs(child)


def _assert_no_sensitive_terms(text: str):
    lowered = text.lower()
    leaked = {term for term in SENSITIVE_TERMS if term in lowered}
    assert leaked == set()


def test_prometheus_config_scrapes_cloud_agent_metrics_and_loads_rules():
    config = _load_yaml(OPS_DIR / "prometheus" / "prometheus.yml")

    assert config["rule_files"] == ["/etc/prometheus/rules/cloud_agent_alerts.yml"]

    scrape_configs = config["scrape_configs"]
    cloud_agent_jobs = [job for job in scrape_configs if job["job_name"] == "cloud_agent"]
    assert len(cloud_agent_jobs) == 1

    job = cloud_agent_jobs[0]
    assert job["metrics_path"] == "/api/metrics"
    assert job["static_configs"] == [{"targets": ["host.docker.internal:5000"]}]


def test_llm_pricing_example_includes_deepseek_chat_for_local_cost_smoke():
    config = _load_yaml(OPS_DIR / "prometheus" / "llm_pricing.example.yml")
    pricing = config["llm_pricing"]["deepseek-chat"]

    assert pricing["prompt_usd_per_1k"] >= 0
    assert pricing["completion_usd_per_1k"] >= 0


def test_prometheus_alert_rules_keep_expected_contract_without_sensitive_terms():
    rules_file = OPS_DIR / "prometheus" / "cloud_agent_alerts.yml"
    alert_config = _load_yaml(rules_file)

    assert len(alert_config["groups"]) == 1
    group = alert_config["groups"][0]
    assert group["name"] == "cloud_agent.rules"

    rules = group["rules"]
    assert {rule["alert"] for rule in rules} == EXPECTED_ALERTS
    assert len(rules) == 15

    for rule in rules:
        assert set(rule["labels"]) == {"severity", "service"}
        assert rule["labels"]["service"] == "cloud_agent"
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert "summary" in rule["annotations"]
        assert "description" in rule["annotations"]
        if rule["alert"] in EXPECTED_HISTOGRAM_QUANTILE_ALERTS:
            assert "histogram_quantile" in rule["expr"]
            assert "_duration_ms_bucket" in rule["expr"]
        else:
            assert "histogram_quantile" not in rule["expr"]
        _assert_no_sensitive_terms(rule["expr"])
        _assert_no_sensitive_terms(json.dumps(rule["labels"], ensure_ascii=False))
        _assert_no_sensitive_terms(json.dumps(rule["annotations"], ensure_ascii=False))


def test_grafana_dashboard_contract_uses_existing_metrics_without_sensitive_promql():
    dashboard = _load_json(OPS_DIR / "grafana" / "cloud_agent_overview_dashboard.json")

    assert dashboard["title"] == "Cloud Agent Overview"
    assert dashboard["uid"] == "cloud-agent-overview"

    templating_names = {item["name"] for item in dashboard["templating"]["list"]}
    assert {"DS_PROMETHEUS", "job"}.issubset(templating_names)

    row_titles = {
        panel["title"]
        for panel in dashboard["panels"]
        if panel.get("type") == "row"
    }
    assert EXPECTED_DASHBOARD_ROWS.issubset(row_titles)

    panel_by_title = {panel.get("title"): panel for panel in dashboard["panels"]}
    assert EXPECTED_HISTOGRAM_QUANTILE_PANELS.issubset(panel_by_title)
    assert EXPECTED_COST_CACHE_PANELS.issubset(panel_by_title)

    exprs = list(_iter_dashboard_exprs(dashboard))
    assert exprs
    assert all("cloud_agent_" in expr or expr.startswith("up{") for expr in exprs)
    for expr in exprs:
        _assert_no_sensitive_terms(expr)
    for metric in EXPECTED_COST_CACHE_METRICS:
        assert any(metric in expr for expr in exprs)

    for panel_name in EXPECTED_COST_CACHE_PANELS:
        panel_exprs = list(_iter_dashboard_exprs(panel_by_title[panel_name]))
        assert panel_exprs
        assert "estimated" in panel_name.lower() or panel_name == "LLM token rate by operation model and type"
        assert all("histogram_quantile" not in expr for expr in panel_exprs)

    quantile_exprs = [
        expr for panel_name in EXPECTED_HISTOGRAM_QUANTILE_PANELS
        for expr in _iter_dashboard_exprs(panel_by_title[panel_name])
    ]
    assert quantile_exprs
    assert all("histogram_quantile" in expr for expr in quantile_exprs)
    assert all("_duration_ms_bucket" in expr for expr in quantile_exprs)

    non_quantile_exprs = [
        expr for title, panel in panel_by_title.items()
        if title not in EXPECTED_HISTOGRAM_QUANTILE_PANELS
        for expr in _iter_dashboard_exprs(panel)
    ]
    assert all("histogram_quantile" not in expr for expr in non_quantile_exprs)


def test_grafana_provisioning_points_to_dashboard_directory_and_prometheus_datasource():
    dashboard_provider = _load_yaml(
        OPS_DIR / "grafana" / "provisioning" / "dashboards" / "cloud_agent.yml"
    )
    datasource = _load_yaml(
        OPS_DIR / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    )

    provider = dashboard_provider["providers"][0]
    assert provider["name"] == "Cloud Agent"
    assert provider["folder"] == "Cloud Agent"
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"

    datasource_config = datasource["datasources"][0]
    assert datasource_config["name"] == "Prometheus"
    assert datasource_config["type"] == "prometheus"
    assert datasource_config["url"] == "http://prometheus:9090"


def test_otel_console_trace_smoke_script_keeps_request_span_contract():
    script = (OPS_DIR / "otel" / "console_trace_smoke.py").read_text(encoding="utf-8")

    assert "ConsoleSpanExporter" in script
    assert "start_stream_chat_span" in script
    assert "CLOUD_AGENT_TRACE_ENABLED" in script
    assert "CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED" in script
    assert '"request.id"' in script
    assert '"request_id"' not in script
    assert "FORBIDDEN_TOKENS" in script
    assert "secret exception message" in script


def test_otel_backend_smoke_script_uses_otlp_grpc_without_expanding_span_scope():
    script = (OPS_DIR / "otel" / "otlp_backend_smoke.py").read_text(encoding="utf-8")

    assert "OTLPSpanExporter" in script
    assert "TraceServiceServicer" in script
    assert "in_process_otlp_grpc_receiver" in script
    assert "start_stream_chat_span" in script
    assert "CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED" in script
    assert '"request.id"' in script
    assert '"request_id"' not in script
    assert "FORBIDDEN_TOKENS" in script
    assert "secret exception message" in script
