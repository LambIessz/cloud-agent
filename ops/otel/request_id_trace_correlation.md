# request_id 与 Trace 关联设计

更新时间：2026-05-26

本文档用于说明 `request_id`、EventLog、metrics 与 OpenTelemetry Trace 的关联边界。当前已实现显式开关注入方案，默认仍不把 `request_id` 写入 Trace。

## 1. 背景

当前系统已经具备：

- Web `/api/chat` 每次请求生成 `request_id`。
- `request_id` 贯穿 `AgentState.metadata`、EventLog、ToolAudit、Degradation 和 SSE done。
- `/api/metrics` 已过滤高基数 / 敏感 label，不输出 `request_id`。
- Trace PoC 已创建最小 Web `stream_chat` request span：`cloud_agent.stream_chat`。

当前 Trace PoC 没有把 `request_id` 写入 span attribute，这是有意选择。`request_id` 虽然不是明文用户身份或对话内容，但它是高基数字段，也可能在日志系统、前端错误反馈、工单系统和 Trace 后端之间建立跨系统关联，因此需要先明确边界。

## 2. 目标

目标是让排障时可以从一个请求定位到相关观测数据，同时不扩大敏感信息和高基数字段的暴露面。

期望排障路径：

```text
用户反馈 / SSE done request_id
  -> EventLog request_start / request_end
  -> ToolAudit / Degradation
  -> 必要时定位到 Trace 后端中的同一次请求 span
  -> 再结合 Prometheus 聚合指标判断是否是系统性问题
```

非目标：

- 不把 `request_id` 加入 Prometheus label。
- 不把明文 `user_id`、`user_id_hash`、`tenant_id`、session、query、prompt、completion、matched_question 或对话内容写入 Trace。
- 不把异常 message 或堆栈写入当前最小 request span。
- 不在当前阶段扩展 Agent / Tool / Memory / LLM 子 span。

## 3. 风险分析

### 3.1 Prometheus 风险

`request_id` 是每次请求唯一值，如果进入 Prometheus label，会造成高基数时间序列，带来：

- 内存和存储成本快速增长。
- 查询性能下降。
- 告警规则不稳定。
- 指标系统被用作明细日志系统。

因此 `request_id` 必须继续被 metrics helper 过滤。

### 3.2 Trace 风险

Trace 系统可以承载比 metrics 更高的基数，但仍需要约束：

- Trace 后端可能长期保留数据。
- Trace 后端可能被更多研发、运维或排障系统访问。
- `request_id` 可与 EventLog / SSE / 工单系统关联。
- 如果未来把 `request_id` 与用户身份系统或工单系统连接，可能间接扩大可识别范围。

因此是否把 `request_id` 写入 span attribute 需要由部署环境决定，而不应默认开启。

### 3.3 日志风险

EventLog 当前已有 `request_id`，这是排障主索引。继续保留，但要保持：

- 不输出明文 `user_id`。
- 不输出异常 message。
- 不输出 prompt、completion、query、matched_question、对话内容或偏好内容。

## 4. 推荐方案

推荐采用“默认不写 Trace，显式开关注入”的方案。

### 4.1 默认行为

默认：

```text
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=false
```

行为：

- Trace span 不包含 `request_id`。
- EventLog 继续包含 `request_id`。
- SSE done 继续返回 `request_id`。
- Prometheus metrics 继续过滤 `request_id`。

### 4.2 可选行为

如生产排障确实需要 Trace 后端直接按请求检索，可以显式启用：

```text
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true
```

启用后仅允许写入一个 span attribute：

```text
request.id=<request_id>
```

命名使用 `request.id`，而不是 `request_id`，便于与 OpenTelemetry 语义属性风格保持一致，也避免和内部字段名混淆。

仍然禁止写入：

```text
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
对话内容
偏好内容
```

### 4.3 采样建议

如果开启 `request.id` trace attribute，建议同时使用采样：

- 开发 / 演示环境：可 100% 采样，便于验证。
- 生产环境：建议按比例采样或错误优先采样。
- 高流量环境：避免对所有成功请求长期保留 `request.id`。

采样配置应由 OpenTelemetry SDK / collector 侧控制，不写入业务代码。

## 5. 实施状态

当前实现只改了一个窄路径：

```text
cloud_agent/agent/core/workflow/tracing.py
cloud_agent/app/service/chat_service.py
cloud_agent/agent/test/test_event_log.py
```

实现内容：

1. 在 `tracing.py` 中新增环境变量判断：

```text
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED
```

2. `start_stream_chat_span()` 接收可选 `request_id`，但默认不写入 span。

3. 只有当以下条件同时满足时写入：

```text
CLOUD_AGENT_TRACE_ENABLED=true
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true
request_id 非空
```

4. 写入 attribute：

```text
request.id
```

5. 补测试：

- 默认不开启时，span attribute 不包含 `request.id`。
- 显式开启时，span attribute 包含 `request.id`。
- 即使显式开启，span attribute 也不包含 `user_id`、`user_id_hash`、`tenant_id`、query、prompt、completion、matched_question、异常 message。
- metrics 输出仍不包含 `request_id`。

## 6. 验收标准

实现前的设计验收：

- 明确 `request_id` 不进入 Prometheus label。
- 明确 Trace 默认不写 `request_id`。
- 明确写入 Trace 需要单独环境变量。
- 明确不记录用户身份、用户输入、模型输出和异常 message。

实现后的技术验收：

- 原有 EventLog / metrics / ToolAudit / Degradation 测试全部通过。
- `cloud_agent.stream_chat` span 默认不包含 `request.id`。
- 开启 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` 后才包含 `request.id`。
- `/api/metrics` 响应仍不包含 `request_id`。
- SSE done 继续返回 `request_id`。

## 7. 当前结论

当前阶段已完成显式开关的最小实现，但默认行为保持保守。

结论：

- 现有 EventLog 已能通过 `request_id` 做主要排障关联。
- metrics 已完成高基数字段过滤，不应扩大指标基数。
- `cloud_agent.stream_chat` span 默认不包含 `request.id`。
- 只有同时设置 `CLOUD_AGENT_TRACE_ENABLED=true` 和 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true`，并且 request_id 非空时，span 才包含 `request.id`。
- 是否允许 Trace 后端持有 `request.id` 仍应由部署环境和数据治理要求决定；生产环境建议配合采样和访问控制。

下一步更稳妥的选择：

1. 先验证 Console / OTLP Trace 导出链路。
2. 确认 Trace 后端访问权限和保留周期。
3. 如继续扩展 Trace 子 span，单独设计 Agent / Tool / Memory / LLM 字段白名单，不复用当前 request span 改动。
