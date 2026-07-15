# Local Development And SSE Runbook

This runbook covers the Windows local loop for Cloud Agent development: start
or diagnose services, verify SSE, understand expected degradation, and run the
canonical regression set.

## Quick Start

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1
```

If backend or frontend is missing, run the one-command smoke. It starts missing
services, waits for readiness, verifies backend SSE and Vite proxy SSE, then
stops only the processes it started.

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1
```

Keep started services running after the smoke:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1 -KeepRunning
```

Use the lower-level smoke when services are already running:

```powershell
python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:5173
```

Browser-level smoke checks live in the frontend package:

```powershell
cd cloud_agent/front/cloud_agent
npm run smoke:browser
npm run smoke:browser:real-backend
```

`npm run smoke:browser` starts Vite and a lightweight mock SSE server. It is
the fastest check for browser rendering, `/api/chat` proxying, and SSE parsing.

`npm run smoke:browser:real-backend` starts Vite and the real FastAPI app, then
sets `CLOUD_AGENT_SMOKE_FAKE_GRAPH=true` for that child backend process. This
keeps the HTTP router, `chat_service`, SSE contract, and browser rendering real
while avoiding real LLM, Redis, Milvus, SemanticCache, MCP, and graph-tool
calls. Failure diagnostics are written under:

```text
cloud_agent/front/cloud_agent/playwright-report/
cloud_agent/front/cloud_agent/test-results/
cloud_agent/front/cloud_agent/playwright-report-real-backend/
cloud_agent/front/cloud_agent/test-results-real-backend/
```

## Service URLs

| Service | URL | Check |
|---|---|---|
| FastAPI backend | `http://127.0.0.1:5000` | `http://127.0.0.1:5000/readyz` |
| Vite frontend | `http://127.0.0.1:5173` | browser page loads |
| Frontend proxy | `http://127.0.0.1:5173/api/metrics` | returns metrics text |

The frontend uses Vite proxying, so browser calls to `/api/chat` go to the
backend. The smoke verifies this proxy path and the direct backend path.

## Manual Startup

Backend:

```powershell
cloud_agent/app/run_dev.bat
```

Frontend:

```powershell
cd cloud_agent/front/cloud_agent
npm run dev
```

Low-resource local mode should disable optional external dependencies:

```powershell
$env:CLOUD_AGENT_SEMANTIC_CACHE_ENABLED='false'
$env:CLOUD_AGENT_VECTOR_SEARCH_ENABLED='false'
$env:CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED='false'
$env:CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED='false'
$env:CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED='false'
```

Equivalent values in env-file form:

```text
CLOUD_AGENT_SEMANTIC_CACHE_ENABLED=false
CLOUD_AGENT_VECTOR_SEARCH_ENABLED=false
CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED=false
```

These settings are expected in local smoke runs. They force graceful degradation
（降级）
for SemanticCache, Milvus/vector search, and Neo4j knowledge graph access while
preserving core chat and SSE behavior.

## Diagnostics

Run:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1
```

Use strict mode for CI-like checks:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1 -Strict
```

The doctor checks:

- backend and frontend ports with `Get-NetTCPConnection`
- listener process names with `Get-Process`
- backend `/readyz`
- frontend home page
- frontend proxy `/api/metrics`
- relevant environment variables
- recent logs under `.codex-run`

For deployment-oriented preflight checks, use the cross-platform Python doctor:

```powershell
python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000
```

If your deployment settings live in the documented env file, load it directly:

```powershell
python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000
```

Machine-readable output for CI artifacts or release notes:

```powershell
python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000 --json
```

After changing the LLM API key/provider or real dependency endpoints, run the
read-only external dependency smoke. It calls the OpenAI-compatible
`/chat/completions` endpoint with a tiny probe, sends Redis `PING`, lists Milvus
collections when `CLOUD_AGENT_MILVUS_MODE=remote`, validates local Milvus Lite
packages/paths when `CLOUD_AGENT_MILVUS_MODE=lite`, runs MySQL `SELECT 1`,
validates MCP config, and fetches OIDC/JWKS metadata when production auth uses
remote keys. It does not write business data or archive LLM response bodies.

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env
```

For config-only LLM validation without calling the provider:

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --skip-llm-call
```

The script writes:

```text
.codex-run/external-readonly-smoke.json
```

