# p95 / p99 延迟 Histogram 指标设计

更新时间：2026-05-24

本文档用于设计 p95 / p99 延迟指标的最小实现方案。当前 metrics helper 已输出 request / LLM / MCP tool 的 `duration_ms` histogram；Prometheus rules 和 Grafana dashboard 已完成 p95 / p99 小步接入。

## 1. 背景

当前 `/api/metrics` 中的延迟指标只有 `_count` 和 `_sum`：

```text
cloud_agent_request_latency_ms_count
cloud_agent_request_latency_ms_sum
cloud_agent_llm_latency_ms_count
cloud_agent_llm_latency_ms_sum
cloud_agent_tool_latency_ms_count
cloud_agent_tool_latency_ms_sum
cloud_agent_event_latency_ms_count
cloud_agent_event_latency_ms_sum
```

这些指标可以计算平均延迟，但不能计算 p95 / p99，因为没有 bucket 序列。

## 2. 设计目标

后续实现 histogram 时应满足：

- 支持 request / LLM / MCP tool 三类关键延迟的 p95 / p99。
- 保留现有 `_latency_ms_count` / `_latency_ms_sum`，避免破坏现有 dashboard、alert rules 和测试。
- 不引入高基数或敏感 label。
- 不记录 request_id、user_id、user_id_hash、tenant_id、session_id、thread_id、conversation_id。
- 不记录 query、prompt、completion、message、matched_question、对话内容或偏好内容。
- 不强制引入 `prometheus-client`，优先延续当前轻量 metrics helper。
- bucket 边界固定，避免运行时动态扩展时间序列。

## 3. 命名策略

不要把 histogram 直接命名为现有 `cloud_agent_request_latency_ms`。

原因：当前已经输出以下 counter family：

```text
# TYPE cloud_agent_request_latency_ms_count counter
# TYPE cloud_agent_request_latency_ms_sum counter
```

如果新增 Prometheus 标准 histogram：

```text
# TYPE cloud_agent_request_latency_ms histogram
cloud_agent_request_latency_ms_bucket{le="..."} ...
cloud_agent_request_latency_ms_count ...
cloud_agent_request_latency_ms_sum ...
```

会与现有 `cloud_agent_request_latency_ms_count` / `_sum` 样本名重叠，容易造成 scrape 或语义冲突。

因此建议新增独立 metric family，使用 `duration_ms` 命名：

```text
cloud_agent_request_duration_ms
cloud_agent_llm_duration_ms
cloud_agent_tool_duration_ms
```

Prometheus exposition 示例：

```text
# HELP cloud_agent_request_duration_ms Request duration histogram in milliseconds.
# TYPE cloud_agent_request_duration_ms histogram
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="100"} 3
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="250"} 8
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="+Inf"} 10
cloud_agent_request_duration_ms_count{component="chat_service",operation="stream_chat",status="success"} 10
cloud_agent_request_duration_ms_sum{component="chat_service",operation="stream_chat",status="success"} 1840
```

## 4. 指标范围

第一批只建议覆盖三类指标：

| Histogram | 来源事件 | 标签 | 用途 |
| --- | --- | --- | --- |
| `cloud_agent_request_duration_ms` | `request_end` | `component`, `operation`, `status` | Web 请求端到端 p95 / p99 |
| `cloud_agent_llm_duration_ms` | `llm_call` | `component`, `operation`, `status` | LLM 调用 p95 / p99 |
| `cloud_agent_tool_duration_ms` | `tool_call` | `component`, `operation`, `tool_name`, `status` | MCP 工具调用 p95 / p99 |

暂不建议第一批加入：

- `event_duration_ms`：事件类型太宽，p95 含义不稳定。
- Memory retrieve/save histogram：当前 memory 指标以降级和调用次数为主，延迟来源还不够统一。
- deep_research 分节点 histogram：应单独设计，避免和 cloud_agent 指标混在一起。

## 5. Bucket 边界

单位统一为毫秒。

Request bucket：

```python
REQUEST_DURATION_BUCKETS_MS = (
    100,
    250,
    500,
    1000,
    2000,
    3000,
    5000,
    10000,
    30000,
)
```

LLM bucket：

```python
LLM_DURATION_BUCKETS_MS = (
    250,
    500,
    1000,
    2000,
    5000,
    10000,
    20000,
    60000,
)
```

MCP tool bucket：

```python
TOOL_DURATION_BUCKETS_MS = (
    50,
    100,
    250,
    500,
    1000,
    2000,
    3000,
    5000,
    10000,
)
```

渲染时必须自动输出 `le="+Inf"` bucket。

## 6. 轻量实现草案

继续沿用当前 `metrics.py` 的进程内存储方式，新增 histogram 存储结构，不引入 `prometheus-client`。

建议新增：

```python
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...], float], float] = {}
_HISTOGRAM_SUMS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAM_COUNTS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
```

新增 API：

```python
observe_histogram(
    name: str,
    value: int | float,
    buckets: tuple[int | float, ...],
    labels: dict[str, Any] | None = None,
) -> None
```

行为：

- `value < 0` 时忽略。
- labels 继续经过 `_normalize_labels()`，沿用敏感 label 过滤。
- 对每个 `bucket >= value` 的 bucket 计数加 1。
- `+Inf` bucket 总是加 1。
- 对同一 label 组合增加 `_sum` 和 `_count`。
- bucket 的 `le` label 只能来自固定 bucket 或 `+Inf`，不能来自请求参数。

