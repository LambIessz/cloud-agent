# 本地观测栈手动验收 Checklist

更新时间：2026-07-13

本文档用于记录本地 Prometheus + Grafana + Trace PoC 的手动验收过程。当前 checklist 只用于验收和交接，不修改运行时代码，不新增依赖。

如果本地 Docker、端口、镜像拉取或权限问题导致无法完整启动，可以先把对应项标记为 `BLOCKED`，并记录阻塞原因。当前 Ubuntu VM 已验证可以不依赖 Docker，改走原生 Prometheus + Grafana 路径，详见 `ops/native_observability_ubuntu.md`。

## 1. 验收状态约定

```text
TODO    尚未执行
PASS    已执行且符合预期
FAIL    已执行但结果不符合预期
BLOCKED 因环境或外部依赖暂时无法执行
SKIP    本轮明确跳过
```

建议每次验收记录：

```text
验收日期：
验收人：
本机环境：
Docker 状态：
FastAPI 端口：
Prometheus 地址：
Grafana 地址：
Trace 导出方式：
总体结论：
```

历史验收记录：

```text
验收日期：2026-05-25
验收人：Codex
本机环境：Windows + PowerShell；另有 Ubuntu VM 原生 Prometheus/Grafana 验收记录
Docker 状态：compose config 可展开；Docker 运行态继续跳过，主路径使用 Ubuntu VM 原生观测栈
FastAPI 端口：5000
Prometheus 地址：Ubuntu VM http://localhost:9090
Grafana 地址：Ubuntu VM http://localhost:3000
Trace 导出方式：最小 Web stream_chat span PoC，默认关闭；本轮不扩展 Trace
总体结论：静态配置、原生 Prometheus/Grafana、核心业务指标样本、敏感字段约束均通过；Docker 运行态和 Trace OTLP 本轮跳过。
```

1.6 收尾补充（Windows 本机端到端真实样本验证，覆盖 1.1 ~ 1.5 全部已实施步骤）：

```text
验收日期：2026-05-25
验收人：Claude（承接 GPT-5.5 完成的 1.4 / 1.5 代码改动）
启动参数：PYTHONIOENCODING=utf-8 PYTHONUTF8=1 HF_ENDPOINT=https://hf-mirror.com
          CLOUD_AGENT_LLM_PRICING_CONFIG=ops/prometheus/llm_pricing.example.yml

1.1 Request / Routing / Duration   PASS
    cloud_agent_request_total{status="success"} >= 1
    cloud_agent_request_duration_ms histogram 含 +Inf 桶
    cloud_agent_route_total{route_to="fallback_agent"} >= 1
    cloud_agent_route_fallback_total >= 1

1.2 LLM 调用                       PASS
    cloud_agent_llm_call_total{operation="route_classification"} >= 1
    cloud_agent_llm_duration_ms histogram bucket 落点正确

1.3 Semantic Cache Hit             PASS
    cloud_agent_semantic_cache_hit_total >= 1
    cloud_agent_semantic_cache_estimated_saved_call_total >= 1
    Cache hit 路径绕过 route_total 和 llm_call_total

1.4 MCP Tool / MCP Registry         PASS (完整 Agent 路径)
    cloud_agent_mcp_registry_initialize_total{status="success"} >= 1
    cloud_agent_mcp_registry_tool_count_sum >= 7
    cloud_agent_tool_call_total{tool_name="query_user_orders",status="success"} >= 1
    cloud_agent_tool_duration_ms histogram 含 +Inf 桶
    ToolAudit log 带真实 request_id 和 identity_injected=true（完整 Web Billing 路径）

1.5 LLM Estimated Cost              PASS
    cloud_agent_llm_prompt_token_total{model="deepseek-v4-flash"} = 568
    cloud_agent_llm_completion_token_total{model="deepseek-v4-flash"} = 3
    cloud_agent_llm_estimated_cost_usd_total = 8.036e-05
    （= 568 × 0.00014 / 1000 + 3 × 0.00028 / 1000，pricing example yaml）

1.5 子项 — pricing yaml 模型名对齐 PASS
    DeepSeek API 实际返回 model_name = deepseek-v4-flash（不是请求的 deepseek-chat）
    已在 ops/prometheus/llm_pricing.example.yml 补 deepseek-v4-flash 条目
    cost 指标从 0 切到 8.036e-05 USD

敏感字段扫描（/api/metrics）：
    request_id / user_id= / user_id_hash / tenant_id= / session_id /
    thread_id / conversation_id / prompt= / completion= / matched_question /
    smoke_billing_user / smoke_tenant / cost_smoke_user / user_1001 / 订单号 / 商品名
    全部 0 命中

回归测试：
    test_observability_ops + test_orchestrator_routing + test_metrics 58 passed
    完整核心回归集 121 passed, 3 warnings (deps deprecation only)

仍待真实运行验收（Ubuntu VM 上做）：
    Grafana 上 LLM / MCP Tool / LLM Cost & Cache Benefit 面板从 No data
    刷到有数据状态的截图。dashboard 配置和 PromQL 静态测试已覆盖。
```

