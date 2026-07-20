# Cloud Agent Release Checklist

Use this checklist for a release candidate, provider/API switch, or production
handoff. Mark each item as `PASS`, `BLOCKED`, or `FAIL`, and keep the generated
artifacts with the release notes. Do not paste real API keys, JWT secrets,
database passwords, access tokens, user identifiers, prompts, completions, order
data, or chat transcripts into this file or into issue comments.

When the backend is already running, use the aggregator for the core automated
gates:

```powershell
python ops/release_gate.py --env-file ops/cloud_agent.env --backend-url http://127.0.0.1:5000 --strict
```

Expected aggregate artifact:

```text
.codex-run/release-gate.json
```

## 0. Release Candidate Scope

- Confirm the repository path and branch under test.
- Record the commit SHA or local diff bundle being released.
- Run `git status --short` and account for every dirty file.
- Confirm `ops/cloud_agent.env` is local-only and ignored by git.
- Prefer secret-file variables for deploy credentials when the target platform
  supports them.
- Confirm the rollback target: previous image tag, previous env file version,
  and previous model/provider setting.

## 1. Configuration Gate

Validate deploy configuration before starting or changing long-running
services:

```powershell
docker compose --env-file ops/cloud_agent.env -f ops/docker-compose.cloud-agent.yml config
```

Check the env contract:

- `CLOUD_AGENT_AUTH_MODE` and `CLOUD_AGENT_AUTH_STRATEGY` match the target
  environment.
- `CLOUD_AGENT_CORS_ORIGINS` is not wildcard in production.
- `REDIS_URL`, MySQL, MCP, and Milvus mode are intentional for this release.
- `CLOUD_AGENT_MCP_PRELOAD` is intentional for the environment.
- Semantic cache and vector search flags are intentional, not accidental
  leftovers from low-resource local mode.

## 2. Start Or Update The Backend

For local Windows validation:

```powershell
cloud_agent/app/run_dev.bat
```

For the compose deployment path:

```powershell
docker compose --env-file ops/cloud_agent.env -f ops/docker-compose.cloud-agent.yml up -d --build
```

Expected basic endpoints:

```powershell
curl http://127.0.0.1:5000/healthz
curl http://127.0.0.1:5000/readyz
curl http://127.0.0.1:5000/api/metrics
```

If `CLOUD_AGENT_METRICS_TOKEN` is set, include `Authorization: Bearer <token>`
or `X-Metrics-Token: <token>` when checking `/api/metrics`.

## 3. Deployment Doctor Gate

Run the deployment doctor against the running backend:

```powershell
python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000 --json
```

Expected result:

- `failed=0`.
- `degraded=0` for a full production candidate.
- `degraded>0` only when the release note explicitly names the optional
  dependency and the accepted impact.
- JSON output is kept as release evidence.

## 4. SSE And Browser Gates

Verify the backend SSE contract:

```powershell
python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000
```

Verify the browser path:

```powershell
cd cloud_agent/front/cloud_agent
npm run smoke:browser
npm run smoke:browser:real-backend
```

Expected result:

- SSE stream emits content and a final done frame.
- Response headers include request tracing information.
- Browser smoke does not require real business data.
- Failure artifacts under Playwright report directories are retained.

## 5. External Dependency Gate

After changing API keys, model names, provider base URLs, Redis, Milvus, MySQL,
MCP, or production auth settings, run the read-only smoke:

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --json
```

For config-only LLM validation without a provider call:

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --skip-llm-call --json
```

Expected artifact:

```text
.codex-run/external-readonly-smoke.json
```

Expected result:

- LLM provider accepts the configured OpenAI-compatible chat completion shape.
- Redis responds to `PING`.
- Milvus Lite or remote Milvus mode is explicit and reachable.
- MySQL responds to `SELECT 1` when MCP billing tools are enabled.
- MCP config parses and points at existing working directories.
- Production OIDC/JWKS config can fetch metadata or keys when applicable.

## 6. Memory E2E Gate

