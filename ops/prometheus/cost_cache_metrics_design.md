# Token 成本与缓存收益指标设计

更新时间：2026-05-24

本文档用于设计 LLM token 成本和 semantic cache 收益指标。当前 metrics helper 已完成 LLM prompt / completion token 指标、价格配置读取、estimated cost 指标，以及 `cache_benefit` event 的 estimated saved call / token / cost 指标；Grafana dashboard 静态 JSON 已接入成本与缓存收益 row；cache hit 业务路径已自动记录 estimated saved call；Semantic Cache v2 已支持可选 token / cost 元数据，命中时可透传为 saved token / saved cost。Prometheus rules 和非 cache hit 写入元数据链路尚未实现。

## 1. 背景

当前系统已经具备：

- `llm_call` EventLog 和 `cloud_agent_llm_call_total`。
- `cloud_agent_llm_latency_ms_count` / `_sum`。
- `cloud_agent_llm_duration_ms` histogram。
- semantic cache lookup / hit / miss / degraded / unavailable 指标。
- request / route / memory / MCP tool / degradation / MCP registry 指标。

但还不能回答以下问题：

- 每类 LLM 调用消耗了多少 prompt / completion token。
- 每类 LLM 调用的估算成本是多少。
- semantic cache 命中节省了多少 LLM 调用次数。
- semantic cache 命中估算节省了多少 token 和成本。

## 2. 设计目标

后续新增成本和缓存收益指标时应满足：

- 只记录数值指标，不记录 prompt、completion、query、matched_question、对话内容或偏好内容。
- 不记录 request_id、明文 user_id、user_id_hash、tenant_id、session_id、thread_id、conversation_id。
- 模型名、组件名、operation、status 作为可控低基数 label。
- 价格配置来自显式配置，不在代码里写死供应商实时价格。
- cache benefit 是估算值，必须在文档和面板中标记为 estimated。
- 保留现有 LLM 和 cache 指标，不破坏当前 dashboard / alert rules。

## 3. 指标命名

建议新增以下 counter 指标：

| 指标族 | 标签 | 含义 |
| --- | --- | --- |
| `cloud_agent_llm_prompt_token_total` | `component`, `operation`, `model`, `status` | LLM prompt token 累计，已实现 |
| `cloud_agent_llm_completion_token_total` | `component`, `operation`, `model`, `status` | LLM completion token 累计，已实现 |
| `cloud_agent_llm_token_total` | `component`, `operation`, `model`, `status`, `token_type` | LLM token 累计，`token_type=prompt/completion`，已实现 |
| `cloud_agent_llm_estimated_cost_usd_total` | `component`, `operation`, `model`, `status` | LLM 估算成本，单位 USD，已实现 |
| `cloud_agent_semantic_cache_estimated_saved_call_total` | `component`, `operation` | cache hit 估算节省的 LLM 调用次数 |
| `cloud_agent_semantic_cache_estimated_saved_token_total` | `component`, `operation`, `token_type` | cache hit 估算节省的 token |
| `cloud_agent_semantic_cache_estimated_saved_cost_usd_total` | `component`, `operation` | cache hit 估算节省成本，单位 USD |

说明：

- `llm_prompt_token_total` / `llm_completion_token_total` 便于直接看两类 token。
- `llm_token_total{token_type=...}` 便于在 Grafana 中按 token_type 聚合和堆叠。
- 如果担心重复指标过多，第一批可以只实现 `cloud_agent_llm_token_total`。

## 4. Label 白名单

允许：

```text
component
operation
model
status
token_type
```

cache benefit 允许：

```text
component
operation
token_type
```

禁止：

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
error_message
preference
```

`model` 必须来自受控配置或 LLM client 返回的模型 id，并建议做归一化。例如：

```text
qwen-plus
qwen-turbo
deepseek-chat
gpt-4.1-mini
unknown
```

不要把 deployment id、api key、endpoint、用户自定义模型别名等高基数或敏感值作为 label。

## 5. 价格配置

不建议在代码中写死价格。建议通过配置提供每 1000 token 单价：

```yaml
llm_pricing:
  qwen-plus:
    prompt_usd_per_1k: 0.0004
    completion_usd_per_1k: 0.0012
  qwen-turbo:
    prompt_usd_per_1k: 0.0001
    completion_usd_per_1k: 0.0003
  unknown:
    prompt_usd_per_1k: 0
    completion_usd_per_1k: 0
