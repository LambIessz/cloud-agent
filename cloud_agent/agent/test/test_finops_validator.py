from langchain_core.messages import AIMessage, ToolMessage

from core.workflow.finops_validator import (
    FinOpsFacts,
    extract_finops_facts,
    validate_finops_response,
)


def test_extract_finops_facts_from_tool_messages():
    messages = [
        ToolMessage(
            content='{"status":"success","data":[{"instance_id":"i-bp123","status":"Running"}]}',
            tool_call_id="call_1",
        ),
        ToolMessage(
            content=(
                '{"status":"success","data":{"instance_id":"i-bp123",'
                '"metrics_7d_avg":{"cpu_usage_percent":5.2,'
                '"memory_usage_percent":21.5,"network_out_bandwidth_mbps":1.2},'
                '"diagnosis":"RESOURCES_IDLE"}}'
            ),
            tool_call_id="call_2",
        ),
    ]

    facts = extract_finops_facts(messages)

    assert facts.instance_ids == {"i-bp123"}
    assert facts.has_metrics is True
    assert {"5.2", "21.5", "1.2"} <= facts.metric_values


def test_validate_replaces_unsupported_instance_id():
    content = "实例 i-fake999 近 7 天资源闲置，建议降配。"

    sanitized, issues = validate_finops_response(
        content,
        FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True),
    )

    assert "i-fake999" not in sanitized
    assert "未核实实例ID" in sanitized
    assert "unsupported_instance_id" in issues
    assert "事实校验说明" in sanitized


def test_validate_removes_metric_values_without_tool_metrics():
    content = "该实例 CPU 平均 5%，内存 20%，建议观察。"

    sanitized, issues = validate_finops_response(content, FinOpsFacts())

    assert "5%" not in sanitized
    assert "20%" not in sanitized
    assert sanitized.count("未核实百分比") == 2
    assert "unsupported_metric_value" in issues


def test_validate_removes_savings_amount_without_pricing_basis():
    content = "降配后预计每月可节省 300 元，建议立即执行。"

    sanitized, issues = validate_finops_response(
        content,
        FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True),
    )

    assert "300 元" not in sanitized
    assert "定性降本判断" in sanitized
    assert "unsupported_savings_amount" in issues


def test_validate_keeps_supported_instance_and_qualitative_advice():
    content = "实例 i-bp123 监控显示资源偏闲置，建议评估降配，但具体节省金额需要价格数据计算。"

    sanitized, issues = validate_finops_response(
        content,
        FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True),
    )

    assert sanitized == content
    assert issues == []


def test_extract_ignores_non_json_messages():
    facts = extract_finops_facts([AIMessage(content="普通回答")])

    assert facts.instance_ids == set()
    assert facts.has_metrics is False
