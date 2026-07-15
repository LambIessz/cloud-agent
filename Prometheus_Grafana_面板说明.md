# Prometheus / Grafana 面板说明

更新时间：2026-05-25

本文档用于第四阶段“可观测性与生产化”的 Grafana / Prometheus 小步落地。当前项目已经提供 `GET /api/metrics`，输出 Prometheus text format，并且已完成 request、route、cache、memory、LLM、MCP tool、degradation、MCP registry 等指标族。

本文档只基于现有 `/api/metrics` 指标给出面板、PromQL 和告警建议，不接入 OpenTelemetry Trace，不修改运行时代码。

## 1. 当前指标边界

### 1.1 暴露端点

FastAPI 已注册：

```text
GET /api/metrics
```

返回类型：

```text
text/plain; version=0.0.4; charset=utf-8
```

Prometheus 抓取示例：

```yaml
scrape_configs:
  - job_name: cloud_agent
    metrics_path: /api/metrics
    static_configs:
      - targets:
          - localhost:5000
```

如果服务通过反向代理暴露，`targets` 和 `metrics_path` 按部署地址调整即可。

### 1.2 重要限制

当前 metrics helper 使用进程内存储，不依赖 `prometheus-client`。因此：

- 进程重启后 counter 会归零，Grafana 面板应优先使用 `rate()` / `increase()`。
- 现有 dashboard 和 alert rules 仍使用 `_latency_ms_count` / `_latency_ms_sum` 计算平均延迟。
- 当前已新增 request / LLM / MCP tool 的 `duration_ms` histogram 指标，可用于后续 p95 / p99。
- 当前 dashboard 和 alert rules 已新增 p95 / p99 面板与 p95 告警，基于 `duration_ms` histogram 使用 `histogram_quantile()`。
- 多实例部署时需要在 PromQL 中按 `job` / `instance` 聚合，避免只看单个实例。

### 1.3 敏感与高基数 label 约束

当前 metrics helper 会过滤以下 label：

```text
request_id
user_id
user_id_hash
tenant_id
session_id
thread_id
conversation_id
query
prompt
completion
message
matched_question
```

后续新增指标或面板时继续遵守：

- 不把明文 `user_id`、`user_id_hash`、`tenant_id`、`request_id` 作为 Prometheus label。
- 不把异常 message、prompt、completion、query、matched_question、对话内容、偏好内容写入日志或 metrics。
- `tool_name` 当前来自 MCP 工具白名单，基数可控；如果后续允许动态工具名，需要重新评估 label 基数。

## 2. 指标清单

所有指标输出时都会带一次 `HELP` 和一次 `TYPE`，当前类型均为 `counter`。