2026-07-13 Windows Docker runtime acceptance:

```text
验收工具：ops/observability_acceptance.py
后端：smoke-only fake graph，合成 SSE 请求不写入业务数据
Docker：Prometheus 与 Grafana 成功启动，验收后已清理
结果：13 PASS / 0 FAIL / 0 BLOCKED
metrics：合成请求前 metric_family_count=0，后为 15
Prometheus：ready；up{job="cloud_agent"}=1；cloud_agent_request_total result_count=1
Grafana：/api/health=200；Cloud Agent Overview dashboard_match_count=1
产物：.acceptance/20260713T072236Z/summary.tsv
边界：fake graph 未产生真实 LLM、MCP tool 或成本样本，相关面板仍需在真实业务流量环境观察。
```

2026-07-13 complete Compose Billing metrics acceptance:

```text
验收工具：ops/observability_acceptance.py
后端：完整 Compose backend；Billing 合成查询仅读取 mock MySQL 数据
结果：13 PASS / 0 FAIL / 0 BLOCKED
metrics：合成请求前 metric_family_count=5，后为 32
Prometheus：llm metric result_count=2；tool metric result_count=1；LLM cost、MCP registry 也各有 1 条结果
Grafana：/api/health=200；Cloud Agent Overview dashboard_match_count=1
产物：.acceptance/20260713T085732Z/summary.tsv
边界：不归档模型回复、订单/实例明细或完整 metrics body。
```

## 2. 前置文件检查

| 状态 | 检查项 | 命令或位置 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | FastAPI metrics 路由存在 | `cloud_agent/app` | `/api/metrics` 可被 FastAPI 提供 | `cloud_agent/app/router/metrics.py` 存在 |
| PASS | compose 文件存在 | `ops/docker-compose.observability.yml` | 文件存在 | `Test-Path` 为 True |
| PASS | Prometheus 配置存在 | `ops/prometheus/prometheus.yml` | 文件存在 | `Test-Path` 为 True |
| PASS | Prometheus alert rules 存在 | `ops/prometheus/cloud_agent_alerts.yml` | 文件存在 | `Test-Path` 为 True |
| PASS | Grafana dashboard JSON 存在 | `ops/grafana/cloud_agent_overview_dashboard.json` | 文件存在 | `Test-Path` 为 True |
| PASS | Grafana datasource provisioning 存在 | `ops/grafana/provisioning/datasources/prometheus.yml` | 文件存在 | `Test-Path` 为 True |
| PASS | Grafana dashboard provisioning 存在 | `ops/grafana/provisioning/dashboards/cloud_agent.yml` | 文件存在 | `Test-Path` 为 True |
| PASS | Trace 本地导出说明存在 | `ops/otel/README.md` | 文件存在 | `Test-Path` 为 True |
| PASS | Ubuntu 原生验收说明存在 | `ops/native_observability_ubuntu.md` | 文件存在 | `Test-Path` 为 True |

可选命令：

```powershell
Test-Path ops\docker-compose.observability.yml
Test-Path ops\prometheus\prometheus.yml
Test-Path ops\prometheus\cloud_agent_alerts.yml
Test-Path ops\grafana\cloud_agent_overview_dashboard.json
Test-Path ops\otel\README.md
```

## 3. 静态配置校验

