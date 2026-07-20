#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


PASS = "pass"
FAIL = "fail"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_runtime_paths() -> None:
    repo_root = _repo_root()
    for relative in ("cloud_agent/app", "cloud_agent/agent"):
        path = repo_root / relative
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


_ensure_runtime_paths()

from langchain_core.messages import AIMessage

from agents.orchestrator import OrchestratorAgent
import service.chat_service as chat_service


DEFAULT_DATASET_PATH = _repo_root() / "ops" / "eval" / "golden_set.json"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    status: str
    score: float
    detail: str
    expected: dict[str, Any]
    actual: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "status": self.status,
            "score": self.score,
            "detail": self.detail,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class EvalReport:
    dataset_name: str
    dataset_version: int
    cases: list[CaseResult]

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.cases)
        passed = sum(1 for case in self.cases if case.status == PASS)
        failed = sum(1 for case in self.cases if case.status == FAIL)
        average_score = round(
            sum(case.score for case in self.cases) / total if total else 0.0,
            3,
        )
        by_kind: dict[str, dict[str, int]] = {}
        for case in self.cases:
            bucket = by_kind.setdefault(case.kind, {"passed": 0, "failed": 0})
            bucket["passed" if case.status == PASS else "failed"] += 1
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "average_score": average_score,
            "by_kind": by_kind,
        }

    @property
    def status(self) -> str:
        return FAIL if self.summary["failed"] else "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dataset": {
                "name": self.dataset_name,
                "version": self.dataset_version,
            },
            "summary": self.summary,
            "cases": [case.to_dict() for case in self.cases],
        }


class _FailingLLM:
    async def ainvoke(self, *_args, **_kwargs):
        raise AssertionError("eval route cases should stay on deterministic rules")


class _EvalMemoryUnavailable:
    available = False


class _EvalMemory:
    short_term = _EvalMemoryUnavailable()
    long_term = _EvalMemoryUnavailable()


class _EvalGraph:
    def __init__(self, events: Sequence[dict[str, Any]]):
        self._events = list(events)

    async def astream_events(self, _state, config=None, version=None):
        for event in self._events:
            yield event

    async def ainvoke(self, _state, config=None):
        raise AssertionError("eval SSE cases should use astream_events")


def _dataset_path(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    return DEFAULT_DATASET_PATH


def load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset root must be a JSON object")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset must contain a non-empty cases array")
    return payload


