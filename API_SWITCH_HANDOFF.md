# API Switch Handoff

## Current Status (2026-07-17)

Local p0-p5 work in this workspace is complete and verified.

- Cloud Agent Python regression subset: `183 passed`
- DeepResearch app tests: `13 passed`
- Frontend tests and build: passed
- SSE contract now includes `route_decision`, `tool_call_start`, `tool_call_end`, and `final`
- Request limits, auth boundary checks, metrics auth, and secret scanning are wired in
- Long-term memory now falls back to deterministic local embeddings when the HuggingFace model cannot load, so the memory smoke stays usable offline
- Release gate and release evidence are generated and ready.

What still remains for a full production handoff:

- Optional long-run observation after deployment on a continuously running host
- Any extra environment-specific acceptance the target platform requires

Recommended next step:

1. Keep the release checklist aligned with the evidence.
2. Capture extra environment-specific acceptance only if the target platform requires it.
3. Run the optional long-run observation on a non-sleeping host when needed.

## Latest Release Evidence

- Release gate: `.codex-run/release-gate.json` (`ready`, `8/8 passed`)
- Release evidence: `.codex-run/release-evidence.json` and `.codex-run/release-evidence.md` (`ready`, `7/7 required passed`)
- Browser diagnostics: `cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json`
- Browser report: `cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html`

> The progress note below is historical local-progress context, not the final production handoff status.

> 用途：换 API、换模型或新会话没有历史上下文时，把这份文档交给新的 AI / Codex，让它按当前项目状态继续推进。
>
> 当前日期：2026-07-13
> 项目路径：`C:\Users\LambIessz\Desktop\企业级ai应用`

## 给新会话的启动提示

请从这个项目继续：

```text
C:\Users\LambIessz\Desktop\企业级ai应用
```

当前目标是把这个企业级 AI 应用从 demo 级推进到更接近生产可验收状态。请先阅读本文件，再检查 git diff 和关键测试，不要假设历史对话仍然存在。

优先原则：

- 不要泄漏或打印真实 API key、JWT secret、数据库密码。
- 不要回滚用户或前一个 AI 已经做过的改动。
- 每次改动后跑相关测试；大改后跑 `.\test_all.bat`。
- 当前本地发布证据、Compose smoke 与真实 Billing LLM/MCP 指标已闭环；下一步是在目标生产环境复跑同一受控验收，并补充 Grafana UI 截图或长期趋势观察。

## 当前进度

P0-P5 的本地实现、自动化门禁、观测、Compose 与 Billing 真实指标证据已完成；目标生产环境的同类验收和长期运行观察仍待做。

已经完成的重点能力：

- 后端 `/api/chat` 已支持真 SSE 流式响应。
- 前端已有 mock SSE browser smoke 和真实 FastAPI backend browser smoke。
- `/api/chat` 响应头包含 `X-Request-Id`，便于排障。
- 新增部署预检脚本 `ops/cloud_agent_doctor.py`：
  - 检查 `DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY_FILE`。
  - 检查 CORS、生产 auth 配置、`/healthz`、`/readyz`、`/api/metrics`。
  - 检查 Redis、Milvus、MCP registry。
  - 支持 `--env-file ops/cloud_agent.env`。
  - 支持 JSON 输出和 strict mode。
  - 不输出真实密钥。
- 新增 GitHub Actions browser smoke workflow：
  - mock browser smoke。
  - real backend browser smoke。
  - deploy doctor artifact 上传。
- Docker Compose 配置已补齐 `CLOUD_AGENT_AUTH_MODE=local` 默认值。
- Container runtime pins `torch==2.13.0+cpu` from the official PyTorch CPU
  index so a CPU deployment does not download CUDA runtime packages through
  `sentence-transformers`.
