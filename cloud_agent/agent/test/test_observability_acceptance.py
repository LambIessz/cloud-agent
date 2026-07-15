import importlib.util
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "observability_acceptance.py"


def _load_acceptance():
    spec = importlib.util.spec_from_file_location("observability_acceptance", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_metric_family_count_rejects_sensitive_terms_without_returning_body():
    acceptance = _load_acceptance()
    text = "# HELP cloud_agent_request_total Count\n# TYPE cloud_agent_request_total counter\n"

    assert acceptance.metric_family_count(text) == 1

    try:
        acceptance.metric_family_count('cloud_agent_request_total{request_id="secret"} 1')
    except RuntimeError as error:
        assert str(error) == "forbidden metrics field detected"
    else:
        raise AssertionError("sensitive metrics terms must fail acceptance")


def test_prometheus_result_count_validates_success_and_minimum():
    acceptance = _load_acceptance()
    payload = {"status": "success", "data": {"result": [{"metric": {}, "value": [0, "1"]}]}}

    assert acceptance.prometheus_result_count(payload, minimum=1) == 1

    try:
        acceptance.prometheus_result_count(payload, minimum=2)
    except RuntimeError as error:
        assert str(error) == "prometheus result count below minimum"
    else:
        raise AssertionError("missing Prometheus samples must fail acceptance")


def test_acceptance_summary_uses_release_evidence_tsv_contract(tmp_path):
    acceptance = _load_acceptance()
    summary = tmp_path / "summary.tsv"
    steps = [
        acceptance.StepResult("healthz", acceptance.PASS, "http_status=200"),
        acceptance.StepResult("grafana_dashboard", acceptance.BLOCKED, "credentials_not_set"),
    ]

    acceptance.write_summary(summary, steps)
    report = acceptance.build_report(summary, steps)

    assert summary.read_text(encoding="utf-8") == (
        "step\tstatus\tdetail\n"
        "healthz\tPASS\thttp_status=200\n"
        "grafana_dashboard\tBLOCKED\tcredentials_not_set\n"
    )
    assert report["status"] == "ready"
    assert report["summary"] == {"steps": 2, "passed": 1, "failed": 0, "blocked": 1}
    assert json.dumps(report, ensure_ascii=False).find("credentials_not_set") == -1


def test_acceptance_defaults_to_a_prometheus_scrape_wait_after_chat():
    acceptance = _load_acceptance()

    assert acceptance.parse_args([]).post_chat_wait_seconds == 20
    assert acceptance.parse_args(["--post-chat-wait-seconds", "0"]).post_chat_wait_seconds == 0


def test_acceptance_supports_in_memory_targeted_metric_requirements():
    acceptance = _load_acceptance()
    args = acceptance.parse_args(
        [
            "--run-chat-smoke",
            "--chat-smoke-text",
            "synthetic billing request",
            "--require-llm-metric",
            "--require-tool-metric",
        ]
    )

    assert args.chat_smoke_text == "synthetic billing request"
    assert args.require_llm_metric is True
    assert args.require_tool_metric is True
