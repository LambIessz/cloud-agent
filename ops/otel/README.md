# OpenTelemetry Trace 本地导出说明

更新时间：2026-05-26

本文档用于验证当前最小 Trace PoC：Web `stream_chat` request span。

当前代码只创建一个 span：

```text
cloud_agent.stream_chat
```

该 span 默认关闭，只有设置 `CLOUD_AGENT_TRACE_ENABLED=true` 时才会尝试创建。如果没有安装 OpenTelemetry API / SDK，当前实现会自动 no-op，不影响请求链路。

## 1. 当前 Trace 边界

当前 PoC 只覆盖：

- Web `stream_chat` 请求级 span。
- span 名称：`cloud_agent.stream_chat`。
- tracer 名称：`cloud_agent.web`。

当前不会覆盖：

- Agent 子 span。
- MCP Tool 子 span。
- Redis / Milvus / Semantic Cache 子 span。
- LLM 子 span。
- background_extract 子 span。

当前 span 只记录低基数字段：

```text
component=chat_service
operation=stream_chat
identity.source
cache.status
request.status
error.type
```

如果同时显式启用：

```text
CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true
```

span 会额外记录一个用于排障关联的属性：

```text
request.id
```

该属性默认不写入；即使启用，也只允许写入 `request.id`，不得写入用户身份、用户输入、模型输出、异常 message 或对话内容。

禁止记录：

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
对话内容
偏好内容
```

异常时只记录异常类型，例如 `RuntimeError`，不记录异常 message 或堆栈。

## 2. 可选依赖

当前项目没有把 OpenTelemetry SDK / exporter 写入 `requirements.txt`，避免影响默认安装和现有测试。需要本地验证 Trace 导出时，可以临时安装：

```powershell
python -m pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation opentelemetry-exporter-otlp
```

如果只验证 ConsoleSpanExporter，`opentelemetry-exporter-otlp` 不是必需项；如果要导出到 OTLP collector，则需要它。

## 3. ConsoleSpanExporter 验证

适合本地最小验证，不需要启动 collector。

如果本机已有 `opentelemetry-sdk`，可以先运行不启动 FastAPI 的最小 smoke：

```powershell
python ops\otel\console_trace_smoke.py
```

该脚本直接配置 `ConsoleSpanExporter`，调用现有 `start_stream_chat_span()`，并验证：

- 默认不写 `request.id`。
- 同时启用 `CLOUD_AGENT_TRACE_ENABLED=true` 和 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` 后写入 `request.id`。
- 错误路径只写 `error.type`，不写异常 message。
- 导出内容不包含用户身份、query、prompt、completion、matched_question 或偏好内容。

当前本机验证结果：

```json
{
  "status": "PASS",
  "span_count": 3,
  "span_names": [
    "cloud_agent.stream_chat",
    "cloud_agent.stream_chat",
    "cloud_agent.stream_chat"
  ],
  "default_has_request_id": false,
  "enabled_request_id": "req_trace_smoke",
  "error_type": "RuntimeError",
  "forbidden_hits": []
}
```

在 PowerShell 中执行：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\cloud_agent\app
$env:CLOUD_AGENT_TRACE_ENABLED="true"
$env:OTEL_SERVICE_NAME="cloud_agent"
$env:OTEL_TRACES_EXPORTER="console"
opentelemetry-instrument python -m uvicorn app_main:app --host 0.0.0.0 --port 5000
```

然后请求 Web chat 接口，例如使用前端，或用一个不包含敏感信息的本地调试请求。

控制台应能看到 span，重点检查：

```text
name: cloud_agent.stream_chat
attributes.component: chat_service
attributes.operation: stream_chat
attributes.identity.source: debug_request 或 authenticated
attributes.cache.status: hit / miss / degraded / unavailable
attributes.request.status: success / error
attributes.error.type: 仅异常类型，异常时出现
```

默认情况下不应出现 `attributes.request.id`。如果需要验证 request_id 与 Trace 后端的关联，可以额外设置：

```powershell
$env:CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED="true"
```

此时只允许出现：

```text
attributes.request.id: req_xxxxxxxxxxxxxxxx
```

同时确认控制台输出中不出现：

```text
明文 user_id
user_id_hash
tenant_id
request_id
query / prompt / completion
对话内容
异常 message
```

## 4. OTLP Exporter 验证

适合接入 OpenTelemetry Collector、Jaeger、Tempo 等后端。

当前本机 Docker Desktop Linux engine 返回 500 / timeout，无法可靠启动容器化 Collector / Jaeger / Tempo。因此先补充了一个不依赖 Docker 的 OTLP gRPC 后端 smoke：

```powershell
python ops\otel\otlp_backend_smoke.py
```

该脚本会在当前进程内启动一个符合 OTLP gRPC `TraceService` 的 receiver，使用真实 `OTLPSpanExporter` 把 `cloud_agent.stream_chat` span 发送到本地 receiver，并验证：

- 后端收到 3 个 `cloud_agent.stream_chat` span。
- 默认不写 `request.id`。
- 显式开启 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` 后，后端收到 `request.id=req_otlp_trace`。
- 错误路径只包含 `error.type=RuntimeError`，不包含异常 message。
- 后端接收到的 span attributes 不包含用户身份、query、prompt、completion、matched_question 或偏好内容。

