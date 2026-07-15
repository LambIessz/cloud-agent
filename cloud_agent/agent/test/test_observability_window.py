import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "observability_window.py"
START_SCRIPT_PATH = PROJECT_ROOT / "ops" / "start_observability_window.ps1"


def _load_window():
    spec = importlib.util.spec_from_file_location("observability_window", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_window_defaults_to_a_bounded_24_hour_low_frequency_monitor():
    window = _load_window()
    args = window.parse_args([])

    assert args.duration_hours == 24
    assert args.interval_seconds == 300
    assert args.require_llm_metric is False
    assert args.require_tool_metric is False


def test_window_report_keeps_only_aggregate_health_and_alert_counts():
    window = _load_window()
    args = window.parse_args([])
    report = window.create_report(args, datetime(2026, 7, 14, tzinfo=timezone.utc))
    sample = {
        "timestamp_utc": "2026-07-14T00:05:00Z",
        "status": window.DEGRADED,
        "checks": {"healthz": window.PASS, "target_up": window.FAIL},
        "firing_alert_count": 2,
    }

    window.record_sample(report, sample)

    assert report["interval_seconds"] == 300
    assert report["summary"] == {
        "sample_count": 1,
        "healthy_samples": 0,
        "degraded_samples": 1,
        "alerting_samples": 1,
        "check_failures": {"target_up": 1},
    }
    rendered = json.dumps(report, ensure_ascii=False)
    assert "prompt" not in rendered
    assert "labels" not in rendered


def test_window_prometheus_counter_discards_metric_labels_after_count(monkeypatch):
    window = _load_window()
    payload = {
        "status": "success",
        "data": {"result": [{"metric": {"request_id": "sensitive"}, "value": [0, "1"]}]},
    }
    monkeypatch.setattr(window, "_request", lambda *_args, **_kwargs: (200, json.dumps(payload).encode()))

    assert window._prometheus_result_count("http://prometheus", "cloud_agent_request_total", 1) == 1


def test_windows_start_helper_runs_hidden_monitor_without_embedding_credentials():
    script = START_SCRIPT_PATH.read_text(encoding="utf-8")

    for value in (
        "observability_window.py",
        "Start-Process",
        "-WindowStyle Hidden",
        "latest.json",
        "--duration-hours",
        "--interval-seconds",
    ):
        assert value in script
    assert "DEEPSEEK_API_KEY" not in script
    assert "GRAFANA_PASSWORD" not in script