- 新增外部依赖只读 smoke：
  - `ops/external_dependency_readonly_smoke.py`
  - 换 API/key/provider 后验证 OpenAI-compatible `/chat/completions`。
  - Redis 只发 `PING`。
  - Milvus 默认按本地 Milvus Lite 验证；`CLOUD_AGENT_MILVUS_MODE=remote` 时才列远程 collections。
  - MySQL/MCP 只跑 `SELECT 1` 和配置校验。
  - OIDC/JWKS 只抓 metadata/key set。
  - 默认写 `.codex-run/external-readonly-smoke.json`，不归档 LLM 响应正文或 secret。
- 新增长记忆端到端 smoke：
  - `ops/memory_e2e_smoke.py`
  - 只写入合成 smoke 数据。
  - 使用确定性的 fake extractor LLM，不调用真实模型。
  - 验证 Redis 短记忆、后台偏好提取、Milvus 长记忆写入和向量检索。
  - 默认写 `.codex-run/memory-e2e-smoke.json`。
- 新增 Windows Compose 部署烟测：
  - `ops/cloud_agent_compose_smoke.ps1`
  - 校验 compose config。
  - `docker compose up -d --build`。
  - 等待 `/readyz`。
  - 运行 deployment doctor。
  - 采集 `.codex-run/compose-doctor.json`、compose 日志和 ps 输出。
  - 默认自动清理；若发现预先运行的 Compose 服务，则保留它们并只删除本次 smoke 新建服务，支持 `-KeepRunning`。
  - Compose 参数不再使用 PowerShell 保留变量 `$Args`，所有子命令可真实执行。
  - `docker compose config --quiet` 避免把已展开的环境变量写入控制台或日志。
- 加固 Ubuntu 观测验收：
  - `ops/ubuntu_ci_acceptance.sh`
  - Prometheus `up{job="cloud_agent"}` 必须至少有 1 条结果，避免 API success 但无样本被误判成功。
  - 业务指标记录样本数，方便早期环境分阶段验收。
- 新增发布前 checklist / 生产部署 runbook：
  - `ops/release_checklist.md`
  - 固化 API / SSE / browser / external dependency / memory / MCP / Compose / observability / regression 门禁顺序。
  - 明确 release evidence artifact 和 rollback checklist。
- 加固真实 IdP / JWT / JWKS smoke：
  - `ops/auth/real_idp_smoke.py`
  - 支持 `--env-file ops/cloud_agent.env`、`--json` 和 `.codex-run/real-idp-smoke.json`。
  - 使用本地真实 OIDC discovery/JWKS provider 和合成 RS256 token，验证 valid token、wrong kid/audience、key rotation、stale-while-error、malformed/missing auth。
  - 输出不归档 token body、真实用户/租户标识或生产 IdP secret。
- 新增 release gate 聚合脚本：
  - `ops/release_gate.py`
  - 按顺序运行 doctor、Chat SSE、external dependency smoke、MCP billing read-only smoke、memory E2E smoke、IdP smoke、`git diff --check` 和 secret scan。
  - 默认写 `.codex-run/release-gate.json`，并引用各子 smoke artifact。
  - 支持 `--strict`、`--dry-run`、`--skip-*` 和 `--skip-llm-call`。
- 加固 Milvus Lite 本地门禁：
  - `ops/cloud_agent_doctor.py` 在 `CLOUD_AGENT_MILVUS_MODE=lite` 时校验 Lite 包可用，不误探测 `127.0.0.1:19530`。
  - `ops/memory_e2e_smoke.py` 在 Lite 模式默认使用 `.codex-run` 下的隔离 Milvus 文件，避免和运行中的 backend 争用文件锁。
  - `cloud_agent/agent/core/memory/long_term.py` 支持 `CLOUD_AGENT_LONG_TERM_MEMORY_URI` 覆盖。
- 新增 MCP billing read-only smoke：
  - `ops/mcp_billing_readonly_smoke.py`
  - 初始化 MCP registry，并确认 Billing allowlist 包含 `query_user_orders` / `query_user_instances`。
  - 对 MySQL-backed billing 工具做只读调用，只报告状态/行数，不归档订单或实例明细。
  - 默认写 `.codex-run/mcp-billing-smoke.json`，并已纳入 `ops/release_gate.py`。
