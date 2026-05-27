import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


INSTANCE_ID_RE = re.compile(r"\bi-[A-Za-z0-9][A-Za-z0-9_-]*\b")
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%")
MONEY_RE = re.compile(r"(?:节省|省下|减少|降低|降到|可省|能省|每月|月省)[^。\n，,；;]*?(?:\d+(?:\.\d+)?\s?(?:元|块|人民币|rmb|RMB|¥))")


@dataclass
class FinOpsFacts:
    instance_ids: set[str] = field(default_factory=set)
    metric_values: set[str] = field(default_factory=set)
    has_metrics: bool = False
    has_pricing_basis: bool = False


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _parse_json_like(content: Any) -> Any | None:
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def extract_finops_facts(messages: Iterable[Any]) -> FinOpsFacts:
    facts = FinOpsFacts()

    for message in messages:
        parsed = _parse_json_like(getattr(message, "content", message))
        if parsed is None:
            continue

        for item in _iter_dicts(parsed):
            instance_id = item.get("instance_id")
            if isinstance(instance_id, str) and instance_id.strip():
                facts.instance_ids.add(instance_id.strip())

            metrics = item.get("metrics_7d_avg")
            if isinstance(metrics, dict):
                facts.has_metrics = True
                for key in (
                    "cpu_usage_percent",
                    "memory_usage_percent",
                    "network_out_bandwidth_mbps",
                ):
                    value = metrics.get(key)
                    if isinstance(value, (int, float)):
                        facts.metric_values.add(_format_number(value))

            if any(key in item for key in ("price", "amount", "monthly_cost", "saving_amount")):
                facts.has_pricing_basis = True

    return facts


def validate_finops_response(content: str, facts: FinOpsFacts) -> tuple[str, list[str]]:
    issues: list[str] = []
    sanitized = content

    unsupported_ids = sorted(set(INSTANCE_ID_RE.findall(sanitized)) - facts.instance_ids)
    if unsupported_ids:
        issues.append("unsupported_instance_id")
        for instance_id in unsupported_ids:
            sanitized = sanitized.replace(instance_id, "未核实实例ID")

    if not facts.has_metrics and PERCENT_RE.search(sanitized):
        issues.append("unsupported_metric_value")
        sanitized = PERCENT_RE.sub("未核实百分比", sanitized)

    if not facts.has_pricing_basis and MONEY_RE.search(sanitized):
        issues.append("unsupported_savings_amount")
        sanitized = MONEY_RE.sub("在缺少价格计算依据时只能给出定性降本判断", sanitized)

    if issues:
        sanitized = _append_validation_note(sanitized)

    return sanitized, issues


def _format_number(value: int | float) -> str:
    if isinstance(value, int) or value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _append_validation_note(content: str) -> str:
    note = (
        "\n\n事实校验说明：以上回答已移除或替换缺少工具结果支撑的实例 ID、"
        "监控数值或节省金额。当前只能基于已查询到的资源和监控数据给出建议。"
    )
    if "事实校验说明：" in content:
        return content
    return content.rstrip() + note
