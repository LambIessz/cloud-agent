from uuid import uuid4

from core.workflow.llm_metrics import LLMCallMetricsCallback, extract_llm_usage
from core.workflow.metrics import render_prometheus_metrics, reset_metrics


class _Message:
    def __init__(self, *, usage_metadata=None, response_metadata=None):
        self.usage_metadata = usage_metadata or {}
        self.response_metadata = response_metadata or {}


class _Generation:
    def __init__(self, message):
        self.message = message


class _Result:
    def __init__(self, message):
        self.generations = [[_Generation(message)]]
        self.llm_output = {}


def _callback():
    return LLMCallMetricsCallback(
        request_id="req_callback_test",
        user_id_hash="hash_callback_test",
        tenant_id="tenant_callback_test",
        component="billing_agent",
        operation="react_agent",
        fallback_model="deepseek-chat",
    )


def test_llm_callback_records_usage_without_identity_or_text_labels(capsys):
    reset_metrics()
    callback = _callback()
    run_id = uuid4()
    message = _Message(
        usage_metadata={"input_tokens": 21, "output_tokens": 8},
        response_metadata={"model_name": "deepseek-v4-flash"},
    )

    callback.on_llm_start({}, ["secret prompt"], run_id=run_id)
    callback.on_llm_end(_Result(message), run_id=run_id)

    metrics = render_prometheus_metrics()
    output = capsys.readouterr().out
    assert 'cloud_agent_llm_call_total{component="billing_agent",operation="react_agent",status="success"} 1' in metrics
    assert 'cloud_agent_llm_prompt_token_total{component="billing_agent",model="deepseek-v4-flash",operation="react_agent",status="success"} 21' in metrics
    assert 'cloud_agent_llm_completion_token_total{component="billing_agent",model="deepseek-v4-flash",operation="react_agent",status="success"} 8' in metrics
    assert "req_callback_test" not in metrics
    assert "hash_callback_test" not in metrics
    assert "secret prompt" not in metrics
    assert "secret prompt" not in output


def test_llm_callback_records_error_type_without_error_message(capsys):
    reset_metrics()
    callback = _callback()
    run_id = uuid4()

    callback.on_llm_start({}, [], run_id=run_id)
    callback.on_llm_error(TimeoutError("sensitive provider detail"), run_id=run_id)

    metrics = render_prometheus_metrics()
    output = capsys.readouterr().out
    assert 'cloud_agent_llm_call_total{component="billing_agent",operation="react_agent",status="error"} 1' in metrics
    assert 'cloud_agent_llm_error_total{component="billing_agent",error_type="TimeoutError",operation="react_agent",status="error"} 1' in metrics
    assert "sensitive provider detail" not in metrics
    assert "sensitive provider detail" not in output


def test_extract_llm_usage_uses_fallback_model_when_provider_omits_it():
    message = _Message(usage_metadata={"prompt_tokens": 3, "completion_tokens": 2})

    usage = extract_llm_usage(_Result(message), fallback_model="fallback-model")

    assert usage == {"model": "fallback-model", "prompt_tokens": 3, "completion_tokens": 2}