| 指标族 | 主要标签 | 用途 |
| --- | --- | --- |
| `cloud_agent_event_total` | `event_type`, `component`, `operation` | 所有结构化事件总量 |
| `cloud_agent_event_status_total` | `event_type`, `component`, `operation`, `status` | 所有结构化事件状态分布 |
| `cloud_agent_event_error_total` | `event_type`, `component`, `operation`, `error_type` | 所有结构化事件错误类型 |
| `cloud_agent_event_latency_ms_count` | `event_type`, `component`, `operation`, `status` | 有延迟字段的事件次数 |
| `cloud_agent_event_latency_ms_sum` | `event_type`, `component`, `operation`, `status` | 有延迟字段的事件延迟总和 |
| `cloud_agent_request_total` | `component`, `operation`, `status` | 请求完成总量 |
| `cloud_agent_request_success_total` | `component`, `operation` | 请求成功总量 |
| `cloud_agent_request_error_total` | `component`, `operation`, `error_type` | 请求错误总量 |
| `cloud_agent_request_latency_ms_count` | `component`, `operation`, `status` | 请求延迟计数 |
| `cloud_agent_request_latency_ms_sum` | `component`, `operation`, `status` | 请求延迟总和 |
| `cloud_agent_request_duration_ms_bucket` | `component`, `operation`, `status`, `le` | 请求延迟 histogram bucket |
| `cloud_agent_request_duration_ms_count` | `component`, `operation`, `status` | 请求延迟 histogram 计数 |
| `cloud_agent_request_duration_ms_sum` | `component`, `operation`, `status` | 请求延迟 histogram 总和 |
| `cloud_agent_route_total` | `route_to`, `primary_intent`, `is_finops_workflow` | Orchestrator 路由分布 |
| `cloud_agent_route_fallback_total` | 无 | fallback 路由次数 |
| `cloud_agent_semantic_cache_lookup_total` | `component`, `operation`, `status` | 语义缓存查询分布 |
| `cloud_agent_semantic_cache_hit_total` | `component`, `operation` | 语义缓存命中次数 |
| `cloud_agent_semantic_cache_miss_total` | `component`, `operation` | 语义缓存未命中次数 |
| `cloud_agent_semantic_cache_degraded_total` | `component`, `operation` | 语义缓存降级次数 |
| `cloud_agent_semantic_cache_unavailable_total` | `component`, `operation` | 语义缓存不可用次数 |
| `cloud_agent_memory_retrieve_total` | `component`, `operation`, `status` | 记忆读取分布 |
| `cloud_agent_memory_save_total` | `component`, `operation`, `status` | 记忆保存分布 |
| `cloud_agent_memory_background_extract_total` | `component`, `operation`, `status` | 后台偏好提取分布 |
| `cloud_agent_memory_degraded_total` | `component`, `operation`, `status`, `event_type` | 记忆链路降级或不可用次数 |
| `cloud_agent_memory_retrieved_item_total` | `component`, `operation` | 读取到的记忆条目数 |
| `cloud_agent_memory_extracted_preference_total` | `component`, `operation` | 提取出的偏好条目数 |
| `cloud_agent_llm_call_total` | `component`, `operation`, `status` | LLM 调用分布 |
| `cloud_agent_llm_error_total` | `component`, `operation`, `status`, `error_type` | LLM 错误分布 |
| `cloud_agent_llm_latency_ms_count` | `component`, `operation`, `status` | LLM 延迟计数 |
| `cloud_agent_llm_latency_ms_sum` | `component`, `operation`, `status` | LLM 延迟总和 |
| `cloud_agent_llm_duration_ms_bucket` | `component`, `operation`, `status`, `le` | LLM 延迟 histogram bucket |
| `cloud_agent_llm_duration_ms_count` | `component`, `operation`, `status` | LLM 延迟 histogram 计数 |
| `cloud_agent_llm_duration_ms_sum` | `component`, `operation`, `status` | LLM 延迟 histogram 总和 |
| `cloud_agent_llm_prompt_token_total` | `component`, `operation`, `model`, `status` | LLM prompt token 累计 |
| `cloud_agent_llm_completion_token_total` | `component`, `operation`, `model`, `status` | LLM completion token 累计 |
| `cloud_agent_llm_token_total` | `component`, `operation`, `model`, `status`, `token_type` | LLM token 累计 |
| `cloud_agent_llm_estimated_cost_usd_total` | `component`, `operation`, `model`, `status` | LLM 估算成本累计，单位 USD |
| `cloud_agent_tool_call_total` | `component`, `operation`, `tool_name`, `status` | MCP 工具调用分布 |
| `cloud_agent_tool_error_total` | `component`, `operation`, `tool_name`, `status`, `error_type` | MCP 工具错误分布 |
| `cloud_agent_tool_latency_ms_count` | `component`, `operation`, `tool_name`, `status` | MCP 工具延迟计数 |
| `cloud_agent_tool_latency_ms_sum` | `component`, `operation`, `tool_name`, `status` | MCP 工具延迟总和 |
| `cloud_agent_tool_duration_ms_bucket` | `component`, `operation`, `tool_name`, `status`, `le` | MCP 工具延迟 histogram bucket |
| `cloud_agent_tool_duration_ms_count` | `component`, `operation`, `tool_name`, `status` | MCP 工具延迟 histogram 计数 |
| `cloud_agent_tool_duration_ms_sum` | `component`, `operation`, `tool_name`, `status` | MCP 工具延迟 histogram 总和 |
| `cloud_agent_degradation_total` | `component`, `operation`, `status` | 降级事件分布 |
| `cloud_agent_degradation_error_total` | `component`, `operation`, `status`, `error_type` | 降级事件错误类型 |
| `cloud_agent_mcp_registry_initialize_total` | `component`, `operation`, `status` | MCP registry 初始化分布 |
| `cloud_agent_mcp_registry_error_total` | `component`, `operation`, `status`, `error_type` | MCP registry 初始化错误 |
| `cloud_agent_mcp_registry_server_count_sum` | `component`, `operation`, `status` | registry 初始化发现的 server 数累计 |
| `cloud_agent_mcp_registry_tool_count_sum` | `component`, `operation`, `status` | registry 初始化发现的 tool 数累计 |

## 3. 推荐 Grafana 面板

以下 PromQL 默认 Prometheus job 名为 `cloud_agent`。如果部署时使用其他 job 名，替换 `{job="cloud_agent"}` 即可。单实例开发环境也可以移除 `job` 过滤。

部分错误率、降级率面板在分子没有任何时间序列时，Grafana 可能显示 `No data` 而不是 `0`。如果希望强制显示 0，可以把分子改写为 `(sum(rate(xxx[5m])) or vector(0))`；告警规则通常可以保留 `No data`，避免没有流量时误报。

### 3.1 总览面板

#### 请求吞吐

Stat 或 Time series：

```promql
sum(rate(cloud_agent_request_total{job="cloud_agent",operation="stream_chat"}[5m]))
```

#### 请求成功 / 错误分布

Time series：

```promql
sum by (status) (
  rate(cloud_agent_request_total{job="cloud_agent",operation="stream_chat"}[5m])
)
```

#### 请求错误率

Stat：

```promql
sum(rate(cloud_agent_request_error_total{job="cloud_agent",operation="stream_chat"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_request_total{job="cloud_agent",operation="stream_chat"}[5m])),
  1
)
```

#### 请求平均延迟

Time series，单位设置为 milliseconds：

```promql
sum(rate(cloud_agent_request_latency_ms_sum{job="cloud_agent",operation="stream_chat"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_request_latency_ms_count{job="cloud_agent",operation="stream_chat"}[5m])),
  1
)
```