- 新增 release evidence 索引：
  - `ops/release_evidence.py`
  - 汇总 release gate、external dependency、MCP billing、memory E2E、IdP、real-backend browser diagnostics 和 HTML report。
  - 默认写 `.codex-run/release-evidence.json` 和 `.codex-run/release-evidence.md`。
  - 只记录 status、summary、size、mtime、SHA-256，不复制 key、prompt、completion、订单行、实例明细或对话正文。
  - 可用 `--require-observability` 将最新 `.acceptance/<timestamp>/summary.tsv` 作为必需门禁，只解析 PASS / FAIL / BLOCKED 计数；FAIL 或 BLOCKED 会返回 non-ready。
  - `--require-observability-window` 只在连续运行的 24 小时观测窗口完成后再启用。
- 新增跨平台 observability runtime acceptance：
  - `ops/observability_acceptance.py`
  - 无 Bash/WSL 的 Windows 环境可检查 FastAPI metrics、Prometheus target/query、Grafana health/dashboard，并写入同样的 `.acceptance/<timestamp>/summary.tsv`。
  - 支持受控 chat text，以及 `--require-llm-metric` / `--require-tool-metric`，可验证真实模型与 MCP tool 样本但不归档响应正文。
- 新增 LangChain per-run LLM metrics callback：
  - `cloud_agent/agent/core/workflow/llm_metrics.py`
  - Billing、Product、Promotion、Recommendation、FinOps 的 ReAct Agent 均接入回调；每次实际模型回调记录低基数调用量、延迟、token usage/估算成本或错误类型。

## 最新验证结果

最近一次完整回归：

```powershell
.\test_all.bat
```

结果：

```text
317 passed, 3 warnings
```

同时通过：

- Python 主回归。
- 前端 markdown / SSE / scenarios / chat-stream / sessions / chat-controller 测试。
- Vitest 组件测试。
- Vue type-check。
- Vite production build。

已做敏感信息扫描：未发现真实密钥；只有 `ci-placeholder` 这类占位值。

真实长记忆端到端 smoke 已通过：

```powershell
python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env --json
```

结果：`status=ready`，7 项通过，0 degraded / 0 blocked / 0 failed；artifact 写入 `.codex-run/memory-e2e-smoke.json`。

真实 IdP / OIDC / JWKS smoke 已通过：

```powershell
python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json
```

结果：`status=ready`，11 项通过，0 failed；artifact 写入 `.codex-run/real-idp-smoke.json`。

MCP billing read-only smoke 已通过：

```powershell
python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json
```

结果：`status=ready`，4 项通过，0 degraded / 0 blocked / 0 failed；artifact 写入 `.codex-run/mcp-billing-smoke.json`。

完整 release gate 已在本地真实 backend 上通过：

```powershell
python ops/release_gate.py --env-file ops/cloud_agent.env --backend-url http://127.0.0.1:5000 --strict --json
```

结果：`status=ready`，8 个步骤通过，0 failed / 0 skipped；artifact 写入 `.codex-run/release-gate.json`。

真实前端 + 真实 FastAPI backend browser smoke 已通过：

```powershell
cd cloud_agent/front/cloud_agent
npm run smoke:browser:real-backend
```

结果：Chromium 1 个场景通过；SSE 响应为 `200 text/event-stream`，`/readyz` 为 `ready`，前端 console/page/request failure 为空；诊断写入 `cloud_agent/front/cloud_agent/test-results-real-backend/real-backend-diagnostics.json`，HTML 报告写入 `cloud_agent/front/cloud_agent/playwright-report-real-backend/index.html`。

真实 Compose deployment smoke 已通过：

```powershell
powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1
```

