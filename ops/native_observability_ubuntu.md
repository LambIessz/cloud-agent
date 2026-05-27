# Ubuntu 原生 Prometheus + Grafana 验收说明

更新时间：2026-05-25

本文档记录 Docker Desktop 或 Docker Hub 拉取不可用时，如何在 Ubuntu VM 中使用原生 Prometheus + Grafana 验收 `cloud_agent` 的 `/api/metrics`、alert rules 和 Grafana dashboard。

本说明只用于本地开发和验收，不修改应用运行时代码。

## 1. 当前已验证状态

基于 2026-05-24 PDT 的 Ubuntu VM 实测输出，当前原生观测栈已达到可验收状态：

| 项目 | 状态 | 依据 |
| --- | --- | --- |
| Grafana service | PASS | `systemctl status grafana-server` 为 `active (running)` |
| Grafana health | PASS | `curl http://localhost:3000/api/health` 返回 `database: ok` |
| Prometheus service | PASS | `systemctl status prometheus` 为 `active (running)` |
| Prometheus readiness | PASS | `curl http://localhost:9090/-/ready` 返回 `Prometheus is Ready.` |
| Prometheus target | PASS | `/api/v1/targets` 中 `cloud_agent` health 为 `up` |
| Alert rules | PASS | `promtool check rules /etc/prometheus/rules/cloud_agent_alerts.yml` 成功，Prometheus UI 显示 `cloud_agent.rules` |
| Grafana datasource | PASS | `/etc/grafana/provisioning/datasources/prometheus.yml` 指向 `http://localhost:9090` |
| Grafana dashboard | PASS | `/var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json` 已存在并可加载 |
| FastAPI metrics | PASS | `curl http://localhost:5000/api/metrics` 返回 Prometheus text format 和 `cloud_agent_*` 样本 |

当前看到部分 Grafana 面板显示 `No data` 是预期现象，不等于观测栈失败。原因是对应业务路径还没有产生指标：

- `LLM` 面板需要实际产生 `llm_call` 事件。
- `MCP Tool` 面板需要实际调用 MCP tool。
- `MCP registry initialize` 面板需要触发 registry 初始化事件。
- `Semantic cache hit rate` 需要 semantic cache 正常命中；当前如果只有 `unavailable` 或 miss，可能显示 `No data`。
- `LLM Cost & Cache Benefit` 面板需要 LLM token / estimated cost 或 cache benefit 指标。

## 2. 推荐目录映射

项目内源文件：

```text
ops/prometheus/cloud_agent_alerts.yml
ops/grafana/cloud_agent_overview_dashboard.json
ops/grafana/provisioning/datasources/prometheus.yml
ops/grafana/provisioning/dashboards/cloud_agent.yml
```

Ubuntu 原生安装后的目标路径：

```text
/etc/prometheus/rules/cloud_agent_alerts.yml
/etc/grafana/provisioning/datasources/prometheus.yml
/etc/grafana/provisioning/dashboards/cloud_agent.yml
/var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json
```

原生安装时 Prometheus 抓取目标应使用：

```text
localhost:5000
```

不要使用 Docker 场景下的：

```text
host.docker.internal:5000
```

## 3. Prometheus 原生配置

确保 `/etc/prometheus/prometheus.yml` 至少包含：

```yaml
rule_files:
  - /etc/prometheus/rules/cloud_agent_alerts.yml

scrape_configs:
  - job_name: cloud_agent
    metrics_path: /api/metrics
    static_configs:
      - targets:
          - localhost:5000
```

复制告警规则：

```bash
sudo mkdir -p /etc/prometheus/rules
sudo cp ~/企业级ai应用/ops/prometheus/cloud_agent_alerts.yml /etc/prometheus/rules/cloud_agent_alerts.yml
sudo chown -R prometheus:prometheus /etc/prometheus/rules
```

校验并重启：

```bash
promtool check rules /etc/prometheus/rules/cloud_agent_alerts.yml
sudo systemctl restart prometheus
sudo systemctl status prometheus --no-pager
curl http://localhost:9090/-/ready
```

检查 target：

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -A 20 cloud_agent
```

预期看到：

```text
"health": "up"
"lastError": ""
"scrapeUrl": "http://localhost:5000/api/metrics"
```

## 4. Grafana 原生配置

Prometheus datasource：

```bash
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo tee /etc/grafana/provisioning/datasources/prometheus.yml > /dev/null <<'YAML'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true
YAML
```

Dashboard provider：

```bash
sudo mkdir -p /etc/grafana/provisioning/dashboards
sudo tee /etc/grafana/provisioning/dashboards/cloud_agent.yml > /dev/null <<'YAML'
apiVersion: 1