#### 全部 EventLog 事件量

Time series：

```promql
sum by (event_type) (
  rate(cloud_agent_event_total{job="cloud_agent"}[5m])
)
```

### 3.2 路由面板

#### Agent 路由分布

Bar gauge 或 Pie chart：

```promql
sum by (route_to) (
  increase(cloud_agent_route_total{job="cloud_agent"}[$__range])
)
```

#### 主意图分布

Bar gauge：

```promql
sum by (primary_intent) (
  increase(cloud_agent_route_total{job="cloud_agent"}[$__range])
)
```

#### fallback 比例

Stat：

```promql
sum(rate(cloud_agent_route_fallback_total{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_route_total{job="cloud_agent"}[5m])),
  1
)
```

#### FinOps 工作流占比

Stat：

```promql
sum(rate(cloud_agent_route_total{job="cloud_agent",is_finops_workflow="True"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_route_total{job="cloud_agent"}[5m])),
  1
)
```

### 3.3 语义缓存面板

#### 缓存命中率

Stat 或 Time series：

```promql
sum(rate(cloud_agent_semantic_cache_hit_total{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_semantic_cache_lookup_total{job="cloud_agent"}[5m])),
  1
)
```

#### 缓存状态分布

Time series：

```promql
sum by (status) (
  rate(cloud_agent_semantic_cache_lookup_total{job="cloud_agent"}[5m])
)
```

#### 缓存降级 / 不可用次数

Stat：

```promql
sum(increase(cloud_agent_semantic_cache_degraded_total{job="cloud_agent"}[$__range]))
+
sum(increase(cloud_agent_semantic_cache_unavailable_total{job="cloud_agent"}[$__range]))
```

### 3.4 Memory 面板

#### 记忆读取状态

Time series：

```promql
sum by (component, operation, status) (
  rate(cloud_agent_memory_retrieve_total{job="cloud_agent"}[5m])
)
```

#### 记忆保存状态

Time series：

```promql
sum by (component, operation, status) (
  rate(cloud_agent_memory_save_total{job="cloud_agent"}[5m])
)
```

#### 后台偏好提取状态

Time series：

```promql
sum by (status) (
  rate(cloud_agent_memory_background_extract_total{job="cloud_agent"}[5m])
)
```

#### 记忆降级比例

Stat：

```promql
sum(rate(cloud_agent_memory_degraded_total{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_memory_retrieve_total{job="cloud_agent"}[5m]))
  +
  sum(rate(cloud_agent_memory_save_total{job="cloud_agent"}[5m]))
  +
  sum(rate(cloud_agent_memory_background_extract_total{job="cloud_agent"}[5m])),
  1
)
```

#### 平均每次读取返回记忆条数

Stat：

```promql
sum(rate(cloud_agent_memory_retrieved_item_total{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_memory_retrieve_total{job="cloud_agent"}[5m])),
  1
)
```

### 3.5 LLM 面板

#### LLM 调用量

Time series：

```promql
sum by (component, operation, status) (
  rate(cloud_agent_llm_call_total{job="cloud_agent"}[5m])
)
```

#### LLM 错误率

Stat：

```promql
sum(rate(cloud_agent_llm_error_total{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_llm_call_total{job="cloud_agent"}[5m])),
  1
)
```

#### LLM 平均延迟

Time series，单位设置为 milliseconds：

```promql
sum(rate(cloud_agent_llm_latency_ms_sum{job="cloud_agent"}[5m]))
/
clamp_min(
  sum(rate(cloud_agent_llm_latency_ms_count{job="cloud_agent"}[5m])),
  1
)
```

#### LLM 错误类型

Table：

```promql
sum by (component, operation, error_type) (
  increase(cloud_agent_llm_error_total{job="cloud_agent"}[$__range])
)
```

### 3.6 MCP Tool 面板

#### 工具调用量

Time series：

```promql
sum by (tool_name, status) (
  rate(cloud_agent_tool_call_total{job="cloud_agent"}[5m])
)
```

#### 工具错误率

Bar gauge：

```promql
sum by (tool_name) (
  rate(cloud_agent_tool_error_total{job="cloud_agent"}[5m])
)
/
clamp_min(
  sum by (tool_name) (
    rate(cloud_agent_tool_call_total{job="cloud_agent"}[5m])
  ),
  1
)
```

#### 工具平均延迟

Time series，单位设置为 milliseconds：

```promql
sum by (tool_name) (
  rate(cloud_agent_tool_latency_ms_sum{job="cloud_agent"}[5m])
)
/
clamp_min(
  sum by (tool_name) (
    rate(cloud_agent_tool_latency_ms_count{job="cloud_agent"}[5m])
  ),
  1
)
```

#### 工具错误类型

Table：

```promql
sum by (tool_name, error_type) (
  increase(cloud_agent_tool_error_total{job="cloud_agent"}[$__range])
)
```

### 3.7 降级事件面板

#### 降级事件分布

Time series：

```promql
sum by (component, operation, status) (
  rate(cloud_agent_degradation_total{job="cloud_agent"}[5m])
)
```

#### 降级错误类型

Table：