def _parse_event_logs(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith("[EventLog] "):
            continue
        try:
            payload = json.loads(line.removeprefix("[EventLog] "))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _parse_sse_payloads(chunks: Sequence[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            raw = line.removeprefix("data: ").strip()
            if not raw or raw == "[DONE]":
                continue
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def _materialize_graph_event(event_spec: dict[str, Any]) -> dict[str, Any]:
    def _materialize(value: Any) -> Any:
        if isinstance(value, dict):
            if "content" in value and set(value.keys()).issubset({"content"}):
                return AIMessage(content=str(value["content"]))
            return {key: _materialize(sub_value) for key, sub_value in value.items()}
        if isinstance(value, list):
            return [_materialize(item) for item in value]
        return value

    event = {key: _materialize(value) for key, value in event_spec.items()}
    return event


async def _run_route_case_async(case: dict[str, Any]) -> CaseResult:
    query = str(case["query"])
    expected = dict(case.get("expected", {}))

    agent = OrchestratorAgent()
    agent.llm = _FailingLLM()
    state = {
        "messages": [("user", query)],
        "user_id": "eval_user",
        "tenant_id": "eval_tenant",
        "session_id": f"session_{case['id']}",
        "memory_context": "",
        "next_agent": "",
        "metadata": {
            "request_id": f"req_{case['id']}",
            "tenant_id": "eval_tenant",
            "user_id_hash": f"hash_{case['id']}",
        },
    }

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await agent.route(state)

    stdout = buffer.getvalue()
    events = _parse_event_logs(stdout)
    actual = {
        "next_agent": result["next_agent"],
        "primary_intent": result["metadata"].get("primary_intent"),
        "secondary_intent": result["metadata"].get("secondary_intent"),
        "is_finops_workflow": result["metadata"].get("is_finops_workflow"),
        "event_types": [event.get("event_type") for event in events],
    }

    mismatches: list[str] = []
    for field in ("next_agent", "primary_intent", "secondary_intent", "is_finops_workflow"):
        if field in expected and expected[field] != actual.get(field):
            mismatches.append(
                f"{field}: expected {expected[field]!r}, got {actual.get(field)!r}"
            )
    if "event_types" in expected and expected["event_types"] != actual["event_types"]:
        mismatches.append(
            f"event_types: expected {expected['event_types']!r}, got {actual['event_types']!r}"
        )
    if "forbidden_substrings" in expected:
        for value in expected["forbidden_substrings"]:
            if value in stdout:
                mismatches.append(f"forbidden substring present in stdout: {value!r}")
    if "eval_user" in stdout:
        mismatches.append("plain eval_user leaked into stdout")

    status = PASS if not mismatches else FAIL
    score = 1.0 if status == PASS else 0.0
    detail = "ok" if not mismatches else "; ".join(mismatches)
    return CaseResult(
        case_id=str(case["id"]),
        kind="route",
        status=status,
        score=score,
        detail=detail,
        expected=expected,
        actual=actual,
    )


def _route_case(case: dict[str, Any]) -> CaseResult:
    return asyncio.run(_run_route_case_async(case))


async def _run_sse_case_async(case: dict[str, Any]) -> CaseResult:
    expected = dict(case.get("expected", {}))
    events = [
        _materialize_graph_event(event_spec)
        for event_spec in case.get("graph_events", [])
    ]
    if not events:
        raise ValueError(f"case {case['id']} has no graph_events")

    original_graph = chat_service.graph
    original_memory = chat_service.memory
    original_semantic_cache = chat_service.semantic_cache
    chat_service.graph = _EvalGraph(events)
    chat_service.memory = _EvalMemory()
    chat_service.semantic_cache = type("_EvalCache", (), {"available": False})()

    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            chunks: list[str] = []
            async for chunk in chat_service.stream_chat(
                str(case["query"]),
                "eval_user",
                f"session_{case['id']}",
                request_id=f"req_{case['id']}",
                request_tenant_id="eval_tenant",
            ):
                chunks.append(chunk)
    finally:
        chat_service.graph = original_graph
        chat_service.memory = original_memory
        chat_service.semantic_cache = original_semantic_cache

    payloads = _parse_sse_payloads(chunks)
    route_payload = next(
        (payload for payload in payloads if payload.get("event_type") == "route_decision"),
        {},
    )
    stream_start = next(
        (payload for payload in payloads if payload.get("event_type") == "stream_start"),
        {},
    )
    done_payload = next(
        (payload for payload in payloads if payload.get("event_type") == "done"),
        {},
    )
    final_payload = next(
        (payload for payload in payloads if payload.get("event_type") == "final"),
        {},
    )
    tool_payloads = [
        payload for payload in payloads if payload.get("event_type") in {"tool_call_start", "tool_call_end"}
    ]
    agent_steps = [
        payload.get("step")
        for payload in payloads
        if payload.get("event_type") == "agent_step" and payload.get("step")
    ]
    deltas = [
        payload.get("content")
        for payload in payloads
        if payload.get("event_type") == "message_delta" and isinstance(payload.get("content"), str)
    ]
    actual = {
        "event_types": [payload.get("event_type") for payload in payloads],
        "stream_mode": stream_start.get("stream_mode"),
        "route_to": route_payload.get("route_to"),
        "route_step": route_payload.get("step"),
        "tool_names": [payload.get("tool_name") for payload in tool_payloads if payload.get("tool_name")],
        "agent_steps": agent_steps,
        "deltas": deltas,
        "final": final_payload.get("final"),
        "request_id": done_payload.get("request_id"),
    }

    mismatches: list[str] = []
    for field in ("stream_mode", "route_to", "route_step", "final", "request_id"):
        if field in expected and expected[field] != actual.get(field):
            mismatches.append(
                f"{field}: expected {expected[field]!r}, got {actual.get(field)!r}"
            )
    if "event_types" in expected and expected["event_types"] != actual["event_types"]:
        mismatches.append(
            f"event_types: expected {expected['event_types']!r}, got {actual['event_types']!r}"
        )
    if "tool_name" in expected:
        if expected["tool_name"] not in actual["tool_names"]:
            mismatches.append(
                f"tool_name: expected {expected['tool_name']!r}, got {actual['tool_names']!r}"
            )
    if "agent_steps" in expected and expected["agent_steps"] != actual["agent_steps"]:
        mismatches.append(
            f"agent_steps: expected {expected['agent_steps']!r}, got {actual['agent_steps']!r}"
        )
    if "deltas" in expected and expected["deltas"] != actual["deltas"]:
        mismatches.append(
            f"deltas: expected {expected['deltas']!r}, got {actual['deltas']!r}"
        )

    status = PASS if not mismatches else FAIL
    score = 1.0 if status == PASS else 0.0
    detail = "ok" if not mismatches else "; ".join(mismatches)
    return CaseResult(
        case_id=str(case["id"]),
        kind="sse",
        status=status,
        score=score,
        detail=detail,
        expected=expected,
        actual=actual,
    )


def _sse_case(case: dict[str, Any]) -> CaseResult:
    return asyncio.run(_run_sse_case_async(case))


CASE_RUNNERS: dict[str, Callable[[dict[str, Any]], CaseResult]] = {
    "route": _route_case,
    "sse": _sse_case,
}


def run_eval(dataset_path: Path) -> EvalReport:
    dataset = load_dataset(dataset_path)
    dataset_name = str(dataset.get("name", dataset_path.stem))
    dataset_version = int(dataset.get("version", 1))
    cases: list[CaseResult] = []

    for case in dataset["cases"]:
        if not isinstance(case, dict):
            raise ValueError("each case must be a JSON object")
        kind = str(case.get("kind", "")).strip().lower()
        if kind not in CASE_RUNNERS:
            raise ValueError(f"unknown case kind: {kind!r}")
        if "id" not in case:
            raise ValueError("each case must include an id")
        if "query" not in case:
            raise ValueError(f"case {case['id']} must include a query")
        cases.append(CASE_RUNNERS[kind](case))

    return EvalReport(dataset_name=dataset_name, dataset_version=dataset_version, cases=cases)


def format_text(report: EvalReport, dataset_path: Path) -> str:
    lines = [
        f"[agent-eval] dataset={dataset_path}",
        f"[agent-eval] status={report.status}",
        (
            "[agent-eval] summary: "
            f"passed={report.summary['passed']} failed={report.summary['failed']} "
            f"total={report.summary['total']} average_score={report.summary['average_score']}"
        ),
    ]
    for case in report.cases:
        lines.append(f"[{case.status.upper()}] {case.case_id} - {case.detail}")
    return "\n".join(lines) + "\n"


def format_json(report: EvalReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, report: EvalReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_json(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Agent golden set evaluator")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=_repo_root() / ".codex-run" / "agent-eval.json",
        help="JSON artifact path",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Do not write the JSON artifact")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run_eval(args.dataset)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[agent-eval] failed: {exc}\n")
        return 2

    if not args.no_artifact:
        write_artifact(args.artifact, report)

    sys.stdout.write(format_json(report) if args.json else format_text(report, args.dataset))
    return 1 if report.status == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