结果：CPU-only 完整镜像构建成功，未解析 NVIDIA/CUDA 包；`/readyz` 通过；deployment doctor 为 `status=ready`（9 passed / 0 degraded / 0 failed）。既有 `cloud_agent_mysql` 保持 healthy；本次新建的 `cloud_agent` 与 Redis 已被选择性清理。artifact 写入 `.codex-run/compose-doctor.json` 及三份 compose log。

跨平台 observability runtime acceptance 已通过：

```powershell
python ops/observability_acceptance.py --run-chat-smoke --grafana-user admin --grafana-password admin --json
```

结果：13 个步骤全部 `PASS`。Prometheus `up{job="cloud_agent"}` 和请求指标各返回 1 条样本；合成 SSE 请求后 `/api/metrics` 指标族从 0 增至 15；Grafana health 与 `Cloud Agent Overview` dashboard API 均通过。产物为 `.acceptance/20260713T072236Z/summary.tsv`，未归档聊天或 metrics 正文。

完整 Compose Billing LLM/MCP metrics acceptance 已通过：

```powershell
python ops/observability_acceptance.py --run-chat-smoke --chat-smoke-text "帮我查一下我的订单记录" --require-llm-metric --require-tool-metric --grafana-user admin --grafana-password admin --json
```

Grafana UI browser evidence is also available after the metrics acceptance:

```powershell
$env:GRAFANA_USER = "admin"
$env:GRAFANA_PASSWORD = "admin"
Push-Location cloud_agent/front/cloud_agent
npm run smoke:grafana
Pop-Location
```

`ops/grafana_ui_smoke.mjs` verifies the dashboard and LLM/MCP panel titles, then
writes a non-content screenshot under `.codex-run/grafana-ui/`. Credentials are
read only from the invoking environment.

24-hour observability windows are now supported without storing chat or metric
payloads:

```powershell
powershell -ExecutionPolicy Bypass -File ops/start_observability_window.ps1 -RequireLlmMetric -RequireToolMetric
Get-Content .codex-run/observability-window/latest.json
python ops/observability_window.py --status --output-dir <output_dir_from_latest_json>
```

The monitor writes per-sample check states and a final aggregate summary only.
`ready` means no failed samples or firing alerts; `degraded` identifies the
failed check names and counts without retaining labels or business content.

After the monitor completes on a continuously running host, you can extend the
release evidence to include the long-running window:

```powershell
python ops/release_evidence.py --require-observability --json
```

For the current handoff, the required release evidence is the targeted
acceptance only; the 24-hour target-environment window stays optional until you
choose to run it on a non-sleeping machine.

结果：13 个步骤全部 `PASS`；合成请求后 metric family 从 5 增至 32，Prometheus LLM metric 返回 2 条、MCP tool 返回 1 条，LLM cost 与 MCP registry 也各有 1 条结果。Billing 查询仅针对 mock MySQL；artifact 为 `.acceptance/20260713T085732Z/summary.tsv`，不含模型回复或订单/实例明细。

严格 release evidence 索引已生成：

```powershell
python ops/release_evidence.py --require-observability --json
```

结果：`status=ready`，8 个必需 artifact 全部通过，0 missing / 0 failed / 0 blocked；4 个 Compose optional artifact 也已存在。索引写入 `.codex-run/release-evidence.json` 和 `.codex-run/release-evidence.md`。

已知非阻塞 warning：

- `requests` 依赖版本 warning。
- `langchain-community` deprecated warning。
- `deep_research/.pytest_cache` 写入权限 warning。
- npm minor version update notice。

## 换 API 时先改哪里

优先使用环境文件：

```text
ops/cloud_agent.env
```

参考模板：

```text
ops/cloud_agent.env.example
```

常见变量：

```env
MODEL=<你的模型名>
CLOUD_AGENT_AUTH_MODE=local
```