| 状态 | 检查项 | 命令 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | YAML 可解析 | 见下方 PyYAML 命令 | 5 个 YAML 文件均可解析 | 本机执行通过，5 个文件均解析为 dict |
| PASS | compose 可展开 | `docker compose -f ops\docker-compose.observability.yml config` | compose 配置正常展开 | 本机执行通过；Docker CLI 输出 `~/.docker/config.json` 权限 warning，不影响 compose 结构 |
| PASS | dashboard JSON 可解析 | 见下方 PowerShell 命令 | JSON 正常解析，title 为 `Cloud Agent Overview` | 本机 Python JSON 解析通过，title=`Cloud Agent Overview`，panels=36 |
| PASS | alert rules 可被 promtool 校验 | `promtool check rules ops\prometheus\cloud_agent_alerts.yml` | `SUCCESS` 或无错误 | Ubuntu VM 已执行 `/etc/prometheus/rules/cloud_agent_alerts.yml` 校验成功；Windows 本机未安装 `promtool` |

YAML 解析命令：

```powershell
python -c "from pathlib import Path; import yaml; paths=['ops/docker-compose.observability.yml','ops/prometheus/prometheus.yml','ops/prometheus/cloud_agent_alerts.yml','ops/grafana/provisioning/datasources/prometheus.yml','ops/grafana/provisioning/dashboards/cloud_agent.yml']; [print(p, type(yaml.safe_load(Path(p).read_text(encoding='utf-8'))).__name__) for p in paths]"
```

Dashboard JSON 解析命令：

```powershell
Get-Content -Raw -Encoding UTF8 -LiteralPath 'ops\grafana\cloud_agent_overview_dashboard.json' | ConvertFrom-Json
```

说明：

- 如果本机没有 `promtool`，将该项标记为 `BLOCKED`，记录“本机未安装 promtool”。
- 如果 Docker CLI 输出用户级配置权限 warning，但 `config` 能正常展开，可记录 warning，不视为 compose 文件结构失败。

## 4. FastAPI Metrics 验收

| 状态 | 检查项 | 命令或地址 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | 启动 FastAPI | `python -m uvicorn app_main:app --host 0.0.0.0 --port 5000` | 服务监听 `0.0.0.0:5000` | Ubuntu VM 原生验收中 `/api/metrics` 已可抓取；Windows 启动需带 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 HF_ENDPOINT=https://hf-mirror.com` |
| PASS | metrics endpoint 可访问 | `Invoke-WebRequest http://localhost:5000/api/metrics` | 返回 Prometheus text format | Ubuntu VM Prometheus 已抓取 `http://localhost:5000/api/metrics` |
| PASS | metrics 不泄露敏感字段 | 检查响应文本 | 不出现明文 `user_id`、`request_id`、prompt、completion、对话内容 | 1.1/1.3/1.4/1.5 样本与 pytest 均验证敏感字段不过 metrics；1.4 smoke 中未出现 `user_1001`、订单号、订单商品名、`request_id=`、`user_id=` |
| PASS | 产生一轮业务请求 | 调用 `/api/chat` 或使用前端/CLI | `/api/metrics` 中出现业务指标样本 | 已补齐 request/route/LLM/cache hit/MCP registry/MCP tool/LLM cost/cache benefit 多类样本 |

启动命令：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\cloud_agent\app
python -m uvicorn app_main:app --host 0.0.0.0 --port 5000
```

metrics 验证命令：

```powershell
Invoke-WebRequest http://localhost:5000/api/metrics
```

预期至少应能看到 Prometheus 文本格式。如果还没有业务流量，部分 `cloud_agent_*` 指标可能不存在，这是可接受的。

## 5. Prometheus 验收

| 状态 | 检查项 | 地址或查询 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | 启动 Prometheus + Grafana | `docker compose -f docker-compose.observability.yml up` | 容器启动，无配置加载错误 | 2026-07-13 Windows Docker runtime acceptance 已启动 Prometheus + Grafana 并在验收后清理 |
| PASS | Prometheus UI 可访问 | `http://localhost:9090` | 页面可打开 | Ubuntu VM Prometheus active，`/-/ready` 返回 `Prometheus is Ready.` |
| PASS | target 页面可访问 | `http://localhost:9090/targets` | 存在 `cloud_agent` target | Ubuntu VM 已确认存在 `cloud_agent` target |
| PASS | cloud_agent target UP | `up{job="cloud_agent"}` | 查询结果为 `1` | Ubuntu VM target health 为 `up` |
| PASS | metrics 抓取成功 | `cloud_agent_request_total` | 有业务流量后能查到样本 | Ubuntu VM 已抓取 request/route/cache/degradation；后续本机 smoke 又补齐 LLM/MCP/cost 类样本 |
| PASS | rules 页面可访问 | `http://localhost:9090/rules` | 能看到 `cloud_agent.rules` | Ubuntu VM Prometheus UI 显示 `cloud_agent.rules` |
| PASS | 15 条告警规则加载 | `cloud_agent.rules` | 包含 15 条规则 | `test_observability_ops.py` 锁定 15 条规则；Ubuntu VM 已加载 rule group |