For MCP billing flows, run the registry and read-only tool smoke. It checks the
Billing allowlist and calls `query_user_orders` / `query_user_instances`
without archiving order or instance details.

```powershell
python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json
```

The script writes:

```text
.codex-run/mcp-billing-smoke.json
```

After Redis and Milvus are enabled, run the memory E2E smoke. It writes only
synthetic smoke data, uses a deterministic fake extractor LLM, verifies Redis
short-term storage, background preference extraction, Milvus long-term storage,
and vector retrieval, then clears the synthetic short-term session and attempts
to delete the synthetic long-term rows.

```powershell
python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env
```

The script writes:

```text
.codex-run/memory-e2e-smoke.json
```

To validate the Docker Compose deployment path on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1
```

The compose smoke validates `ops/docker-compose.cloud-agent.yml`, starts the
stack with `docker compose up -d --build`, waits for `/readyz`, runs
`python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000 --json`,
and then cleans up. When no compose service was running before the smoke, it
shuts the stack down; when pre-existing compose services are detected, it
preserves them and removes only services started by the smoke. Use
`-KeepRunning` when you want to inspect the running containers after the smoke.

The image uses the complete Agent dependency set when memory/vector features
are enabled. `requirements-container.txt` pins the official CPU-only PyTorch
wheel so Linux builds do not resolve NVIDIA/CUDA runtime packages.

Compose smoke artifacts:

```text
.codex-run/compose-doctor.json
.codex-run/compose-cloud-agent.log
.codex-run/compose-all.log
.codex-run/compose-ps.log
```

The deployment doctor checks `DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY_FILE`,
`CLOUD_AGENT_CORS_ORIGINS`, production auth settings such as
`CLOUD_AGENT_AUTH_MODE` and `CLOUD_AGENT_AUTH_STRATEGY`, backend `/healthz`,
backend `/readyz`, `/api/metrics`, Redis, Milvus, and MCP registry config.
Core endpoint or security misconfiguration returns `failed`; unavailable
optional dependencies return `degraded` unless `--strict` is used.

For a local end-to-end OIDC/JWKS auth check, run the realistic IdP smoke. It
starts a local discovery/JWKS provider, issues synthetic RS256 tokens, verifies
valid token resolution, wrong kid/audience rejection, JWKS key rotation, and
stale-while-error behavior. It does not call your production IdP and does not
archive token bodies.

```powershell
python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json
```

The script writes:

```text
.codex-run/real-idp-smoke.json
```

The optional `cloud-agent-browser-smoke` GitHub Actions workflow also runs the
deployment doctor against a smoke-only fake backend. The workflow uploads
`deploy-doctor-artifacts`, including:

```text
.cloud-agent-doctor/doctor.json
.cloud-agent-doctor/backend.out.log
.cloud-agent-doctor/backend.err.log
```

Logs commonly used during local diagnosis:

```text
.codex-run/backend.log
.codex-run/frontend.log
.codex-run/local-smoke-backend.log
.codex-run/local-smoke-frontend.log
```

## Common Issues

### Port Conflict / 端口冲突

If `5000` or `5173` is already occupied by the wrong process, identify it:

```powershell
Get-NetTCPConnection -LocalPort 5000,5173 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

Then inspect the process:

```powershell
Get-Process -Id <PID>
```

For an isolated smoke without touching existing services, choose temporary
ports:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1 `
  -BackendUrl http://127.0.0.1:5100 `
  -FrontendUrl http://127.0.0.1:5174
```

### Backend Is Not Ready

Check:

```powershell
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1
```

If `/readyz` fails, inspect `.codex-run/backend.log` or start the backend with
the one-command smoke. In local mode, optional Redis/Milvus/SemanticCache/Neo4j
degradation is acceptable; `agent_graph` still needs to be ready.

### Frontend Proxy Fails

The direct backend smoke can pass while the browser path fails. Check the Vite
proxy through:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173/api/metrics
```

If this fails, restart `npm run dev` or run the local smoke with a temporary
frontend port.

### Expected Warnings

These warnings are currently non-blocking during local development:

- `RequestsDependencyWarning`: local Python packages have a requests dependency
  version mismatch. It does not affect the current smoke result.
- `Vite chunk size`: production build emits a large chunk warning. Build still
  passes; code splitting can be handled later as a frontend performance task.
- `CRLF`: `git diff --check` may report line-ending conversion warnings on
  Windows. Treat these differently from whitespace errors.
