# Supply Chain Security Gate

The `cloud-agent-supply-chain` workflow runs on pull requests, the main
branches, a weekly schedule, and manual dispatch. It writes JSON reports to a
private GitHub Actions artifact and never uploads environment files, secrets,
chat content, or runtime metrics.

## Blocking Checks

- Trivy configuration scan for high and critical deployment findings.
- `pip-audit` for `cloud_agent/agent/requirements.txt`.
- `pip-audit` for `deep_research/requirements.txt`.
- `npm audit --omit=dev --audit-level=high` for the frontend production tree.

The workflow uses the official npm registry for audit requests. This avoids
mirrors that support package installation but do not implement npm's security
advisory API.

## Deep Research Status

The 2026-07-14 baseline contained 26 vulnerable dependency packages. Foundation
libraries, the LangChain/LangGraph/MCP compatibility group, and the
FastAPI/Starlette/cryptography upgrade group have been remediated in isolated
Linux validation. `pip check`, HTTP routing, encryption, workflow compilation,
research tests, and `pip-audit` all passed with no known vulnerabilities.

`deep_research` is now part of the blocking audit set. Do not add vulnerability
ignore lists without a time-bounded security review.

Do not add vulnerability ignore lists without a time-bounded security review.
Do not run `npm audit fix --force` or broad Python upgrades as an automated
workflow step.