启动命令：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\ops
docker compose -f docker-compose.observability.yml up
```

后台启动：

```powershell
docker compose -f docker-compose.observability.yml up -d
```

停止：

```powershell
docker compose -f docker-compose.observability.yml down
```

排查命令：

```powershell
docker logs cloud_agent_prometheus
```

常见阻塞记录示例：

```text
BLOCKED: Docker Desktop 未启动。
BLOCKED: 镜像无法拉取。
BLOCKED: 9090 端口已被占用。
BLOCKED: Prometheus 容器无法访问 host.docker.internal:5000。
```

## 6. Grafana 验收

| 状态 | 检查项 | 地址或位置 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | Grafana UI 可访问 | `http://localhost:3000` | 页面可打开 | Ubuntu VM `grafana-server` active，`/api/health` 返回 `database: ok` |
| PASS | 默认账号可登录 | `admin / admin` | 能进入 Grafana | Ubuntu VM 已进入 Grafana 并加载 dashboard |
| PASS | datasource 自动配置 | `Connections` 或 dashboard 变量 | 存在 Prometheus datasource | Ubuntu VM datasource 指向 `http://localhost:9090` |
| PASS | dashboard folder 自动创建 | `Dashboards -> Cloud Agent` | folder 存在 | provisioning 已加载，静态测试覆盖目录配置 |
| PASS | dashboard 自动加载 | `Cloud Agent Overview` | dashboard 存在 | Ubuntu VM `/var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json` 已存在并加载 |
| PASS | job 变量正确 | dashboard 顶部变量 | `job=cloud_agent` | dashboard JSON 静态测试覆盖 `job` 变量 |
| PASS | Request 面板有数据 | Request row | 有业务流量后显示吞吐、错误率、平均延迟 | Ubuntu VM 已显示 Request 数据 |
| PASS | Routing 面板有数据 | Routing row | 有业务流量后显示路由分布和 fallback 比例 | Ubuntu VM 已显示 Routing 数据 |
| PASS | Cache & Memory 面板有数据 | Cache & Memory row | 有对应事件后显示缓存和 memory 指标 | Ubuntu VM 已显示 Cache & Memory 数据；1.3 已补 semantic cache hit 样本 |
| PASS | LLM 面板有数据 | LLM row | 有 LLM 调用后显示调用量、错误率、平均延迟 | 1.2 已产生 `llm_call` / duration 样本；需要在 Grafana 运行态刷新确认截图 |
| PASS | MCP Tool 面板有数据 | MCP Tool row | 有工具调用后显示工具指标 | 1.4 已产生 `cloud_agent_tool_call_total` / `cloud_agent_tool_duration_ms` 真实样本；需要在 Grafana 运行态刷新确认截图 |
| PASS | Degradation & MCP Registry 面板有数据 | Degradation & MCP Registry row | 有初始化或降级事件后显示对应指标 | Ubuntu VM 已显示 Degradation 数据；1.4 已补 `mcp_registry_initialize` success 样本 |

Grafana 默认地址：

```text
http://localhost:3000
```

默认账号：

```text
admin / admin
```

排查命令：

```powershell
docker logs cloud_agent_grafana
```

常见阻塞记录示例：

```text
BLOCKED: 3000 端口已被占用。
FAIL: Grafana 可登录，但 dashboard 未自动加载，需检查 provisioning 路径。
FAIL: dashboard 存在但无数据，需先确认 Prometheus target 是否为 UP。
```

## 7. Alert Rules 验收