```promql
sum by (component, operation, error_type) (
  increase(cloud_agent_degradation_error_total{job="cloud_agent"}[$__range])
)
```

#### 最近窗口降级总数

Stat：

```promql
sum(increase(cloud_agent_degradation_total{job="cloud_agent"}[10m]))
```

### 3.8 MCP Registry 面板

#### registry 初始化状态

Time series：

```promql
sum by (status) (
  rate(cloud_agent_mcp_registry_initialize_total{job="cloud_agent"}[5m])
)
```

#### registry 初始化错误

Table：

```promql
sum by (status, error_type) (
  increase(cloud_agent_mcp_registry_error_total{job="cloud_agent"}[$__range])
)
```

#### 平均每次成功初始化发现的工具数

Stat：

```promql
sum(increase(cloud_agent_mcp_registry_tool_count_sum{job="cloud_agent",status="success"}[$__range]))
/
clamp_min(
  sum(increase(cloud_agent_mcp_registry_initialize_total{job="cloud_agent",status="success"}[$__range])),
  1
)
```

#### 平均每次成功初始化发现的 server 数

Stat：

```promql
sum(increase(cloud_agent_mcp_registry_server_count_sum{job="cloud_agent",status="success"}[$__range]))
/
clamp_min(
  sum(increase(cloud_agent_mcp_registry_initialize_total{job="cloud_agent",status="success"}[$__range])),
  1
)
```

## 4. 推荐告警

下面的阈值适合开发 / 演示环境起步。生产环境需要根据真实流量、模型供应商 SLA、MCP 工具耗时和缓存依赖稳定性重新校准。

### 4.1 服务抓取失败

```yaml
- alert: CloudAgentMetricsScrapeDown
  expr: up{job="cloud_agent"} == 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: cloud_agent metrics endpoint is down
    description: Prometheus cannot scrape GET /api/metrics for 2 minutes.
```

### 4.2 请求错误率过高

```yaml
- alert: CloudAgentRequestErrorRateHigh
  expr: |
    sum(rate(cloud_agent_request_error_total{job="cloud_agent",operation="stream_chat"}[5m]))
    /
    clamp_min(
      sum(rate(cloud_agent_request_total{job="cloud_agent",operation="stream_chat"}[5m])),
      1
    ) > 0.05
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent request error rate is high
    description: stream_chat request error rate is above 5% for 10 minutes.
```

### 4.3 请求平均延迟过高

```yaml
- alert: CloudAgentRequestAverageLatencyHigh
  expr: |
    sum(rate(cloud_agent_request_latency_ms_sum{job="cloud_agent",operation="stream_chat"}[5m]))
    /
    clamp_min(
      sum(rate(cloud_agent_request_latency_ms_count{job="cloud_agent",operation="stream_chat"}[5m])),
      1
    ) > 3000
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent request average latency is high
    description: stream_chat average latency is above 3000 ms for 10 minutes.
```

### 4.4 fallback 路由比例过高

```yaml
- alert: CloudAgentFallbackRouteRateHigh
  expr: |
    sum(rate(cloud_agent_route_fallback_total{job="cloud_agent"}[5m]))
    /
    clamp_min(
      sum(rate(cloud_agent_route_total{job="cloud_agent"}[5m])),
      1
    ) > 0.20
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent fallback route rate is high
    description: More than 20% of route decisions go to fallback_agent.
```

### 4.5 语义缓存不可用

```yaml
- alert: CloudAgentSemanticCacheUnavailable
  expr: |
    sum(increase(cloud_agent_semantic_cache_unavailable_total{job="cloud_agent"}[10m])) > 0
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent semantic cache is unavailable
    description: Semantic cache reported unavailable events in the last 10 minutes.
```

### 4.6 Memory 降级

```yaml
- alert: CloudAgentMemoryDegraded
  expr: |
    sum(increase(cloud_agent_memory_degraded_total{job="cloud_agent"}[10m])) > 0
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent memory path degraded
    description: Redis, Milvus, or background extraction emitted degraded or unavailable memory events.
```

### 4.7 LLM 错误率过高

```yaml
- alert: CloudAgentLLMErrorRateHigh
  expr: |
    sum(rate(cloud_agent_llm_error_total{job="cloud_agent"}[5m]))
    /
    clamp_min(
      sum(rate(cloud_agent_llm_call_total{job="cloud_agent"}[5m])),
      1
    ) > 0.05
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent LLM error rate is high
    description: LLM error rate is above 5% for 10 minutes.
```

### 4.8 LLM 平均延迟过高

```yaml
- alert: CloudAgentLLMAverageLatencyHigh
  expr: |
    sum(rate(cloud_agent_llm_latency_ms_sum{job="cloud_agent"}[5m]))
    /
    clamp_min(
      sum(rate(cloud_agent_llm_latency_ms_count{job="cloud_agent"}[5m])),
      1
    ) > 5000
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent LLM average latency is high
    description: LLM average latency is above 5000 ms for 10 minutes.
```

### 4.9 MCP 工具错误率过高

