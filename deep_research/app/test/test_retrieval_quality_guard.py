import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from langchain_core.messages import AIMessage

from mult_agents.graph import should_continue_research
from mult_agents.nodes import (
    _assess_retrieval_quality,
    _build_queries,
    _quality_filter_records,
    analyze_node,
    reflect_node,
)
from mult_agents.state import create_initial_state


class _JsonAgent:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, _payload):
        return {"messages": [AIMessage(content=self.content)]}


def test_quality_filter_drops_irrelevant_web_records():
    records = [
        {
            "title": "unrelated cooking tips",
            "snippet": "recipe and kitchen notes",
            "domain": "example.com",
            "search_query": "AI Agent 使用趋势",
        },
        {
            "title": "AI Agent trend report",
            "snippet": "AI Agent adoption and usage trend",
            "domain": "official.example.com",
            "search_query": "AI Agent 使用趋势",
        },
    ]

    kept, stats = _quality_filter_records(records, "web", "AI Agent 使用趋势")

    assert [item["title"] for item in kept] == ["AI Agent trend report"]
    assert stats["raw_count"] == 2
    assert stats["quality_kept_count"] == 1
    assert stats["quality_dropped_count"] == 1
    assert stats["avg_relevance_score"] > 0


def test_assess_retrieval_quality_marks_empty_evidence_low():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["web_retrieval_stats"] = {"raw_count": 3, "quality_kept_count": 0, "avg_relevance_score": 0.0}
    state["local_retrieval_stats"] = {"raw_count": 0, "quality_kept_count": 0}

    quality, reasons = _assess_retrieval_quality(state)

    assert quality == "low"
    assert "相关性过滤后无可用结果" in reasons


def test_analyze_forces_more_research_on_low_retrieval_quality():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["web_retrieval_stats"] = {"raw_count": 2, "quality_kept_count": 0, "avg_relevance_score": 0.0}
    state["local_retrieval_stats"] = {"raw_count": 0, "quality_kept_count": 0}
    agent = _JsonAgent(
        '{"analysis_summary":"证据不足","needs_more_research":false,'
        '"missing_gaps":[],"findings":[],"claim_map":[]}'
    )

    result = analyze_node(state, agent, "analyst")

    assert result["retrieval_quality"] == "low"
    assert result["needs_more_research"] is True
    assert any("检索质量不足" in item for item in result["missing_gaps"])


def test_route_reflects_on_low_quality_until_max_iterations():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["retrieval_quality"] = "low"
    state["iteration"] = 1

    assert should_continue_research(state) == "reflect"

    state["iteration"] = 2
    assert should_continue_research(state) == "write"


def test_reflect_filters_duplicate_supplementary_queries():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["search_plan"] = [
        {"section_id": "user_query", "query": "AI Agent 使用趋势", "source_preference": "hybrid"}
    ]
    state["web_search_trace"] = [{"query": "AI Agent 官方文档"}]
    agent = _JsonAgent(
        '{"reflection_summary":"补搜","supplementary_queries":['
        '{"section_id":"gap_1","query":"AI Agent 使用趋势","source_preference":"hybrid","reason":"duplicate"},'
        '{"section_id":"gap_2","query":"AI Agent 2026 enterprise adoption","source_preference":"web","reason":"new"}'
        ']}'
    )

    result = reflect_node(state, agent, "planner")

    assert result["iteration"] == 1
    assert [item["query"] for item in result["supplementary_queries"]] == ["AI Agent 2026 enterprise adoption"]
    assert result["reflection_stop_reason"] == ""


def test_reflect_sets_stop_reason_when_no_new_queries():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["search_plan"] = [
        {"section_id": "user_query", "query": "AI Agent 使用趋势", "source_preference": "hybrid"}
    ]
    agent = _JsonAgent(
        '{"reflection_summary":"补搜","supplementary_queries":['
        '{"section_id":"gap_1","query":"AI Agent 使用趋势","source_preference":"hybrid","reason":"duplicate"}'
        ']}'
    )

    result = reflect_node(state, agent, "planner")

    assert result["supplementary_queries"] == []
    assert result["reflection_stop_reason"] == "no_new_supplementary_queries"


def test_rerun_without_supplementary_queries_does_not_fallback_to_original_query():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["iteration"] = 1
    state["supplementary_queries"] = []

    assert _build_queries(state, "web") == []


def test_analyze_counts_no_new_evidence_round_after_reflection():
    state = create_initial_state("AI Agent 使用趋势", max_iterations=2, user_id="u", tenant_id="t")
    state["iteration"] = 1
    state["previous_evidence_count"] = 2
    state["evidence_pool"] = [{"source_id": "WEB-1"}, {"source_id": "WEB-2"}]
    state["web_retrieval_stats"] = {"raw_count": 2, "quality_kept_count": 2, "avg_relevance_score": 0.8}
    agent = _JsonAgent(
        '{"analysis_summary":"没有新增","needs_more_research":true,'
        '"missing_gaps":["缺少最新数据"],"findings":[],"claim_map":[]}'
    )

    result = analyze_node(state, agent, "analyst")

    assert result["no_new_evidence_rounds"] == 1
    routed_state = dict(state)
    routed_state.update(result)
    assert should_continue_research(routed_state) == "write"