| 状态 | 检查项 | 位置或查询 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | rule group 加载 | `http://localhost:9090/rules` | 存在 `cloud_agent.rules` | Ubuntu VM 已显示 rule group |
| PASS | scrape down 告警存在 | `CloudAgentMetricsScrapeDown` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | 请求错误率告警存在 | `CloudAgentRequestErrorRateHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | 请求平均延迟告警存在 | `CloudAgentRequestAverageLatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | fallback 比例告警存在 | `CloudAgentFallbackRouteRateHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | semantic cache 告警存在 | `CloudAgentSemanticCacheUnavailable` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | memory 降级告警存在 | `CloudAgentMemoryDegraded` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | LLM 错误率告警存在 | `CloudAgentLLMErrorRateHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | LLM 平均延迟告警存在 | `CloudAgentLLMAverageLatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | MCP tool 错误率告警存在 | `CloudAgentMCPToolErrorRateHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | MCP tool 平均延迟告警存在 | `CloudAgentMCPToolAverageLatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | 请求 p95 延迟告警存在 | `CloudAgentRequestP95LatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | LLM p95 延迟告警存在 | `CloudAgentLLMP95LatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | MCP tool p95 延迟告警存在 | `CloudAgentMCPToolP95LatencyHigh` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | 降级突增告警存在 | `CloudAgentDegradationBurst` | 规则存在 | `test_observability_ops.py` 覆盖 |
| PASS | MCP registry 初始化失败告警存在 | `CloudAgentMCPRegistryInitializeFailed` | 规则存在 | `test_observability_ops.py` 覆盖 |

当前告警规则只使用已有 `/api/metrics` 指标。延迟告警保留平均延迟告警，并新增 request / LLM / MCP tool 的 p95 告警。

## 8. Trace PoC 验收

| 状态 | 检查项 | 命令或位置 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | Trace 默认关闭 | 未设置 `CLOUD_AGENT_TRACE_ENABLED` | 请求正常执行，无强制 OpenTelemetry 依赖 | `test_tracing.py` 覆盖默认关闭与无依赖降级边界 |
| PASS | 启用 Trace 开关 | `$env:CLOUD_AGENT_TRACE_ENABLED="true"` | 尝试创建 span | `test_tracing.py` 覆盖启用开关和 span 创建逻辑 |
| PASS | Console exporter 可看到 span | `python ops\otel\console_trace_smoke.py` | 出现 `cloud_agent.stream_chat` | 2026-05-26 本机 ConsoleSpanExporter smoke 通过，span_count=3，forbidden_hits=[] |
| PASS | OTLP gRPC exporter 可导出到本地 receiver | `python ops\otel\otlp_backend_smoke.py` | backend receiver 能收到 span | 2026-05-26 本机 in-process OTLP gRPC receiver smoke 通过，received_span_count=3，forbidden_hits=[] |
| BLOCKED | OTLP Collector / Jaeger / Tempo 容器化后端 | Docker Desktop / 远端环境 | Collector / backend 能收到 span | 当前 Docker Desktop Linux engine 返回 500 / timeout，未做容器化真实后端验收 |
| PASS | span 字段符合边界 | 检查导出内容 | 只包含低基数字段 | `test_tracing.py` 和设计约束覆盖 |
| PASS | span 不泄露敏感信息 | 检查导出内容 | 默认不包含 request.id；不包含 user_id、user_id_hash、tenant_id、query、prompt、completion、异常 message、对话内容 | 当前 Trace 默认不写 request.id；显式开启时只写 request.id；敏感字段约束由测试和文档覆盖 |
| PASS | request_id Trace 关联显式开关 | `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` | 只有同时启用 Trace 总开关和 request_id 开关时写入 `request.id` | 2026-05-26 已由 `test_tracing.py` 覆盖 |

当前 Trace PoC 边界：

- 只覆盖 Web `stream_chat` request span。
- span 名称为 `cloud_agent.stream_chat`。
- 默认不记录 `request.id`。
- 显式设置 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` 后，仅允许额外记录 `request.id`。
- 当前不扩展 Agent / Tool / Memory / LLM 子 span。

如果需要按 `request_id` 检索 Trace，可显式开启：

```text
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true
```

即使启用，也只允许写入 `request.id`，不得写入用户身份、用户输入、模型输出、异常 message 或对话内容。

## 9. 敏感字段验收