When Redis and Milvus long-term memory are enabled, verify the memory path with
synthetic data only:

```powershell
python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env --json
```

Expected artifact:

```text
.codex-run/memory-e2e-smoke.json
```

Expected result:

- Redis short-term memory saves and reloads the synthetic session.
- Background preference extraction uses the deterministic fake extractor LLM.
- Milvus long-term memory stores and retrieves the synthetic marker.
- Synthetic Redis rows are cleared and synthetic Milvus rows are deleted.

## 7. Auth IdP Gate

For production JWT/OIDC/JWKS release candidates, validate the auth boundary
with a local realistic IdP:

```powershell
python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json
```

Expected artifact:

```text
.codex-run/real-idp-smoke.json
```

Expected result:

- Local OIDC discovery and JWKS endpoints are exercised.
- Synthetic RS256 token resolves through `resolve_authenticated_identity`.
- Unknown `kid`, wrong `aud`, malformed token, and missing authorization return
  sanitized 401 responses.
- JWKS key rotation accepts both keys during transition and rejects the removed
  key after cache expiry.
- Stale-while-error behavior is validated for both enabled and disabled modes.
- No token body, real user identifier, real tenant identifier, or production
  IdP secret is archived.

This smoke validates the application auth boundary. Real remote IdP metadata or
JWKS reachability is covered by `ops/external_dependency_readonly_smoke.py`.

## 8. MCP Billing Tool Gate

If billing or FinOps flows are in release scope, validate the MCP registry and a
read-only billing tool call in the target environment. At minimum confirm:

```powershell
python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json
```

Expected artifact:

```text
.codex-run/mcp-billing-smoke.json
```

- Registry initialization succeeds.
- Billing tool list includes `query_user_orders` and `query_user_instances`.
- Read-only tool calls do not print real customer identifiers or order details
  into release notes.
- Tool errors are sanitized and include only error class/status information.

## 9. Compose Gate

Run the Windows compose smoke when validating the compose deployment path:

```powershell
powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1
```

Expected artifacts:

```text
.codex-run/compose-doctor.json
.codex-run/compose-cloud-agent.log
.codex-run/compose-all.log
.codex-run/compose-ps.log
```

Use `-KeepRunning` only when a human needs to inspect containers after a
failure.

## 10. Observability Gate

For local or VM observability validation:

```powershell
docker compose -f ops/docker-compose.observability.yml up -d
```

For Ubuntu or CI-style acceptance:

```bash
bash ops/ubuntu_ci_acceptance.sh
```

For Windows or another environment without Bash, use the cross-platform
equivalent after starting the backend, Prometheus, and Grafana:

```powershell
python ops/observability_acceptance.py --run-chat-smoke --grafana-user admin --grafana-password admin --json
```

Bind the latest acceptance summary into the release evidence index:

```powershell
python ops/release_evidence.py --require-observability --json
```

Strict evidence mode parses only the latest acceptance's PASS / FAIL / BLOCKED
counts. It returns a non-ready status when that acceptance has failed or
blocked steps; record the approved blocked reason before a human Go decision.

When the release requires actual LLM and Billing MCP dashboard samples, run a
synthetic read-only Billing query and require both metric families:

```powershell
python ops/observability_acceptance.py --run-chat-smoke --chat-smoke-text "帮我查一下我的订单记录" --require-llm-metric --require-tool-metric --grafana-user admin --grafana-password admin --json
```

For optional browser evidence after the API-level Grafana check has passed:

```powershell
$env:GRAFANA_USER = "admin"
$env:GRAFANA_PASSWORD = "admin"
Push-Location cloud_agent/front/cloud_agent
npm run smoke:grafana
Pop-Location
```

The command verifies the `Cloud Agent Overview`, `LLM calls`, and `MCP tool
calls` panels, then stores a non-content screenshot in `.codex-run/grafana-ui/`.

Optional, for a continuously running staging or production-like host only:

```powershell
powershell -ExecutionPolicy Bypass -File ops/start_observability_window.ps1 -RequireLlmMetric -RequireToolMetric
Get-Content .codex-run/observability-window/latest.json
python ops/observability_window.py --status --output-dir <output_dir_from_latest_json>
```

