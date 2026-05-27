# 本地可观测性栈启动说明

更新时间：2026-05-25

本文档用于本地启动 Prometheus + Grafana，验证 `cloud_agent` 已有 `/api/metrics`、Grafana dashboard 和 Prometheus alert rules。

本说明只覆盖本地观测栈，不修改应用运行时代码。

## 1. 文件清单

```text
ops/docker-compose.observability.yml
ops/prometheus/prometheus.yml
ops/prometheus/cloud_agent_alerts.yml
ops/grafana/cloud_agent_overview_dashboard.json
ops/grafana/provisioning/datasources/prometheus.yml
ops/grafana/provisioning/dashboards/cloud_agent.yml
ops/observability_checklist.md
ops/ubuntu_ci_acceptance.sh
```

## 2. 启动 cloud_agent API

先启动 FastAPI 服务，确保 `/api/metrics` 可访问。

PowerShell 示例：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\cloud_agent\app
python -m uvicorn app_main:app --host 0.0.0.0 --port 5000
```

验证：

```powershell
Invoke-WebRequest http://localhost:5000/api/metrics
```

期望返回 Prometheus text format。如果还没有业务流量，响应可能只有少量或没有指标样本，这是正常的。

## 3. 启动 Prometheus + Grafana

另开一个 PowerShell：

```powershell
cd C:\Users\LambIessz\Desktop\企业级ai应用\ops
docker compose -f docker-compose.observability.yml up
```

如果要后台运行：

```powershell
docker compose -f docker-compose.observability.yml up -d
```

停止：

```powershell
docker compose -f docker-compose.observability.yml down
```

如需同时清理本地 Prometheus / Grafana 数据卷：

```powershell
docker compose -f docker-compose.observability.yml down -v
```

如果 Docker Desktop、Docker Hub 镜像拉取或 `host.docker.internal` 不可用，可以跳过本节，改用 Ubuntu VM 原生安装方式：

```text
ops/native_observability_ubuntu.md
```

当前 Ubuntu VM 原生路径已经完成一次真实验收：Grafana health 正常、Prometheus ready、`cloud_agent` target 为 `up`、`cloud_agent.rules` 已加载、dashboard JSON 已加载、`/api/metrics` 有真实 `cloud_agent_*` 样本。

如果已经在 Ubuntu VM 或 CI runner 上启动了 FastAPI、Prometheus 和 Grafana，可以用验收脚本一次性收集关键结果：

```bash
bash ops/ubuntu_ci_acceptance.sh
```

默认会执行 canonical pytest、`promtool`（如果已安装）、`/healthz`、`/readyz`、`/api/metrics` 敏感字段扫描、Prometheus 查询和 Grafana health。脚本把结果写入 `.acceptance/<timestamp>/summary.tsv`，该目录已加入 `.gitignore`。

如需额外触发一轮合成 `/api/chat` 流量，可显式开启：

```bash
RUN_CHAT_SMOKE=1 bash ops/ubuntu_ci_acceptance.sh
```

脚本不会归档 chat 响应正文或完整 metrics body；Grafana dashboard API 验证需要设置 `GRAFANA_USER` / `GRAFANA_PASSWORD`，否则该项会标记为 `BLOCKED`。

## 4. 访问地址

Prometheus：

```text
http://localhost:9090
```

Grafana：

```text
http://localhost:3000
```

默认 Grafana 登录：

```text
admin / admin
```

Grafana 会自动 provision：

- datasource：`Prometheus`
- dashboard folder：`Cloud Agent`
- dashboard：`Cloud Agent Overview`

## 5. Prometheus 验证

打开：

```text
http://localhost:9090/targets
```

检查 `cloud_agent` target 是否为 `UP`。

Prometheus 容器内默认抓取：

```text
host.docker.internal:5000/api/metrics
```

这适合 Windows Docker Desktop。若运行环境不支持 `host.docker.internal`，需要修改：

```text
ops/prometheus/prometheus.yml
```

把 target 改成宿主机可从容器访问的地址。

可在 Prometheus UI 查询：

```promql
up{job="cloud_agent"}
```

或：

```promql
cloud_agent_request_total
```

如果没有请求流量，部分 `cloud_agent_*` 指标可能暂时不存在。可以先调用 `/api/chat` 产生一次请求，再刷新。

## 6. Alert Rules 验证

Prometheus 会加载：

```text
/etc/prometheus/rules/cloud_agent_alerts.yml
```

对应本地文件：

```text
ops/prometheus/cloud_agent_alerts.yml
```

打开：

```text
http://localhost:9090/rules
```

应能看到 rule group：

```text
cloud_agent.rules
```

当前包含 15 条告警，详见：

```text
ops/prometheus/cloud_agent_alerts.yml
```

如果本地安装了 `promtool`，可以在项目根目录执行：

```powershell
promtool check rules ops\prometheus\cloud_agent_alerts.yml
```

## 7. Grafana 验证

打开：

```text
http://localhost:3000
```

登录后进入：

```text
Dashboards -> Cloud Agent -> Cloud Agent Overview
```

如果 dashboard 没有数据，按顺序检查：

1. FastAPI 是否在 `localhost:5000` 运行。
2. `http://localhost:5000/api/metrics` 是否可访问。
3. Prometheus `Targets` 中 `cloud_agent` 是否为 `UP`。
4. dashboard 变量 `job` 是否为 `cloud_agent`。
5. 是否已经产生过 `/api/chat` 请求流量。