```

使用环境变量指向配置文件：

```text
CLOUD_AGENT_LLM_PRICING_CONFIG=ops/prometheus/llm_pricing.example.yml
```

已提供本地示例文件：

```text
ops/prometheus/llm_pricing.example.yml
```

没有价格配置时：

- token 指标照常记录。
- cost 指标不记录。
- 不因为价格缺失影响请求链路。

## 6. EventLog 扩展草案

当前 `llm_call` event 不记录 prompt / completion 文本。后续可以只增加数值字段：

```json
{
  "event_type": "llm_call",
  "component": "orchestrator",
  "operation": "route_classification",
  "status": "success",
  "model": "qwen-plus",
  "prompt_tokens": 120,
  "completion_tokens": 18,
  "estimated_cost_usd": 0.00007
}
```

禁止增加：

```json
{
  "prompt": "...",
  "completion": "...",
  "query": "...",
  "message": "...",
  "matched_question": "..."
}
```

## 7. Metrics Helper 草案

在 `record_event_metrics()` 中处理 `event_type == "llm_call"` 时：

```python
prompt_tokens = event.get("prompt_tokens")
completion_tokens = event.get("completion_tokens")
model = event.get("model", "unknown")
status = event.get("status", "unknown")
```

如果 token 数值是非负 int / float：

```python
increment_counter(
    "llm_token_total",
    {
        "component": component,
        "operation": operation,
        "model": model,
        "status": status,
        "token_type": "prompt",
    },
    amount=float(prompt_tokens),
)
```

同理记录 completion。

成本字段：

```python
estimated_cost_usd = event.get("estimated_cost_usd")
```

如果是非负数：

```python
increment_counter(
    "llm_estimated_cost_usd_total",
    {
        "component": component,
        "operation": operation,
        "model": model,
        "status": status,
    },
    amount=float(estimated_cost_usd),
)
```

## 8. 缓存收益估算

semantic cache 命中时，真实节省 token 无法精确知道，因为没有发生 LLM 调用。建议第一阶段使用估算值。

推荐策略：

1. 维护按 `operation` 和 `model` 聚合的近期平均 prompt / completion token。
2. cache hit 时按对应 operation 的平均值估算 saved token。
3. 如果没有足够历史样本，使用保守默认值或不记录 saved token。
4. saved cost 使用同一价格配置估算。

示例 EventLog：

```json
{
  "event_type": "cache_benefit",
  "component": "semantic_cache",
  "operation": "stream_chat",
  "status": "estimated",
  "estimated_saved_calls": 1,
  "estimated_saved_prompt_tokens": 120,
  "estimated_saved_completion_tokens": 80,
  "estimated_saved_cost_usd": 0.00014
}
```

对应指标：

```text
cloud_agent_semantic_cache_estimated_saved_call_total
cloud_agent_semantic_cache_estimated_saved_token_total{token_type="prompt"}
cloud_agent_semantic_cache_estimated_saved_token_total{token_type="completion"}
cloud_agent_semantic_cache_estimated_saved_cost_usd_total
```

## 9. PromQL 示例

### 9.1 LLM token rate

```promql
sum by (operation, model, token_type) (
  rate(cloud_agent_llm_token_total{job="cloud_agent"}[5m])
)
```

### 9.2 LLM cost per hour

```promql
sum by (operation, model) (
  increase(cloud_agent_llm_estimated_cost_usd_total{job="cloud_agent"}[1h])
)
```

### 9.3 Cache estimated saved cost per hour

```promql
sum(
  increase(cloud_agent_semantic_cache_estimated_saved_cost_usd_total{job="cloud_agent"}[1h])
)
```

### 9.4 Net estimated LLM cost per hour

```promql
sum(increase(cloud_agent_llm_estimated_cost_usd_total{job="cloud_agent"}[1h]))
-
sum(increase(cloud_agent_semantic_cache_estimated_saved_cost_usd_total{job="cloud_agent"}[1h]))
```

## 10. Dashboard 建议

建议新增 row：

```text
LLM Cost & Cache Benefit
```

面板：

- LLM token rate by operation / model / token_type。
- LLM estimated cost per hour。
- Semantic cache estimated saved calls。
- Semantic cache estimated saved tokens。
- Semantic cache estimated saved cost。
- Net estimated LLM cost。

所有标题中保留 `estimated`，避免误认为账单真实成本。

## 11. 告警建议

第一批不建议直接加成本告警。原因：

- 价格配置可能不完整。
- token 统计来源可能由不同 LLM SDK 返回，字段需要适配。
- cache benefit 是估算值，不适合初期告警。

等数据稳定后可以考虑：

```text
CloudAgentLLMEstimatedCostHourlyHigh
CloudAgentLLMTokenBurst
CloudAgentCacheEstimatedBenefitDropped
```

## 12. 测试计划

后续实现时至少覆盖：

1. `llm_call` 带 prompt/completion token 时输出 token counters。
2. `llm_call` 带 estimated cost 时输出 cost counter。
3. token / cost 负数被忽略。
4. 缺失 model 时使用 `unknown`。
5. metrics 输出不包含 prompt、completion、query、matched_question、对话内容。
6. cache benefit event 输出 saved call / token / cost counters。
7. `test_observability_ops.py` 增加 cost dashboard 静态约束。
8. pricing 配置缺失时不影响请求链路。

## 13. 上线顺序

建议分四步：

1. 只扩展 `llm_call` EventLog 和 metrics helper，记录 token 数，不记录成本。
2. 增加价格配置读取和 `estimated_cost_usd` 指标。
3. 增加 cache benefit 估算 event 和 metrics。
4. 最后更新 Grafana dashboard；先不加告警。当前 dashboard 静态 JSON 已完成。

## 14. 当前状态

已完成：

- `llm_call` event 如果带 `prompt_tokens`，会输出 `cloud_agent_llm_prompt_token_total` 和 `cloud_agent_llm_token_total{token_type="prompt"}`。
- `llm_call` event 如果带 `completion_tokens`，会输出 `cloud_agent_llm_completion_token_total` 和 `cloud_agent_llm_token_total{token_type="completion"}`。
- `CLOUD_AGENT_LLM_PRICING_CONFIG` 可指向 YAML / JSON 价格配置。
- 如果配置中存在对应 model 的 prompt / completion 单价，会输出 `cloud_agent_llm_estimated_cost_usd_total`。
- `cache_benefit` event 如果带 `estimated_saved_calls`，会输出 `cloud_agent_semantic_cache_estimated_saved_call_total`。
- `cache_benefit` event 如果带 `estimated_saved_prompt_tokens` / `estimated_saved_completion_tokens`，会输出 `cloud_agent_semantic_cache_estimated_saved_token_total{token_type="prompt|completion"}`。
- `cache_benefit` event 如果带 `estimated_saved_cost_usd`，会输出 `cloud_agent_semantic_cache_estimated_saved_cost_usd_total`。
- `stream_chat` 命中 semantic cache 时，会自动 emit `cache_benefit` event，并记录 `estimated_saved_calls=1`。
- Semantic Cache 默认 collection 已切换为 `qa_semantic_cache_v2`，也可通过 `CLOUD_AGENT_SEMANTIC_CACHE_COLLECTION` 覆盖。
- `set_cache()` 已支持可选 `estimated_prompt_tokens`、`estimated_completion_tokens`、`estimated_cost_usd` 和 `model` 元数据。
- `get_cache()` 命中带元数据的缓存条目时，会返回 token / cost 元数据。
- `stream_chat` cache hit 如果拿到 token / cost 元数据，会透传为 `cache_benefit` event 的 saved token / saved cost 字段。
- cache hit 路径不会凭空估算 saved token / saved cost；没有元数据时仍只记录 `estimated_saved_calls=1`。
- token 负数会被忽略。
- cache benefit 负数估算值会被忽略。
- 缺失或可疑 `model` 会归一化为 `unknown`。
- 配置缺失或解析失败时不输出 cost 指标，不影响请求链路。
- `test_metrics.py` 覆盖 token 输出、负数忽略、model 归一、estimated cost、cache benefit 和敏感文本不泄漏。
- `test_semantic_cache.py` 覆盖 cache metadata 写入、缺失元数据 sentinel、exact hit / semantic hit 元数据读回。
- `test_event_log.py` 覆盖 cache hit 带元数据时 `cache_benefit` 输出 saved token / saved cost，且不泄漏 `matched_question` 或模型名。
- Grafana dashboard 已新增 `LLM Cost & Cache Benefit` row，展示 token rate、estimated cost/hour、estimated saved calls/tokens/cost 和 net estimated cost。
- `test_observability_ops.py` 已约束新增 row、面板标题和成本 / 缓存收益 PromQL 指标，并继续禁止 dashboard PromQL 使用敏感字段。

仍未完成：

- 非 cache hit 路径尚未把真实 LLM usage 或明确估算 usage 写入 semantic cache 元数据。
- Prometheus alert rules。
- Docker / Grafana 实际启动验收已于 2026-07-13 通过；当前 fake graph smoke 不产生真实 LLM usage、MCP tool 或 cost 样本，仍需在真实业务流量环境观察对应面板。

下一步如果继续成本方向，建议把非 cache hit 的 LLM usage 或明确估算 usage 接入 `set_cache()`，让后续 cache hit 可以产生 saved token / saved cost；仍不要同一步增加告警。