## 7. Prometheus 输出格式

每个 histogram family 输出一次 HELP / TYPE：

```text
# HELP cloud_agent_request_duration_ms Request duration histogram in milliseconds.
# TYPE cloud_agent_request_duration_ms histogram
```

然后输出 bucket / count / sum：

```text
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="100"} 1
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="250"} 2
cloud_agent_request_duration_ms_bucket{component="chat_service",operation="stream_chat",status="success",le="+Inf"} 3
cloud_agent_request_duration_ms_count{component="chat_service",operation="stream_chat",status="success"} 3
cloud_agent_request_duration_ms_sum{component="chat_service",operation="stream_chat",status="success"} 450
```

## 8. PromQL 示例

Request p95：

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(cloud_agent_request_duration_ms_bucket{job="cloud_agent",operation="stream_chat"}[5m])
  )
)
```

Request p99：

```promql
histogram_quantile(
  0.99,
  sum by (le) (
    rate(cloud_agent_request_duration_ms_bucket{job="cloud_agent",operation="stream_chat"}[5m])
  )
)
```

LLM p95 by operation：

```promql
histogram_quantile(
  0.95,
  sum by (operation, le) (
    rate(cloud_agent_llm_duration_ms_bucket{job="cloud_agent"}[5m])
  )
)
```

MCP tool p95 by tool：

```promql
histogram_quantile(
  0.95,
  sum by (tool_name, le) (
    rate(cloud_agent_tool_duration_ms_bucket{job="cloud_agent"}[5m])
  )
)
```

## 9. 告警建议

第一批可先新增 request p95 告警，不建议一口气替换现有平均延迟告警。

示例：

```yaml
- alert: CloudAgentRequestP95LatencyHigh
  expr: |
    histogram_quantile(
      0.95,
      sum by (le) (
        rate(cloud_agent_request_duration_ms_bucket{job="cloud_agent",operation="stream_chat"}[5m])
      )
    ) > 5000
  for: 10m
  labels:
    severity: warning
    service: cloud_agent
  annotations:
    summary: cloud_agent request p95 latency is high
    description: stream_chat p95 latency is above 5000 ms for 10 minutes.
```

保留现有平均延迟告警的原因：

- 平均延迟对总体趋势仍有价值。
- histogram 首次上线时需要观察 bucket 分布是否合适。
- p95 / p99 会受低流量窗口影响，需要先验证业务流量规模。

## 10. Dashboard 建议

新增 row 或在 Request / LLM / MCP Tool row 中增加：

- Request p95。
- Request p99。
- LLM p95 by operation。
- MCP tool p95 by tool_name。
- Bucket 分布热力图或表格，可选。

继续保留平均延迟面板，便于对照。

## 11. 测试计划

新增或扩展测试时至少覆盖：

1. `observe_histogram()` 会输出 bucket、count、sum。
2. `+Inf` bucket 总是存在。
3. bucket 是累积计数，符合 Prometheus histogram 语义。
4. HELP / TYPE 输出为 `histogram`，且不与现有 counter HELP / TYPE 冲突。
5. labels 继续过滤敏感字段。
6. 负数延迟不会写入 histogram。
7. `reset_metrics()` 同时清空 counters 和 histograms。
8. `test_observability_ops.py` 更新为允许新增 histogram_quantile 的新规则或新 dashboard 面板。

## 12. 上线顺序

建议分三步：

1. 只在 metrics helper 中新增 histogram 输出和测试，不改 dashboard / alert rules。
2. 手动或通过 `/api/metrics` 验证 bucket 序列存在且无敏感 label。
3. 再更新 Grafana dashboard 和 alert rules，新增 p95 / p99 面板和告警。

不建议第一步就删除或替换现有平均延迟指标。

## 13. 当前状态

已完成：

- metrics helper 新增进程内 histogram 存储和 Prometheus 渲染。
- `request_end` 会记录 `cloud_agent_request_duration_ms`。
- `llm_call` 会记录 `cloud_agent_llm_duration_ms`。
- `tool_call` 会记录 `cloud_agent_tool_duration_ms`。
- `test_metrics.py` 覆盖 bucket、count、sum、`+Inf`、敏感 label 过滤、负值忽略和 reset。
- Grafana dashboard 新增 `Latency Percentiles` row。
- Grafana dashboard 新增 Request p95、Request p99、LLM p95 by operation、MCP tool p95 by tool 面板。
- Prometheus alert rules 新增 `CloudAgentRequestP95LatencyHigh`、`CloudAgentLLMP95LatencyHigh`、`CloudAgentMCPToolP95LatencyHigh`。
- `test_observability_ops.py` 已限制 `histogram_quantile()` 只能出现在上述 p95/p99 面板和 p95 告警中。

运行验收状态：

- 2026-07-13 Windows Docker runtime acceptance 已实际启动 Prometheus 与 Grafana；target 为 `up`、request metric 有样本、Grafana health 与 dashboard API 通过，见 `.acceptance/20260713T072236Z/summary.tsv`。
- `promtool` 仍不可用，alert rules 只能通过 pytest 静态测试守住部分约束。

后续仍建议保留平均延迟面板和告警，用于和 p95 / p99 长尾延迟对照。