```yaml
- alert: CloudAgentMCPToolErrorRateHigh
  expr: |
    sum by (tool_name) (
      rate(cloud_agent_tool_error_total{job="cloud_agent"}[5m])
    )
    /
    clamp_min(
      sum by (tool_name) (
        rate(cloud_agent_tool_call_total{job="cloud_agent"}[5m])
      ),
      1
    ) > 0.05
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent MCP tool error rate is high
    description: One or more MCP tools have error rate above 5% for 10 minutes.
```

### 4.10 MCP 工具平均延迟过高

```yaml
- alert: CloudAgentMCPToolAverageLatencyHigh
  expr: |
    sum by (tool_name) (
      rate(cloud_agent_tool_latency_ms_sum{job="cloud_agent"}[5m])
    )
    /
    clamp_min(
      sum by (tool_name) (
        rate(cloud_agent_tool_latency_ms_count{job="cloud_agent"}[5m])
      ),
      1
    ) > 3000
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent MCP tool average latency is high
    description: One or more MCP tools have average latency above 3000 ms for 10 minutes.
```

### 4.11 降级事件突增

```yaml
- alert: CloudAgentDegradationBurst
  expr: |
    sum(increase(cloud_agent_degradation_total{job="cloud_agent"}[10m])) > 5
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: cloud_agent degradation events increased
    description: More than 5 degradation events were emitted in 10 minutes.
```

### 4.12 MCP registry 初始化失败

```yaml
- alert: CloudAgentMCPRegistryInitializeFailed
  expr: |
    sum(increase(cloud_agent_mcp_registry_error_total{job="cloud_agent"}[10m])) > 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: cloud_agent MCP registry initialization failed
    description: MCP registry emitted initialization errors in the last 10 minutes.
```

## 5. Grafana Dashboard 建议布局

建议先做一个 `Cloud Agent Overview` dashboard，分为以下行：

1. `Request`
   - 请求吞吐
   - 请求成功 / 错误分布
   - 请求错误率
   - 请求平均延迟

2. `Routing`
   - Agent 路由分布
   - 主意图分布
   - fallback 比例
   - FinOps 工作流占比

3. `Cache & Memory`
   - 缓存命中率
   - 缓存状态分布
   - 记忆读取状态
   - 记忆保存状态
   - 后台偏好提取状态
   - 记忆降级比例

4. `LLM`
   - LLM 调用量
   - LLM 错误率
   - LLM 平均延迟
   - LLM 错误类型表

5. `MCP Tool`
   - 工具调用量
   - 工具错误率
   - 工具平均延迟
   - 工具错误类型表

6. `Degradation & MCP Registry`
   - 降级事件分布
   - 降级错误类型表
   - registry 初始化状态
   - registry 初始化错误表
   - 平均初始化工具数
   - 平均初始化 server 数

## 6. 后续演进建议

短期仍建议保持现有方案，不急于大范围接入 Trace：

1. 如果需要 p95 / p99 延迟，当前 metrics helper 已新增固定 bucket histogram；下一步是更新 Grafana dashboard、Prometheus alert rules 和 `test_observability_ops.py`。
2. 如果需要 Trace，先做最小 proof-of-concept，只覆盖 Web `stream_chat` request span，不把 Agent / Tool / Memory 全链路一次性纳入。
3. 如果部署多实例，建议在 dashboard 中增加 `instance` 维度筛选，并在告警中使用 `sum by(job)` 或 `sum without(instance)` 聚合。
4. 如果后续记录 token 成本，新增指标时只记录数值、模型名、组件名，不记录 prompt、completion 或对话内容；当前已补充设计文档，见 `ops/prometheus/cost_cache_metrics_design.md`。

## 7. Dashboard JSON 草案

已提供可导入 Grafana 的 dashboard 草案：

```text
ops/grafana/cloud_agent_overview_dashboard.json
```

导入方式：

1. 在 Grafana 中进入 `Dashboards` -> `New` -> `Import`。
2. 上传或粘贴 `ops/grafana/cloud_agent_overview_dashboard.json`。
3. 选择 Prometheus 数据源。
4. 导入后检查变量：
   - `DS_PROMETHEUS`：Prometheus datasource。
   - `job`：默认值为 `cloud_agent`，如果 Prometheus scrape job 名不同，需要切换。

当前 dashboard 覆盖：

- Request：吞吐、错误率、平均延迟。
- Routing：Agent 路由分布、fallback 比例、FinOps 工作流比例。
- Cache & Memory：缓存命中率、缓存状态、memory 降级比例。
- LLM：调用量、错误率、平均延迟。
- MCP Tool：工具调用量、工具错误率、工具平均延迟。
- Latency Percentiles：Request p95、Request p99、LLM p95 by operation、MCP tool p95 by tool。
- Degradation & MCP Registry：降级事件、降级错误类型、registry 初始化状态。

该 JSON 只引用现有 Prometheus 指标，不包含 `request_id`、`user_id`、`user_id_hash`、`tenant_id`、prompt、completion、matched_question 或对话内容等敏感 label。JSON 内部存在 Grafana 变量字段名 `query`，这是 Grafana schema 字段，不是业务 query 内容。

## 8. Prometheus Alert Rules

已提供可加载的 Prometheus 告警规则草案：

