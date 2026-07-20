import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ops.agent_eval as agent_eval


def test_eval_dataset_runs_ready(tmp_path):
    dataset_path = ROOT / "ops" / "eval" / "golden_set.json"
    report = agent_eval.run_eval(dataset_path)

    assert report.status == "ready"
    assert report.summary["total"] == 6
    assert report.summary["passed"] == 6
    assert report.summary["failed"] == 0
    assert report.summary["average_score"] == 1.0
    assert report.summary["by_kind"]["route"]["passed"] == 4
    assert report.summary["by_kind"]["sse"]["passed"] == 2

    artifact = tmp_path / "agent-eval.json"
    agent_eval.write_artifact(artifact, report)
    rendered = json.loads(artifact.read_text(encoding="utf-8"))

    assert rendered["status"] == "ready"
    assert rendered["summary"]["passed"] == 6
    assert len(rendered["cases"]) == 6


def test_eval_rejects_unknown_case_kind(tmp_path):
    dataset_path = tmp_path / "bad_dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "version": 1,
                "cases": [
                    {
                        "id": "bad_case",
                        "kind": "unknown",
                        "query": "hello",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        agent_eval.run_eval(dataset_path)
    except ValueError as exc:
        assert "unknown case kind" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown case kind")