providers:
  - name: Cloud Agent
    orgId: 1
    folder: Cloud Agent
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
YAML
```

Dashboard JSON：

```bash
sudo mkdir -p /var/lib/grafana/dashboards
sudo cp ~/企业级ai应用/ops/grafana/cloud_agent_overview_dashboard.json /var/lib/grafana/dashboards/cloud_agent_overview_dashboard.json
sudo chown -R grafana:grafana /var/lib/grafana/dashboards
```

重启并检查：

```bash
sudo systemctl restart grafana-server
sudo systemctl status grafana-server --no-pager
curl http://localhost:3000/api/health
```

访问：

```text
http://localhost:3000
```

默认账号：

```text
admin / admin
```

## 5. 业务指标验收

先确认 FastAPI 正在运行：

```bash
curl http://localhost:5000/api/metrics
```

如果只返回空白或极少内容，先通过前端、CLI 或接口触发一次 `/api/chat`。触发后再次检查：

```bash
curl http://localhost:5000/api/metrics | head -100
```

至少应看到部分指标族，例如：

```text
# HELP cloud_agent_event_total ...
# TYPE cloud_agent_event_total counter
# HELP cloud_agent_request_total ...
# TYPE cloud_agent_request_total counter
# HELP cloud_agent_route_total ...
# TYPE cloud_agent_route_total counter
```

Prometheus 查询：

```bash
curl -s -G http://localhost:9090/api/v1/query --data-urlencode 'query=up{job="cloud_agent"}' | python3 -m json.tool
curl -s -G http://localhost:9090/api/v1/query --data-urlencode 'query=cloud_agent_request_total' | python3 -m json.tool
curl -s -G http://localhost:9090/api/v1/query --data-urlencode 'query=cloud_agent_event_total' | python3 -m json.tool
```

也可以直接使用仓库内的验收脚本收集 Ubuntu VM / CI 结果：

```bash
cd ~/企业级ai应用
bash ops/ubuntu_ci_acceptance.sh
```

如果服务地址不是默认值，可覆盖：

```bash
CLOUD_AGENT_BASE_URL=http://127.0.0.1:5000 \
PROMETHEUS_URL=http://127.0.0.1:9090 \
GRAFANA_URL=http://127.0.0.1:3000 \
bash ops/ubuntu_ci_acceptance.sh
```

脚本输出目录为 `.acceptance/<timestamp>/`，核心结论在 `summary.tsv`。`RUN_CHAT_SMOKE=1` 会额外发起一轮合成 `/api/chat` 流量，但不会保存请求正文、响应正文或完整 metrics body；如果生产认证开启，需要通过 `CHAT_SMOKE_AUTH_USER` / `CHAT_SMOKE_AUTH_TENANT` 提供网关注入身份。

## 6. Dashboard No Data 判读

Grafana 面板可按下面方式判读：

| 面板区域 | 有数据说明 | `No data` 常见原因 |
| --- | --- | --- |
| Request | `/api/chat` 已完成请求并写入 request metrics | 没有请求流量或时间窗口太短 |
| Routing | Orchestrator 已产生 `route_decision` | 请求没有进入 orchestrator 或时间窗口太短 |
| Cache & Memory | cache / Redis / Milvus / background extract 事件已产生 | 依赖未触发、无命中、无降级或时间窗口太短 |
| LLM | 已产生 `llm_call` event | 当前路由走确定性规则，未调用 LLM，或 LLM metrics 尚无样本 |
| MCP Tool | 已调用 MCP tool | 尚未触发 Billing / FinOps / Promotion / Recommendation 工具路径 |
| MCP Registry | registry 初始化事件已产生 | 尚未触发 MCP registry 初始化或预热 |
| LLM Cost & Cache Benefit | LLM token/cost 或 cache benefit 指标已产生 | 尚无 token usage、价格配置或 cache hit benefit 样本 |

开发验收时不要求所有面板立刻都有数据。核心验收优先级是：

1. Prometheus 能抓取 `cloud_agent`，target 为 `up`。
2. `/api/metrics` 输出真实 `cloud_agent_*` 指标。
3. `cloud_agent.rules` 成功加载。
4. Grafana datasource 和 dashboard 正常加载。
5. 有业务流量后 Request / Routing 至少出现数据。
6. 敏感字段不出现在 metrics、alert rules 和 dashboard PromQL 中。

## 7. 敏感字段边界

原生安装不改变观测性安全边界：

- 不把 `request_id`、明文 `user_id`、`user_id_hash`、`tenant_id`、`session_id`、`thread_id`、`conversation_id` 放入 Prometheus label。
- 不记录 prompt、completion、query、matched_question、对话内容、偏好内容。
- 不记录异常 message 或堆栈到 metrics / alert rules / dashboard。
- Grafana JSON 中的 `query` 字段是 Grafana schema 字段，不代表业务 query 内容。

可选检查：

```bash
grep -R -n -E 'request_id|user_id|user_id_hash|tenant_id|session_id|thread_id|conversation_id|prompt|completion|matched_question' ~/企业级ai应用/ops/prometheus ~/企业级ai应用/ops/grafana
```

发现 `token`、`token_type`、`completion_token_total` 这类指标名时，需要结合上下文判断；它们表示 token 数值分类，不是模型输出文本。

## 8. 后续建议

当前原生 Prometheus + Grafana 已可作为本机验收路径。后续不建议继续卡 Docker 镜像拉取问题，可以保留 Docker compose 作为可选方案，同时把 Ubuntu 原生安装作为当前主路径。

下一步工程化建议：

1. 用当前原生观测栈继续补齐不同业务路径的真实样本，尤其是 LLM、MCP Tool、MCP Registry 和 semantic cache hit。
2. 暂不新增成本类告警，先观察 estimated cost / cache benefit 面板在真实样本下是否稳定。
3. 若继续 Trace，只保持最小 `stream_chat` request span，不扩展全链路子 span。