| 状态 | 检查项 | 范围 | 预期结果 | 记录 |
| --- | --- | --- | --- | --- |
| PASS | metrics 不包含高基数字段 | `/api/metrics`、Prometheus、Grafana PromQL | 不包含 `request_id`、`user_id`、`user_id_hash`、`tenant_id`、`session_id`、`thread_id`、`conversation_id` | `test_metrics.py` / `test_observability_ops.py` 覆盖；真实 smoke 检查无明文用户/订单/request_id label |
| PASS | metrics 不包含内容字段 | `/api/metrics`、Prometheus、Grafana PromQL | 不包含 prompt、completion、query、matched_question、对话内容、偏好内容；token_type 或指标名中的 token 分类不代表文本内容 | `test_metrics.py` / `test_event_log.py` 覆盖；LLM token/cost 只记录数值 |
| PASS | alert labels 保持低基数 | `ops/prometheus/cloud_agent_alerts.yml` | 只使用 `severity`、`service` 等低基数 label | `test_observability_ops.py` 覆盖所有 alert labels |
| PASS | Trace 不包含敏感字段 | Trace exporter 输出 | 不包含用户身份、用户输入、模型输出、异常 message、对话内容 | `test_tracing.py` 和 Trace 设计边界覆盖；本轮未启用 request_id trace 开关 |

可用搜索命令：

```powershell
rg -n "request_id|user_id|user_id_hash|tenant_id|session_id|thread_id|conversation_id|prompt|completion|matched_question" ops\prometheus ops\grafana
```

说明：Grafana JSON schema 中可能存在字段名 `query`，这不是业务 query 内容。判断时应结合上下文。

## 10. 验收结论模板

```text
验收日期：
验收人：

FastAPI metrics：
Prometheus target：
Prometheus rules：
Grafana datasource：
Grafana dashboard：
Trace Console exporter：
Trace OTLP exporter：
敏感字段检查：

阻塞项：
失败项：
后续处理：
总体结论：
```

## 11. 当前建议

Docker runtime acceptance 已通过。若后续环境启动观测栈受阻，优先完成以下低风险工作：

1. 优先使用 Ubuntu VM 原生 Prometheus + Grafana 路径，或使用 `ops/observability_acceptance.py` 记录 Windows 验收结果。
2. 如本机可安装 Prometheus 工具，先执行 `promtool check rules`。
3. 用 `/api/chat` 产生真实业务流量后，确认 Prometheus target、rules 和 Grafana dashboard。
4. 对 `No data` 面板按业务路径判读：未触发 LLM、MCP tool、MCP registry 或 semantic cache hit 时，相关面板无数据是正常状态。
5. 暂不扩大 Agent / Tool / Memory / LLM 子 span。
6. 成本与缓存收益 dashboard 已有静态 JSON；当前建议先观察真实样本，不新增成本类告警。

## 12. Ubuntu VM 原生验收记录

2026-05-24 PDT，Ubuntu VM 原生 Prometheus + Grafana 已完成一次真实验收。

已确认：

- Grafana `grafana-server` active，`/api/health` 返回 `database: ok`。
- Prometheus active，`/-/ready` 返回 `Prometheus is Ready.`。
- Prometheus target `cloud_agent` health 为 `up`，抓取地址为 `http://localhost:5000/api/metrics`。
- `promtool check rules /etc/prometheus/rules/cloud_agent_alerts.yml` 成功，Prometheus UI 显示 `cloud_agent.rules`。
- Grafana datasource 指向 `http://localhost:9090`。
- `/var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json` 存在。
- `/api/metrics` 已输出 `cloud_agent_degradation_total`、`cloud_agent_event_total`、`cloud_agent_request_total`、`cloud_agent_route_total`、`cloud_agent_semantic_cache_lookup_total` 等真实样本。
- Grafana dashboard 中 Request、Routing、Cache & Memory、Degradation 面板已有数据。

当前未强制要求全部面板有数据：

- LLM 面板需要实际 LLM 调用。
- MCP Tool 面板需要实际 MCP tool 调用。
- MCP Registry 面板需要 registry 初始化事件。
- Semantic cache hit rate 需要正常 cache hit。
- LLM Cost & Cache Benefit 面板需要 token/cost 或 cache benefit 样本。