```text
ops/prometheus/cloud_agent_alerts.yml
```

Prometheus 配置示例：

```yaml
rule_files:
  - ops/prometheus/cloud_agent_alerts.yml
```

如果 Prometheus 配置文件不在项目根目录，需要把路径改成相对于 Prometheus 配置文件的实际路径。

当前规则组：

```text
cloud_agent.rules
```

当前包含 15 条告警：

- `CloudAgentMetricsScrapeDown`
- `CloudAgentRequestErrorRateHigh`
- `CloudAgentRequestAverageLatencyHigh`
- `CloudAgentFallbackRouteRateHigh`
- `CloudAgentSemanticCacheUnavailable`
- `CloudAgentMemoryDegraded`
- `CloudAgentLLMErrorRateHigh`
- `CloudAgentLLMAverageLatencyHigh`
- `CloudAgentMCPToolErrorRateHigh`
- `CloudAgentMCPToolAverageLatencyHigh`
- `CloudAgentRequestP95LatencyHigh`
- `CloudAgentLLMP95LatencyHigh`
- `CloudAgentMCPToolP95LatencyHigh`
- `CloudAgentDegradationBurst`
- `CloudAgentMCPRegistryInitializeFailed`

规则约束：

- 默认 Prometheus scrape job 为 `cloud_agent`。
- 告警标签只包含 `severity` 和 `service`。
- 规则只使用现有 `/api/metrics` 指标。
- 规则不引用 request_id、明文 user_id、user_id_hash、tenant_id、prompt、completion、matched_question 或对话内容。
- 延迟告警同时保留平均延迟告警，并新增 request / LLM / MCP tool 的 p95 告警。

本地已用 PyYAML 做结构解析：

```powershell
python -c "from pathlib import Path; import yaml; data=yaml.safe_load(Path('ops/prometheus/cloud_agent_alerts.yml').read_text(encoding='utf-8')); print(data['groups'][0]['name']); print(len(data['groups'][0]['rules']))"
```

如果环境安装了 Prometheus 工具，建议再做正式规则校验：

```powershell
promtool check rules ops\prometheus\cloud_agent_alerts.yml
```

## 9. OpenTelemetry Trace 最小 PoC

已接入一个非常窄的 Trace proof-of-concept，只覆盖 Web `stream_chat` request span。

涉及文件：

```text
cloud_agent/agent/core/workflow/tracing.py
cloud_agent/app/service/chat_service.py
```

设计边界：

- 默认关闭，只有设置 `CLOUD_AGENT_TRACE_ENABLED=true` 才尝试创建 span。
- 如果当前环境没有安装 OpenTelemetry API，自动 no-op，不影响请求链路。
- 只创建一个 span：`cloud_agent.stream_chat`。
- 只记录低基数字段：
  - `component=chat_service`
  - `operation=stream_chat`
  - `identity.source`
  - `cache.status`
  - `request.status`
  - `error.type`
- 不记录 request_id、明文 user_id、user_id_hash、tenant_id、session_id、thread_id、conversation_id。
- 不记录 query、prompt、completion、matched_question、对话内容或偏好内容。
- 异常时只记录异常类型，例如 `RuntimeError`，不记录异常 message 或堆栈。
- 不改变 EventLog、metrics、SSE done、ToolAudit、Degradation、MCP registry、timeout/retry 语义。

启用方式：

```powershell
$env:CLOUD_AGENT_TRACE_ENABLED="true"
```

说明：当前代码只做最小 span 生命周期封装。若要真正导出 Trace，需要运行环境自行安装并配置 OpenTelemetry SDK / exporter，例如 Console、OTLP 或 Jaeger exporter。这个 PoC 暂不在项目 requirements 中强制加入 OpenTelemetry 依赖，避免影响现有测试和部署。

后续如果继续推进 Trace，建议保持小步：

1. 先补本地 ConsoleSpanExporter 或 OTLP exporter 的启动说明。
2. 再考虑为 `request_id` 做受控关联策略，但不要把它作为高基数 Prometheus label。
3. 最后再评估是否扩展到 Agent / Tool / Memory 子 span，不要一次性全链路改造。

### 9.1 本地导出说明

已补充 Trace 本地导出说明：

```text
ops/otel/README.md
```

该文档覆盖：

- ConsoleSpanExporter 本地验证方式。
- OTLP exporter 接入 collector 的环境变量示例。
- 当前 `cloud_agent.stream_chat` span 应出现的字段。
- 不应出现在 span 中的敏感字段清单。
- 没有看到 span 时的排查顺序。

该说明只用于本地验证 Trace PoC，不要求项目默认安装 OpenTelemetry SDK / exporter，也不改变运行时代码。

### 9.2 request_id 与 Trace 关联设计

已补充 `request_id` 与 Trace 的关联设计文档：

```text
ops/otel/request_id_trace_correlation.md
```

当前结论：

