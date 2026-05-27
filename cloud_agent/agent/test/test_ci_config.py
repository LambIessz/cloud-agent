from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cloud-agent-regression.yml"

SENSITIVE_TERMS = {
    "user_id",
    "user_id_hash",
    "tenant_id",
    "session_id",
    "thread_id",
    "conversation_id",
    "query",
    "prompt",
    "completion",
    "matched_question",
}


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _github_on(workflow: dict):
    return workflow.get("on", workflow.get(True))


def test_github_actions_workflow_runs_canonical_regression_without_real_secrets():
    assert WORKFLOW_PATH.exists()
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _load_workflow()

    triggers = _github_on(workflow)
    assert {"push", "pull_request", "workflow_dispatch"}.issubset(triggers)
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["pytest"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 45

    env = job["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert env["CLOUD_AGENT_LLM_PRICING_CONFIG"].endswith(
        "/ops/prometheus/llm_pricing.example.yml"
    )
    assert env["DEEPSEEK_API_KEY"] == "ci-placeholder"
    assert "secrets." not in workflow_text
    assert "sk-" not in workflow_text
    assert "4AMDiDiWei" not in workflow_text

    steps = job["steps"]
    setup_python_steps = [
        step for step in steps if step.get("uses") == "actions/setup-python@v5"
    ]
    assert len(setup_python_steps) == 1
    assert setup_python_steps[0]["with"]["python-version"] == "3.12"

    run_commands = "\n".join(
        step.get("run", "")
        for step in steps
        if isinstance(step.get("run"), str)
    )
    assert "python -m pip install -r cloud_agent/requirements-container.txt" in run_commands
    assert "bash test_all.sh" in run_commands

    lowered = workflow_text.lower()
    leaked_terms = {
        term
        for term in SENSITIVE_TERMS
        if f"{term}=" in lowered or f"{term}:" in lowered
    }
    assert leaked_terms == set()
