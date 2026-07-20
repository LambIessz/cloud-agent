# Cloud Agent — 企业级多智能体云平台客服系统

基于 LangGraph + FastAPI 构建的 Multi-Agent 智能客服系统，覆盖云平台 **产品咨询、订单查询、选型推荐、成本优化、推广营销、故障排查、能力边界兜底** 7 类业务场景。项目经历四阶段全链路工程化改造，从 Demo 演进至生产就绪。

[![CI](https://github.com/LambIessz/cloud-agent/actions/workflows/cloud-agent-regression.yml/badge.svg)](https://github.com/LambIessz/cloud-agent/actions)

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                     POST /api/chat (SSE)                    │
└─────────────────────────┬───────────────────────────────────┘
                          │
                  ┌───────▼───────┐
                  │  Orchestrator │  规则优先 + LLM 兜底两级路由
                  │  (路由中心)    │
                  └───┬───┬───┬──┘
        ┌─────────────┤   │   ├─────────────┐
        ▼             ▼   ▼   ▼             ▼
┌──────────┐  ┌──────────┐  ...  ┌──────────────┐
│ product  │  │ billing  │       │  fallback    │
│ _agent   │  │ _agent   │       │  _agent      │
└────┬─────┘  └────┬─────┘       └──────────────┘
     │              │
     │         is_finops_workflow?
     │              │ YES
     │         ┌────▼─────┐
     │         │ finops   │  State Handoff
     │         │ _agent   │
     │         └──────────┘
     │
     ▼
┌─────────────────────────────────────────────┐
│  共享基础设施                                  │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌───────┐ │
│  │ Redis  │ │ Milvus │ │  MCP   │ │ Neo4j │ │
│  │ 短时记忆│ │ 语义缓存│ │ 工具调用│ │知识图谱│ │
│  └────────┘ │ 长期记忆│ └────────┘ └───────┘ │
│             └────────┘                        │
└─────────────────────────────────────────────┘
```

---

## 核心能力

### 多 Agent 编排与路由

- **7 个领域 Agent**：产品(product) · 订单(billing) · 推荐(recommendation) · 成本优化(finops) · 推广(promotion) · 故障排查(support) · 能力兜底(fallback)
- **两级路由策略**：确定性规则优先（关键词匹配 + 复合意图仲裁 + 非云平台拦截），LLM 仅处理规则无法判定的边缘 Case
- **跨 Agent 状态交接**：BillingAgent 查询实例数据后，根据 `is_finops_workflow` 标记动态决定是否交付 FinOpsAgent 继续分析，实现"查资源 → 分析 → 降本建议"自动化串联

### MCP 工具管理体系

- **应用级单例 ToolRegistry**：lazy 初始化，避免每次请求重复创建 stdio 子进程
- **按 Agent 工具白名单**：Billing → `query_user_orders/query_user_instances`，FinOps → `query_user_instances/analyze_instance_usage`
- **timeout + 可配置重试**：区分可重试错误（TimeoutError/ConnectionError）与参数错误（ValueError）
- **ToolAudit 审计**：记录 `request_id`、`tool_name`、`latency_ms`、`status`、`error_type`、`retry` 次数

### 双层记忆系统

- **短时记忆**：Redis 按用户/会话存储最近对话，超阈值自动裁剪，TTL 自动过期
- **长期记忆**：会话中每 N 轮自动触发 LLM 偏好提取 → Milvus 向量存储；新会话首次查询语义检索注入上下文
- **优雅降级**：Redis/Milvus 不可用时自动跳过，不影响核心对话能力

### 可观测性

- **40+ Prometheus 指标族**：request 吞吐/延迟/错误、route 分布/fallback 率、semantic cache 命中率、LLM Token/成本估算(USD)、MCP tool 调用量/延迟、degradation 事件、registry 状态
- **p95/p99 分位数**：request/LLM/tool 三组 duration_ms histogram
- **敏感字段过滤**：`request_id`、`user_id`、`user_id_hash`、`tenant_id`、`session_id`、`query`、`prompt`、`completion` 等 13 类字段自动从 metrics 剔除
- **Grafana 8 面板** + **7 条 Prometheus 告警规则**
- **全链路 request_id**：API 入口 → AgentState.metadata → 各节点日志 → SSE 尾帧，单次请求可溯源

### 安全与稳定性

- **身份认证**：支持 JWT（HS256）+ OIDC Discovery + JWKS key rotation + stale-while-error 降级
- **身份隔离**：生产模式忽略请求体 `user_id`，metadata 只存 `user_id_hash`，不落盘明文
- **FinOps 事实校验**：程序化校验 Agent 回答中的实例 ID/监控指标/节省金额是否来自工具实际返回，杜绝幻觉
- **全组件优雅降级**：Redis/Milvus/SemanticCache/Neo4j 任一不可用 → Degradation 审计 → 系统不中断

### 工程化交付

- **Docker Compose** 四容器一键部署（app + Redis + MySQL + Neo4j）
- **GitHub Actions CI**：push/PR 自动触发 20 文件 156 项回归测试
- **语义缓存预热**：`preload_cache.py` 离线预灌种子 QA，命中时绕过 LLM 推理，响应时延降至 50ms 以内

---

## 快速开始

### 环境要求

- Python >= 3.10
- Redis（可选，不可用时自动降级）
- MySQL（可选，MCP 工具依赖）
- Neo4j（可选，知识图谱依赖）

### 本地开发

```bash
# 1. 安装依赖
cd cloud_agent/agent
pip install -r requirements.txt

# 2. 配置环境变量（复制示例文件填入真实 key）
cp ops/cloud_agent.env.example ops/cloud_agent.env
# 编辑 ops/cloud_agent.env，填入 DEEPSEEK_API_KEY

# 3. 启动后端
# Windows:
cloud_agent/app/run_dev.bat
# Linux / macOS:
bash cloud_agent/app/run_dev.sh

# 4. 验证
curl http://127.0.0.1:5000/healthz
curl -X POST http://127.0.0.1:5000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Id: dev_user" \
  -d '{"query":"今天天气怎么样？","session_id":"test_1"}'
```

> 低资源环境可用 `requirements-minimal.txt`（~100MB，不含 torch/sentence-transformers/milvus/pyarrow），后端降级模式正常运行。

### Docker 部署

```bash
# 1. 准备环境变量文件
cp ops/cloud_agent.env.example ops/cloud_agent.env
# 编辑填入真实 key（DeepSeek API、MySQL 密码等）

# 2. 一键启动
docker compose --env-file ops/cloud_agent.env -f ops/docker-compose.cloud-agent.yml up -d

# 3. 验证
curl http://127.0.0.1:5000/healthz
```

Windows compose deployment smoke:

```powershell
powershell -ExecutionPolicy Bypass -File ops/cloud_agent_compose_smoke.ps1
```

The smoke script validates compose config, starts the stack, waits for `/readyz`,
runs the deployment doctor, writes `.codex-run/compose-doctor.json`, and collects
compose logs. It removes the full stack when it started every service itself;
when compose services were already running, it preserves them and removes only
services started by this smoke run.

The Docker runtime pins the official CPU-only PyTorch wheel. It keeps vector
features available without resolving CUDA runtime packages in a CPU deployment.

### 可观测性栈（可选）

```bash
# Prometheus + Grafana
docker compose -f ops/docker-compose.observability.yml up -d

# Ubuntu / CI acceptance (writes .acceptance/<timestamp>/summary.tsv)
bash ops/ubuntu_ci_acceptance.sh

# Windows or cross-platform runtime acceptance (same summary.tsv contract)
python ops/observability_acceptance.py --run-chat-smoke --grafana-user admin --grafana-password admin --json

# Require the latest observability result in the release evidence index
python ops/release_evidence.py --require-observability --json

# Optional later: require a completed 24-hour window only on a continuously
# running staging or production-like host. It is not a local release blocker
# and is not part of the current handoff.
python ops/release_evidence.py --require-observability --json
# Add `--require-observability-window` later only after that window has actually completed.

# Grafana: http://127.0.0.1:3000 (admin/admin)
# Prometheus: http://127.0.0.1:9090
```

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 流式对话（SSE），响应尾帧返回 `request_id` |
| `GET` | `/api/metrics` | Prometheus 指标（text/plain） |
| `GET` | `/healthz` | 存活探针（不触发依赖访问） |
| `GET` | `/readyz` | 就绪探针（仅检查 Agent graph 初始化状态） |

### 请求示例

```bash
curl -X POST http://127.0.0.1:5000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Id: dev_user" \
  -H "X-Tenant-Id: default_tenant" \
  -d '{"query":"查询我的订单记录","session_id":"sess_001"}'
```

### SSE 响应格式

```
data: {"content": "正在为您查询..."}
data: {"content": "您共有 3 条订单记录..."}
data: {"done": true, "request_id": "req_f60d98761c80440b"}
```

---

## 运行测试

```bash
# Windows 一键回归
test_all.bat

# Linux
bash test_all.sh

# 聚焦认证测试（28 项）
python -m pytest cloud_agent/agent/test/test_auth_router.py -v

# 运行 OIDC/JWKS 认证 smoke（本地真实发现文档 + JWKS + token 校验）
python ops/auth/real_idp_smoke.py --env-file ops/cloud_agent.env --json

# 发布前聚合门禁：doctor -> SSE -> external -> MCP billing -> memory -> IdP -> hygiene
python ops/release_gate.py --env-file ops/cloud_agent.env --backend-url http://127.0.0.1:5000 --strict

# 发布证据索引：汇总 release gate、smoke、browser diagnostics 和报告指纹
python ops/release_evidence.py --json

# Supply-chain gate: GitHub Actions workflow cloud-agent-supply-chain
# blocks core Python, frontend high/critical, and deployment config findings.

# 运行 Chat SSE smoke（需要后端已启动）
python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000

# 同时验证前端 Vite /api 代理（需要前后端都已启动）
python ops/chat_sse_smoke.py --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:5173

# Windows 一键本地 SSE 联调（自动启动缺失的后端/前端）
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1

# 联调后保留脚本启动的服务
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_smoke.ps1 -KeepRunning

# Windows 本地服务诊断（只检查，不启动服务）
powershell -ExecutionPolicy Bypass -File ops/chat_sse_local_doctor.ps1

# 部署前预检：检查密钥、CORS、healthz、readyz、metrics、Redis、Milvus、MCP 配置
python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000
python ops/cloud_agent_doctor.py --env-file ops/cloud_agent.env --base-url http://127.0.0.1:5000
python ops/cloud_agent_doctor.py --base-url http://127.0.0.1:5000 --json

# 换 API / 外部依赖后，只做只读连通 smoke，不写业务数据
python ops/external_dependency_readonly_smoke.py --env-file ops/cloud_agent.env

# 验证 MCP registry 与 Billing 只读工具调用，不输出订单/实例明细
python ops/mcp_billing_readonly_smoke.py --env-file ops/cloud_agent.env --json
# artifact: .codex-run/mcp-billing-smoke.json

# 验证 Redis 短记忆 -> Milvus 长记忆 -> 向量检索链路，只写入合成 smoke 数据
python ops/memory_e2e_smoke.py --env-file ops/cloud_agent.env

# Browser smoke: Vite + mock SSE server
cd cloud_agent/front/cloud_agent
npm run smoke:browser

# Browser smoke: Vite + real FastAPI app + smoke-only fake graph
npm run smoke:browser:real-backend
```

仓库不再保留独立的 runbook/checklist 文档；本地开发、发布门禁和证据索引都直接用下方命令和脚本。`ops/cloud_agent_doctor.py`、`ops/release_gate.py` 和 `ops/release_evidence.py` 仍然是主入口，证据默认写入 `.codex-run/release-evidence.json` 和 `.codex-run/release-evidence.md`。

**最近一次 canonical regression：Python 308 项通过，前端组件测试 13 项通过，Vue type-check 与 Vite production build 通过。**

| 测试文件 | 覆盖内容 |
|---|---|
| `test_auth_router.py` | JWT/OIDC/JWKS/key-rotation/stale-while-error（28 项） |
| `test_orchestrator_routing.py` | 7 类 Agent 路由正确性 |
| `test_metrics.py` | 40+ 指标族计数/直方图/PromQL 格式 |
| `test_event_log.py` | EventLog 字段约束 + 敏感字段不泄漏 |
| `test_semantic_cache.py` | 语义缓存命中/未命中/降级 |
| `test_tool_audit.py` | MCP 工具审计、timeout/retry |
| `test_degradation_audit.py` | Redis/Milvus/Cache 降级审计 |
| `test_identity_context.py` | 身份隔离、user_id_hash、local/production 模式 |
| `test_observability_ops.py` | Prometheus alert rules、Grafana dashboard 静态校验 |
| `test_tracing.py` | OpenTelemetry span 属性/request_id 开关 |
| 其余 10 文件 | health、secrets、container、CI、requirements、memory、MCP registry、FinOps validator、deep_research |

---

## 配置参考

核心环境变量（完整列表见 `ops/cloud_agent.env.example`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥（必填） |
| `MODEL` | `deepseek-chat` | LLM 模型名 |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接（可选） |
| `CLOUD_AGENT_AUTH_MODE` | `local` | `production` 启用认证 |
| `CLOUD_AGENT_AUTH_STRATEGY` | `gateway` | `jwt` / `oidc` / `jwks` |
| `CLOUD_AGENT_AUTH_JWKS_CACHE_SECONDS` | `300` | JWKS 缓存 TTL |
| `CLOUD_AGENT_AUTH_JWKS_STALE_WHILE_ERROR` | `false` | 远端故障时使用缓存 JWKS |
| `CLOUD_AGENT_TOOL_TIMEOUT_SECONDS` | `30` | MCP 工具超时 |
| `CLOUD_AGENT_LLM_PRICING_CONFIG` | `ops/prometheus/llm_pricing.example.yml` | LLM 计费配置 |

---

## 项目结构

```
企业级ai应用/
├── cloud_agent/
│   ├── agent/
│   │   ├── agents/           # 7 个业务 Agent + Orchestrator 路由
│   │   ├── core/
│   │   │   ├── workflow/     # EventLog, metrics, degradation, tool_audit, tracing, finops_validator, identity_context, request_context
│   │   │   ├── mcp/          # MCPToolRegistry + MCPManager
│   │   │   ├── memory/       # MemoryManager (short-term Redis + long-term Milvus)
│   │   │   └── graph/        # Neo4j 知识图谱（ingestor + client + models）
│   │   ├── config/           # settings, secrets, mcp_servers.json
│   │   ├── tools/            # vector_tool (Milvus RAG) + graph_tool (Neo4j)
│   │   ├── mcp_servers/      # cloud_platform_server (MySQL MCP stdio)
│   │   └── test/             # 20 个测试文件
│   ├── app/
│   │   ├── router/           # chat / health / metrics 路由
│   │   ├── security/         # auth.py (JWT/OIDC/JWKS)
│   │   ├── service/          # chat_service.py (stream_chat + init/shutdown)
│   │   ├── infra/            # cache.py (SemanticCache)
│   │   └── app_main.py       # FastAPI 入口
│   ├── Dockerfile
│   └── requirements-container.txt
├── deep_research/            # deep_research 子系统
├── ops/
│   ├── prometheus/           # Prometheus 配置 + alert rules + pricing
│   ├── grafana/              # Grafana dashboard JSON + provisioning
│   ├── otel/                 # OpenTelemetry smoke (console + OTLP gRPC)
│   ├── auth/                 # 真实 IdP smoke (real_idp_smoke.py)
│   ├── docker-compose.cloud-agent.yml
│   └── docker-compose.observability.yml
├── .github/workflows/        # GitHub Actions CI
├── test_all.bat / test_all.sh
└── README.md
```

---

## 技术栈

| 分类 | 技术 |
|------|------|
| **AI 框架** | LangGraph, LangChain, MCP (langchain-mcp-adapters) |
| **LLM** | DeepSeek (deepseek-chat), DashScope |
| **Web** | FastAPI (SSE 流式, lifespan, uvicorn) |
| **向量存储** | Milvus Lite, BAAI/bge-small-zh-v1.5 (HuggingFace) |
| **关系型 DB** | MySQL (PyMySQL), PostgreSQL + pgvector + SQLAlchemy async |
| **缓存** | Redis (短时记忆 + Celery 任务队列) |
| **图数据库** | Neo4j (产品知识图谱) |
| **监控** | Prometheus (自研 metrics helper), Grafana, OpenTelemetry |
| **认证** | PyJWT, HS256/RS256, OIDC Discovery, JWKS key rotation |
| **容器化** | Docker, Docker Compose |
| **CI/CD** | GitHub Actions |
| **测试** | pytest (156 canonical + 28 auth + smoke) |
| **前端** | Vue 3 + TypeScript + Vite |