- `request_id` 继续作为 EventLog、ToolAudit、Degradation 和 SSE done 的排障主索引。
- `request_id` 继续禁止进入 Prometheus label。
- Trace span 默认不写入 `request_id`。
- 如果后续确实需要在 Trace 后端按请求检索，应单独增加显式开关，例如 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true`。
- 即使启用，也只允许写入 `request.id`，不写用户身份、用户输入、模型输出、异常 message 或对话内容。

该文档目前只做设计，不修改运行时代码。

## 10. 本地 Prometheus + Grafana 启动

已补充本地观测栈配置和启动说明：

```text
ops/README.md
ops/docker-compose.observability.yml
ops/prometheus/prometheus.yml
ops/grafana/provisioning/datasources/prometheus.yml
ops/grafana/provisioning/dashboards/cloud_agent.yml
```

用途：

- Prometheus 抓取 `cloud_agent` 的 `/api/metrics`。
- Prometheus 加载 `ops/prometheus/cloud_agent_alerts.yml`。
- Grafana 自动配置 Prometheus datasource。
- Grafana 自动加载 `Cloud Agent Overview` dashboard。

本地启动顺序：

1. 先启动 FastAPI：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\cloud_agent\app
python -m uvicorn app_main:app --host 0.0.0.0 --port 5000
```

2. 再启动观测栈：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\ops
docker compose -f docker-compose.observability.yml up
```

访问地址：

```text
Prometheus: http://localhost:9090
Grafana: http://localhost:3000
Grafana 默认登录: admin / admin
```

Prometheus 默认抓取目标：

```text
host.docker.internal:5000/api/metrics
```

该配置适合 Windows Docker Desktop。如果运行环境不支持 `host.docker.internal`，需要调整 `ops/prometheus/prometheus.yml` 中的 target。

已做静态校验：

```powershell
python -c "from pathlib import Path; import yaml; paths=['ops/docker-compose.observability.yml','ops/prometheus/prometheus.yml','ops/prometheus/cloud_agent_alerts.yml','ops/grafana/provisioning/datasources/prometheus.yml','ops/grafana/provisioning/dashboards/cloud_agent.yml']; [print(p, type(yaml.safe_load(Path(p).read_text(encoding='utf-8'))).__name__) for p in paths]"
docker compose -f ops\docker-compose.observability.yml config
```

说明：本地执行 `docker compose config` 可正常解析 compose 文件，但 Docker CLI 输出了用户级 `~/.docker/config.json` 权限 warning；这属于当前机器 Docker 配置权限问题，不是 compose 文件结构错误。

### 10.1 Ubuntu VM 原生安装路径

如果 Docker Desktop 或 Docker Hub 镜像拉取不可用，可以不使用 Docker，直接在 Ubuntu VM 中原生运行 Prometheus + Grafana。当前项目已经补充原生验收说明：

```text
ops/native_observability_ubuntu.md
```

原生方式与 Docker compose 的主要差异：

- Prometheus target 使用 `localhost:5000`。
- Grafana datasource 使用 `http://localhost:9090`。
- Prometheus rule file 放在 `/etc/prometheus/rules/cloud_agent_alerts.yml`。
- Grafana dashboard JSON 放在 `/var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json`。
- Grafana provisioning 文件放在 `/etc/grafana/provisioning/...`。

2026-05-24 PDT 已在 Ubuntu VM 完成一次真实验收：

- `grafana-server` active，`curl http://localhost:3000/api/health` 返回 `database: ok`。
- Prometheus active，`curl http://localhost:9090/-/ready` 返回 `Prometheus is Ready.`。
- Prometheus `cloud_agent` target 为 `up`。
- `promtool check rules /etc/prometheus/rules/cloud_agent_alerts.yml` 成功。
- Grafana dashboard 已加载，`/api/metrics` 已有真实 `cloud_agent_*` 样本。

部分面板显示 `No data` 是可接受状态，前提是对应业务路径尚未触发。例如 LLM、MCP Tool、MCP Registry、semantic cache hit、estimated cost / cache benefit 都需要对应事件先产生样本。

## 11. 手动验收 Checklist

已补充本地观测栈手动验收 checklist：

```text
ops/observability_checklist.md
```

该 checklist 用于在实际启动受阻或分阶段验收时记录状态，覆盖：

- `/api/metrics` 可访问性。
- Prometheus target `cloud_agent` 是否为 `UP`。
- Prometheus alert rules 是否加载。
- Grafana datasource 和 `Cloud Agent Overview` dashboard 是否自动加载。
- Trace Console / OTLP exporter 是否能看到 `cloud_agent.stream_chat` span。
- metrics、dashboard、alert rules、Trace 是否泄露敏感字段。

如果 Docker、端口、镜像拉取或本机权限问题导致无法完整启动，可以把对应项标记为 `BLOCKED`，记录原因，后续环境恢复后继续验收。

## 12. 运维配置自动化静态测试

已补充不依赖 Docker / Grafana / Prometheus 进程的 pytest 静态测试：

```text
cloud_agent/agent/test/test_observability_ops.py
```

测试覆盖：