- `DEEPSEEK_API_KEY`：通过环境变量或受控的 `DEEPSEEK_API_KEY_FILE` 注入，不在文档、命令历史或仓库文件中填写具体值。

如果不想把 key 明文写进 env 文件，建议使用 secret file：

```env
DEEPSEEK_API_KEY_FILE=C:\path\to\deepseek_api_key.txt
MODEL=<你的模型名>
CLOUD_AGENT_AUTH_MODE=local
```

注意：

- 不要把真实 key 提交到 git。
- 如果新 API 不是 DeepSeek 兼容接口，需要先检查 LLM client / settings 是否支持 base URL、provider 或 SDK 切换。
- 如果目前代码只支持 `DEEPSEEK_API_KEY`，不要直接硬编码新供应商 key；应先加配置层或兼容命名。

## 换 API 后的验证顺序

1. 启动后端或 Compose 前，先做静态配置检查。

```powershell
python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000
```

2. 本地后端已启动后，再检查健康状态。

```powershell
curl http://127.0.0.1:5000/healthz
curl http://127.0.0.1:5000/readyz
curl http://127.0.0.1:5000/api/metrics
```

3. 验证 Chat SSE。

```powershell
python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000
```

4. 验证新 API 和真实外部依赖的只读连通。

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env
```

如果只想校验 LLM 配置，不真实调用供应商：

```powershell
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env --skip-llm-call
```

5. MySQL/MCP 已配置后，验证 Billing registry 和只读工具调用。

```powershell
python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json
```

结果写入 `.codex-run/mcp-billing-smoke.json`，不要把订单或实例明细复制进文档。

6. Redis/Milvus 已启用后，验证长记忆端到端链路。

```powershell
python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env
```

7. 如果要验证 Docker Compose。

```powershell
powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1
```

8. 大改后跑主回归。

```powershell
.\test_all.bat
```

## 关键文件索引

部署与验收：

- `ops/cloud_agent_doctor.py`
- `ops/external_dependency_readonly_smoke.py`
- `ops/mcp_billing_readonly_smoke.py`
- `ops/memory_e2e_smoke.py`
- `ops/auth/real_idp_smoke.py`
- `ops/release_gate.py`
- `ops/release_evidence.py`
- `ops/cloud_agent_compose_smoke.ps1`
- `ops/chat_sse_smoke.py`
- `ops/chat_sse_local_smoke.ps1`
- `ops/chat_sse_local_doctor.ps1`
- `ops/ubuntu_ci_acceptance.sh`
- `.github/workflows/cloud-agent-browser-smoke.yml`
- `.github/workflows/cloud-agent-supply-chain.yml`
- `ops/supply_chain_security.md`

配置：

- `ops/cloud_agent.env.example`
- `ops/docker-compose.cloud-agent.yml`
- `ops/docker-compose.observability.yml`
- `cloud_agent/Dockerfile`
- `cloud_agent/requirements-container.txt`

后端入口：

- `cloud_agent/app/app_main.py`
- `cloud_agent/app/router/chat.py`
- `cloud_agent/app/router/health.py`
- `cloud_agent/app/router/metrics.py`
- `cloud_agent/app/security/auth.py`
- `cloud_agent/app/service/chat_service.py`

核心能力：

- `cloud_agent/agent/core/workflow/metrics.py`
- `cloud_agent/agent/core/workflow/event_log.py`
- `cloud_agent/agent/core/workflow/degradation_audit.py`
- `cloud_agent/agent/core/workflow/tool_audit.py`
- `cloud_agent/agent/core/workflow/identity_context.py`
- `cloud_agent/agent/core/workflow/tracing.py`
- `cloud_agent/agent/core/mcp/`
- `cloud_agent/agent/core/memory/`

前端：

- `cloud_agent/front/cloud_agent/`
- `cloud_agent/front/cloud_agent/src/`
- `cloud_agent/front/cloud_agent/src/smoke/real-backend-smoke.spec.ts`

测试守护：

- `cloud_agent/agent/test/test_deploy_doctor.py`
- `cloud_agent/agent/test/test_compose_deploy_smoke_script.py`
- `cloud_agent/agent/test/test_container_config.py`
- `cloud_agent/agent/test/test_observability_ops.py`
- `cloud_agent/agent/test/test_ci_config.py`
- `cloud_agent/agent/test/test_local_dev_runbook.py`
- `cloud_agent/agent/test/test_metrics.py`
- `test_all.bat`
- `test_all.sh`

文档：

- `README.md`
- `ops/README.md`
- `ops/local_dev_runbook.md`
- `ops/release_checklist.md`
- `ops/observability_checklist.md`

## 建议的下一步计划

### P0：换 API 后先恢复最小可用

- 更新 `ops/cloud_agent.env` 或 secret file。
- 启动后端。
- 跑 `cloud_agent_doctor.py`。
- 跑 `chat_sse_smoke.py`。
- 确认 `/api/chat` 能返回 SSE，并且前端能正常消费。

### P1：真实 API read-only smoke

已新增一个只读脚本：

```text
ops/external_dependency_readonly_smoke.py
```

建议检查：

- 新 API key 是否可用。
- 模型名是否存在或可调用。
- API 超时和错误信息是否被清洗。
- Redis 连通。
- Milvus 连通。
- MySQL/MCP 连通。
- Auth 配置是否自洽。

要求：

- 不写业务数据。
- 不打印真实 key。
- 输出 JSON artifact 到 `.codex-run/`。

### P2：把 read-only smoke 接入测试和文档

- 新增静态守护测试。
- 更新 `README.md` / `ops/local_dev_runbook.md`。
- 视情况接入 CI 的手动 workflow。

### P2.5：长记忆端到端 smoke

已新增：

```text
ops/memory_e2e_smoke.py
```

建议检查：

- Redis 短记忆是否能保存并读取当前会话。
- 后台偏好提取是否能产生合成偏好。
- Milvus 长记忆是否能写入并按同一用户检索。
- smoke 输出是否不包含真实密钥或业务对话内容。

### P3：真实 IdP / JWT / JWKS smoke

已完成衔接：

```powershell
python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json
```

结果写入：

```text
.codex-run/real-idp-smoke.json
```

覆盖：

- 本地真实 OIDC discovery + JWKS。
- 合成 RS256 token 校验。
- wrong kid / wrong audience / malformed / missing auth 的 401 边界。
- JWKS key rotation。
- stale-while-error 开启/关闭行为。

### P4：生产部署 runbook

已新增：

```text
ops/release_checklist.md
```

覆盖：

- 换 key / provider 后的验证顺序。
- doctor / SSE / browser / external smoke / memory smoke / Compose / observability / regression 门禁。
- release evidence artifact 清单。
- rollback checklist。
- secret 与真实业务数据不落文档/日志的边界。

### P5：发布前 checklist

已形成 `ops/release_checklist.md`，Go / No-Go 条件包含：

- doctor 通过。
- SSE smoke 通过。
- external dependency smoke 通过。
- memory E2E smoke 在启用记忆时通过。
- Compose smoke 在 Compose 部署路径中通过。
- browser smoke 通过。
- observability acceptance 通过或明确 blocked 原因。
- `.\test_all.bat` 通过。
- 无真实密钥或业务数据进入 git diff、日志、artifact 或 release note。

## 给新 AI 的注意事项

- 当前 git worktree 可能是 dirty 的，这是正常的；不要随便 reset。
- 新增或修改文件请优先用小步补丁。
- 修改后先跑针对性测试，再跑主回归。
- 如果要联网查 OpenAI / API 文档，优先查官方文档。
- 如果遇到真实 key、真实公司接口、真实账号，不要写进代码、文档或日志。
- 如果新 API 与 DeepSeek 不兼容，先做配置兼容层，不要在业务逻辑里到处散落 provider 判断。