Review the final `summary.json`: `ready` requires every scheduled sample to be
healthy with zero firing alerts; `degraded` keeps safe aggregate failure counts.

Do not use this as a local release blocker. For the current handoff, only the
targeted acceptance is required in the release evidence. After the optional
24-hour window has completed on the stable host, add it too:

```powershell
python ops/release_evidence.py --require-observability --json
```

The scheduled CI `observability-stack-smoke` validates the same Compose
topology with a fake graph backend and does not replace this real LLM/MCP or
target-environment evidence.

The acceptance artifact records only check status and Prometheus result counts,
not the model completion or Billing tool output.

Manual evidence checklist:

```text
ops/observability_checklist.md
```

Expected result:

- `/api/metrics` is reachable and does not expose sensitive labels.
- Prometheus `up{job="cloud_agent"}` has at least one result.
- Alert rules load successfully.
- Grafana datasource and dashboard load successfully.
- Trace smoke is `PASS` or explicitly `BLOCKED` with an environment reason.

## 11. Regression And Hygiene Gate

Run the canonical regression before handoff:

```powershell
.\test_all.bat
```

Run hygiene checks:

```powershell
git diff --check
python ops/secret_scan.py
```

Expected result:

- Python regression passes.
- Frontend markdown, SSE, scenario, chat-stream, session, controller,
  component tests pass.
- Vue type-check and Vite production build pass.
- Secret scan finds no real keys. Test-only fake values are acceptable when
  they are clearly artificial.
- CRLF warnings on Windows are noted separately from whitespace errors.
- The `cloud-agent-supply-chain` workflow passes its blocking core dependency,
  npm high/critical, and deployment configuration audits.

## 12. Release Evidence Bundle

Generate the release evidence index after the gate and browser smoke have run:

```powershell
python ops/release_evidence.py --json
```

Use `--require-observability` when the observability gate is part of the
release candidate, as shown in section 10.
Use `--require-observability-window` only after the optional long-running
window has actually completed on a continuously running host.

The index writes:

- `.codex-run/release-evidence.json`
- `.codex-run/release-evidence.md`

Attach or retain these artifacts for the release candidate:

- `.codex-run/release-evidence.json`
- `.codex-run/release-evidence.md`
- `.codex-run/external-readonly-smoke.json`
- `.codex-run/mcp-billing-smoke.json`
- `.codex-run/memory-e2e-smoke.json`
- `.codex-run/real-idp-smoke.json`
- `.codex-run/release-gate.json`
- `.codex-run/compose-doctor.json` when compose was validated
- `.codex-run/compose-cloud-agent.log` when compose was validated
- `.codex-run/compose-all.log` when compose was validated
- `.codex-run/compose-ps.log` when compose was validated
- `.acceptance/<timestamp>/summary.tsv` when observability acceptance ran
- `cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json` when real-backend browser smoke ran
- `cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html` when retaining the browser smoke report
- Playwright failure artifacts only when browser smoke failed

## 13. Rollback Checklist

Before release approval, confirm:

- Previous deploy artifact or container image can be restored.
- Previous env file or secret version can be restored.
- Previous LLM model/provider setting is known.
- Database schema or vector collection changes are either backwards compatible
  or have a documented rollback path.
- Alerts and dashboards will still work after rollback.

## 14. Go / No-Go Decision

Release is `GO` only when:

- Deployment doctor is `ready`.
- SSE smoke passes.
- External dependency smoke is `ready`.
- Memory E2E smoke is `ready` when memory is enabled.
- Auth IdP smoke is `ready` for production JWT/OIDC/JWKS release candidates.
- Compose smoke passes when compose is the deployment path.
- Browser smoke passes for the target UI path.
- Observability gate is `PASS` or has an accepted `BLOCKED` reason.
- `.\test_all.bat` passes.
- No real secrets or business data appear in git diff, logs, artifacts, or
  release notes.