- `ops/prometheus/prometheus.yml` 会加载 `/etc/prometheus/rules/cloud_agent_alerts.yml`。
- Prometheus scrape job 为 `cloud_agent`，抓取 `/api/metrics`，target 为 `host.docker.internal:5000`。
- `ops/prometheus/cloud_agent_alerts.yml` 只有一个 rule group：`cloud_agent.rules`。
- 告警规则数量保持 15 条，名称与当前设计一致。
- 告警规则 labels 只包含 `severity` 和 `service`。
- 只有 p95 延迟告警使用 `histogram_quantile()`，其他告警不使用。
- 告警表达式、labels、annotations 不包含 `request_id`、`user_id`、`user_id_hash`、`tenant_id`、`session_id`、`thread_id`、`conversation_id`、prompt、completion、matched_question。
- Grafana dashboard title / uid、datasource 变量、job 变量和主要 row 分组保持稳定。
- Grafana dashboard PromQL 只引用当前已有指标，不引用敏感字段；只有 Latency Percentiles 面板使用 `histogram_quantile()`。
- Grafana provisioning 指向 Prometheus datasource 和 dashboard 目录。

执行命令：

```powershell
python -m pytest cloud_agent\agent\test\test_observability_ops.py -q
```

## 13. p95 / p99 Histogram 指标设计

已补充 p95 / p99 延迟 histogram 指标设计文档：

```text
ops/prometheus/histogram_metrics_design.md
```

当前已完成 metrics helper、Grafana dashboard 和 Prometheus alert rules 的 p95 / p99 小步接入。

核心结论：

- 不直接复用现有 `cloud_agent_request_latency_ms_count` / `_sum` 所属命名，避免 Prometheus histogram 与现有 counter family 发生语义冲突。
- 后续新增独立 metric family，建议命名为：
  - `cloud_agent_request_duration_ms`
  - `cloud_agent_llm_duration_ms`
  - `cloud_agent_tool_duration_ms`
- 第一批只覆盖 request / LLM / MCP tool 三类关键延迟。
- bucket 使用固定毫秒边界，渲染时自动包含 `le="+Inf"`。
- 保留现有平均延迟指标和告警，不在第一步替换。
- 新增 histogram 时继续过滤敏感和高基数 label。
- 当前 dashboard / alert rules 已启用 `histogram_quantile()`，仅限 Latency Percentiles 面板和 3 条 p95 告警；`test_observability_ops.py` 已同步限制使用范围。

## 14. Token 成本与缓存收益指标设计

已补充 token 成本与缓存收益指标设计文档：

```text
ops/prometheus/cost_cache_metrics_design.md
```

当前已完成 LLM token 指标、价格配置、estimated cost 指标、`cache_benefit` event 的 estimated saved call / token / cost 指标、semantic cache hit 自动记录 estimated saved call、Semantic Cache v2 可选 token / cost 元数据，以及成本与缓存收益 Grafana dashboard 静态 JSON；仍未实现 Prometheus alert rules 和 Docker / Grafana 实际启动验收。

核心结论：

- LLM token 指标只记录数值，不记录 prompt、completion、query、matched_question 或对话内容。
- 允许的 label 仅限 `component`、`operation`、`model`、`status`、`token_type` 等低基数字段。
- 成本指标标记为 estimated，价格来自 `CLOUD_AGENT_LLM_PRICING_CONFIG` 指向的显式配置，不在代码中写死实时价格。
- semantic cache 收益只能做估算，当前 metrics helper 已支持 `cache_benefit` event，cache hit 业务路径已自动记录 `estimated_saved_calls=1`。
- Semantic Cache 默认 collection 已切换为 `qa_semantic_cache_v2`，支持可选 `estimated_prompt_tokens`、`estimated_completion_tokens`、`estimated_cost_usd` 和 `model` 元数据。
- cache hit 命中带元数据的缓存条目时，会透传 saved token / saved cost；没有元数据时仍只记录 saved call，避免凭空估算成本。
- Dashboard 已新增 `LLM Cost & Cache Benefit` row，包含 token rate、estimated cost/hour、estimated saved calls/tokens/cost 和 net estimated cost。
- 当前仍不新增成本类告警，避免估算口径未经过真实运行验收前进入告警链路。

## 15. 当前原生观测栈验收结论

截至 2026-05-25，第四阶段 Prometheus / Grafana 小步已经从“静态配置完成”推进到“Ubuntu VM 原生真实运行已验证”。

已验证闭环：

1. FastAPI `/api/metrics` 可访问并输出 Prometheus text format。
2. Prometheus 原生服务 ready。
3. Prometheus `cloud_agent` target 为 `up`。
4. Prometheus `cloud_agent.rules` 已加载并评估。
5. Grafana 原生服务 health 正常。
6. Grafana Prometheus datasource 已配置。
7. Grafana `Cloud Agent Overview` dashboard 已加载。
8. Request、Routing、Cache & Memory、Degradation 等已触发路径可显示真实数据。

仍需后续业务样本补齐的面板：

- LLM：需要产生 `llm_call` event。
- MCP Tool：需要触发 MCP tool 调用。
- MCP Registry：需要触发 registry 初始化事件。
- Semantic cache hit rate：需要语义缓存正常命中。
- LLM Cost & Cache Benefit：需要 token/cost 或 cache benefit 样本。

这些不是当前观测栈配置失败，而是业务样本尚未覆盖对应路径。下一步应优先用真实或测试流量补齐这些路径的样本，再决定是否需要调 dashboard 查询或告警阈值。
