import importlib.util
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "ops" / "release_gate.py"


def _load_release_gate():
    spec = importlib.util.spec_from_file_location("release_gate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env_file(tmp_path: Path, content: str = "DEEPSEEK_API_KEY=release-gate-test-key\n") -> Path:
    path = tmp_path / "cloud_agent.env"
    path.write_text(content, encoding="utf-8")
    return path


def test_release_gate_runs_expected_steps_in_order(tmp_path):
    gate = _load_release_gate()
    env_file = _env_file(tmp_path)
    commands = []

    def fake_runner(command):
        commands.append(tuple(command))
        command_text = " ".join(command)
        if "chat_sse_smoke.py" in command_text:
            return gate.CommandOutput(exit_code=0, stdout='{"status":"ok"}\n')
        if command[0] == "git":
            return gate.CommandOutput(exit_code=0)
        if command[0] == "rg":
            return gate.CommandOutput(exit_code=1)
        return gate.CommandOutput(
            exit_code=0,
            stdout=json.dumps({"status": "ready", "summary": {"passed": 1, "failed": 0}}),
        )

    report = gate.run_release_gate(
        env_file=env_file,
        backend_url="http://127.0.0.1:5000",
        runner=fake_runner,
        process_env={},
    )

    assert report.status == "ready"
    assert report.summary == {"passed": 8, "failed": 0, "skipped": 0}
    assert [step.name for step in report.steps] == [
        "deployment_doctor",
        "chat_sse",
        "external_dependencies",
        "mcp_billing_readonly",
        "memory_e2e",
        "auth_idp",
        "diff_check",
        "secret_scan",
    ]
    command_blob = "\n".join(" ".join(command) for command in commands)
    assert "ops/cloud_agent_doctor.py" in command_blob
    assert "ops/chat_sse_smoke.py" in command_blob
    assert "ops/external_dependency_readonly_smoke.py" in command_blob
    assert "ops/mcp_billing_readonly_smoke.py" in command_blob
    assert "ops/memory_e2e_smoke.py" in command_blob
    assert "ops/auth/real_idp_smoke.py" in command_blob
    assert "git diff --check" in command_blob
    assert "ops/secret_scan.py" in command_blob


def test_release_gate_redacts_failed_step_output(tmp_path):
    gate = _load_release_gate()
    secret = "release-gate-secret-value"
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"
    env_file = _env_file(tmp_path, f"DEEPSEEK_API_KEY={secret}\n")

    def fake_runner(command):
        if "cloud_agent_doctor.py" in " ".join(command):
            return gate.CommandOutput(
                exit_code=1,
                stderr=f"failed with {secret} and {jwt}",
            )
        if command[0] == "rg":
            return gate.CommandOutput(exit_code=1)
        return gate.CommandOutput(exit_code=0, stdout='{"status":"ready","summary":{"passed":1}}')

    report = gate.run_release_gate(env_file=env_file, runner=fake_runner, process_env={})
    rendered = gate.format_json(report) + gate.format_text(report)

    assert report.status == "failed"
    assert secret not in rendered
    assert jwt not in rendered
    assert "<redacted>" in rendered or "jwt-<redacted>" in rendered


def test_release_gate_dry_run_and_skips_are_reported(tmp_path):
    gate = _load_release_gate()
    report = gate.run_release_gate(
        env_file=_env_file(tmp_path),
        dry_run=True,
        skip_steps={"chat_sse"},
        process_env={},
    )

    assert report.status == "incomplete"
    assert report.summary == {"passed": 0, "failed": 0, "skipped": 8}
    assert all(step.status == gate.SKIPPED for step in report.steps)
    assert any(step.name == "chat_sse" and step.detail == "skipped by CLI flag" for step in report.steps)


def test_release_gate_writes_json_artifact(tmp_path):
    gate = _load_release_gate()
    artifact = tmp_path / "release-gate.json"
    report = gate.ReleaseGateReport(
        [
            gate.StepResult(
                name="deployment_doctor",
                status=gate.PASS,
                command=["python", "ops/cloud_agent_doctor.py"],
                exit_code=0,
                duration_ms=1,
                detail="status=ready",
            )
        ]
    )

    gate.write_artifact(artifact, report)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["steps"][0]["name"] == "deployment_doctor"