- `PytestCacheWarning`: local `.pytest_cache` may be unwritable in nested
  directories. It does not mean tests failed.

## Regression

Run the canonical regression before handing off:

```powershell
.\test_all.bat
```

Expected shape:

- Python pytest passes
- `npm run test:markdown` passes
- `npm run test:sse` passes
- `npm run build` passes

Then run hygiene checks:

```powershell
git diff --check
rg -n "sk-[A-Za-z0-9]{20,}" -S cloud_agent deep_research ops .github README.md test_all.bat test_all.sh
```

`git diff --check` may show CRLF warnings. The secret scan should not find real
keys; test files may contain forbidden-value assertions.

For release candidates, run the full gate order in:

```text
ops/release_checklist.md
```

When the backend is already running, the release gate aggregator runs the core
command sequence and writes a single summary artifact:

```powershell
python ops/release_gate.py --env-file ops/cloud_agent.env --backend-url http://127.0.0.1:5000 --strict
```

The script writes:

```text
.codex-run/release-gate.json
```

After the release gate and browser smoke have run, generate a release evidence
index. It records required artifact status, size, modified time, and SHA-256
hash without copying API keys, prompts, completions, order rows, or chat
transcripts.

```powershell
python ops/release_evidence.py --json
```

For a release candidate that includes the observability gate, run the Ubuntu or
CI acceptance first. Windows can use the equivalent cross-platform Python
acceptance command, then require its latest result in the evidence index:

```powershell
bash ops/ubuntu_ci_acceptance.sh
python ops/observability_acceptance.py --run-chat-smoke --grafana-user admin --grafana-password admin --json
python ops/release_evidence.py --require-observability --json
```

The index parses only the `PASS` / `FAIL` / `BLOCKED` step counts from the
latest `.acceptance/<timestamp>/summary.tsv`; it does not copy command details.
Earlier local acceptance attempts remain on disk for audit but are not mixed
into the release candidate index. A failed or blocked latest acceptance returns
a non-ready status in this strict mode and requires an explicit release
decision.

To require real LLM and Billing MCP metric samples after starting the complete
Compose backend and observability stack, use a synthetic read-only query. The
query text is held in memory only; the artifact records only status and sample
counts.

```powershell
python ops/observability_acceptance.py --run-chat-smoke --chat-smoke-text "帮我查一下我的订单记录" --require-llm-metric --require-tool-metric --grafana-user admin --grafana-password admin --json
```

For a browser-rendered Grafana dashboard screenshot after the acceptance passes,
provide local credentials through the shell rather than the script or source
files:

```powershell
$env:GRAFANA_USER = "admin"
$env:GRAFANA_PASSWORD = "admin"
Push-Location cloud_agent/front/cloud_agent
npm run smoke:grafana
Pop-Location
```

The smoke writes a dashboard screenshot under `.codex-run/grafana-ui/` and only
reports the dashboard UID and artifact path. It does not print credentials,
prompts, responses, or metrics payloads.

For an optional 24-hour observation window on a continuously running staging
or production-like host, first run the targeted Billing acceptance so the
expected LLM and MCP metric families already exist. The window is not a local
release blocker because a local computer may sleep or shut down. It samples
every five minutes and writes only check states, result counts, and firing-alert
counts.

```powershell
powershell -ExecutionPolicy Bypass -File ops/start_observability_window.ps1 -RequireLlmMetric -RequireToolMetric
Get-Content .codex-run/observability-window/latest.json
python ops/observability_window.py --status --output-dir <output_dir_from_latest_json>
```

At the end of the window, `summary.json` is `ready` only when every sample was
healthy and no alert fired. A `degraded` result preserves the aggregate failure
counts for investigation without persisting Prometheus labels or business data.

Only after that optional window completes on the stable host, bind it to the
release evidence gate:

```powershell
python ops/release_evidence.py --require-observability --require-observability-window --json
```

The scheduled `cloud-agent-browser-smoke` workflow also runs an
`observability-stack-smoke` job with a fake graph backend, Prometheus, Grafana,
the cross-platform acceptance, and the Grafana browser smoke. It is a
configuration and UI regression gate only; retain the targeted LLM/MCP runtime
acceptance for production-like evidence.

The script writes:

```text
.codex-run/release-evidence.json
.codex-run/release-evidence.md
```