当前本机验证结果：

```json
{
  "status": "PASS",
  "backend": "in_process_otlp_grpc_receiver",
  "received_span_count": 3,
  "span_names": [
    "cloud_agent.stream_chat",
    "cloud_agent.stream_chat",
    "cloud_agent.stream_chat"
  ],
  "default_has_request_id": false,
  "enabled_request_id": "req_otlp_trace",
  "error_type": "RuntimeError",
  "forbidden_hits": []
}
```

这验证了应用侧 OTLP gRPC exporter 到后端 receiver 的协议路径，但不等同于 Collector / Jaeger / Tempo 的容器化验收。后者仍需要 Docker 或远端环境可用。

示例环境变量：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\cloud_agent\app
$env:CLOUD_AGENT_TRACE_ENABLED="true"
$env:OTEL_SERVICE_NAME="cloud_agent"
$env:OTEL_TRACES_EXPORTER="otlp"
$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
$env:OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
opentelemetry-instrument python -m uvicorn app_main:app --host 0.0.0.0 --port 5000
```

如果 collector 使用 HTTP/protobuf，可以改成：

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
$env:OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
```

后端中应能看到服务名：

```text
cloud_agent
```

以及 span：

```text
cloud_agent.stream_chat
```

## 5. 调试建议

如果没有看到 span，按顺序检查：

1. 是否设置了 `CLOUD_AGENT_TRACE_ENABLED=true`。
2. 是否通过 `opentelemetry-instrument` 启动应用。
3. 是否安装了 `opentelemetry-api` 和 `opentelemetry-sdk`。
4. Console exporter 是否设置了 `OTEL_TRACES_EXPORTER=console`。
5. OTLP exporter 的 endpoint、protocol 和 collector 端口是否匹配。
6. 是否实际发起了 `/api/chat` 请求并完整消费 SSE 响应。

如果 span 存在但状态不符合预期，先对照 EventLog：

```text
[EventLog] request_start
[EventLog] request_end
```

Trace PoC 不替代 EventLog 和 metrics；当前它只用于证明请求级 span 生命周期可以工作。

## 6. 后续扩展边界

后续如果继续推进 Trace，建议按以下顺序小步扩展：

1. 为本地 Console / OTLP 导出补自动化 smoke test 或手动验收清单。
2. `request_id` 与 trace 的关联方式已收口为显式开关 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED`，仍不得把 request_id 写入 Prometheus label。
3. 只在已有 request span 稳定后，再考虑 Agent 子 span。
4. Tool / Memory / LLM 子 span 要单独设计字段白名单，避免泄漏 prompt、completion、query、异常 message 或偏好内容。

`request_id` 与 Trace 的关联边界见：

```text
ops/otel/request_id_trace_correlation.md
```

## 7. 自动化 Smoke Test

已补充不依赖真实 OpenTelemetry SDK / exporter 的轻量 smoke test：

```text
cloud_agent/agent/test/test_tracing.py
```

该测试用 fake OpenTelemetry module 验证：

- `CLOUD_AGENT_TRACE_ENABLED` 未开启时，Trace wrapper 为 no-op。
- 开启后使用 tracer `cloud_agent.web`。
- span 名称为 `cloud_agent.stream_chat`。
- 成功路径只记录 `component`、`operation`、`identity.source`、`cache.status`、`request.status`。
- 默认不记录 `request.id`；只有同时开启 `CLOUD_AGENT_TRACE_ENABLED=true` 和 `CLOUD_AGENT_TRACE_REQUEST_ID_ENABLED=true` 且 request_id 非空时才记录 `request.id`。
- 错误路径只记录异常类型到 `error.type`，不记录异常 message。
- span context manager 退出时不传入异常对象和 traceback，避免 OpenTelemetry 自动记录异常 message 或堆栈。
- 不记录 `request_id`、`user_id`、`user_id_hash`、`tenant_id`、`session_id`、`thread_id`、`conversation_id`、`query`、`prompt`、`completion`、`message`、`matched_question`。

执行命令：

```powershell
python -m pytest cloud_agent\agent\test\test_tracing.py -q
```