说明：部分面板 `No data` 不一定是错误。LLM、MCP Tool、MCP Registry、semantic cache hit、成本与缓存收益面板需要对应业务路径先产生样本。只要 Prometheus target 为 `UP`，`/api/metrics` 有真实 `cloud_agent_*` 指标，Request / Routing 等已触发路径能显示数据，本地观测栈就已经可用于继续验收。

## 8. 安全和边界

当前本地观测栈遵守以下边界：

- Prometheus alert rules 不使用 `request_id`、`user_id`、`user_id_hash`、`tenant_id` 等高基数或敏感 label。
- Dashboard 只基于现有聚合指标展示。
- 当前延迟同时包含平均延迟指标和 `duration_ms` histogram；dashboard 展示平均延迟以及 p95 / p99 面板。
- Dashboard 已包含 `LLM Cost & Cache Benefit` row；其中成本和缓存收益均为 estimated，仅用于工程观测，不等同于账单。
- 本地 Grafana 默认密码 `admin / admin` 只适合开发环境，不能用于生产。
- 本地 compose 文件不包含 OpenTelemetry Collector；Trace Console / OTLP gRPC smoke 和后端验收说明见 `ops/otel/README.md`。

## 9. 常见问题

### Prometheus target DOWN

优先检查 FastAPI 是否监听 `0.0.0.0:5000`，而不是只监听容器不可访问的地址。

### Grafana dashboard 没有数据

先在 Prometheus 中查询：

```promql
up{job="cloud_agent"}
```

如果 `up` 为 1，但业务指标为空，通常是还没有触发对应 EventLog / metrics。调用一次 `/api/chat` 后再看。

### 告警规则没有出现

检查 Prometheus 容器日志，确认 rule file 是否加载成功：

```powershell
docker logs cloud_agent_prometheus
```

也可以进入 Prometheus UI 的 `Status -> Runtime & Build Information` 和 `Rules` 页面查看。

## 10. 手动验收 Checklist

如果本地 Docker、端口、镜像拉取或权限问题导致无法一次性跑完整个观测栈，可以先使用下面的 checklist 记录每个验收点的状态和阻塞原因：

```text
ops/observability_checklist.md
```

该 checklist 覆盖：

- `/api/metrics` 可访问性。
- Prometheus target `cloud_agent` 是否为 `UP`。
- Prometheus alert rules 是否加载。
- Grafana datasource 和 dashboard 是否自动加载。
- Trace Console / OTLP exporter 是否能看到 `cloud_agent.stream_chat` span。
- metrics、dashboard、alert rules、Trace 是否泄露敏感字段。
