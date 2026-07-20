from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from core.mcp.mcp_manager import get_global_mcp_tool_registry
from core.workflow.event_log import build_event, elapsed_ms, emit_event, now_ms
from core.workflow.request_context import ensure_request_metadata, get_request_id
from core.workflow.state import AgentState


logger = logging.getLogger(__name__)
INSTANCE_ID_RE = re.compile(r"\bi-[A-Za-z0-9][A-Za-z0-9-]{2,}\b")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


def _tool_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "content"):
        raw = getattr(raw, "content")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {
                "status": "error",
                "data": None,
                "user_message": "工具返回空内容",
                "error_code": "EMPTY_PAYLOAD",
            }
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "data": None,
                "user_message": "工具返回了无法解析的内容",
                "error_code": "BAD_TOOL_PAYLOAD",
            }
        if isinstance(parsed, dict):
            return parsed
        return {"status": "success", "data": parsed}
    return {
        "status": "error",
        "data": None,
        "user_message": "工具返回了不支持的数据类型",
        "error_code": "BAD_TOOL_PAYLOAD",
    }


def _extract_instance_id(query: str) -> str:
    match = INSTANCE_ID_RE.search(query)
    return match.group(0) if match else ""


class SupportAgentNode:
    """
    规则型首轮排查 + 只读诊断闭环。

    先给出稳定的人工排查步骤；如果能从用户输入里拿到 instance_id，
    就再拉取只读实例列表和 7 日监控摘要，把证据回填到回复和 metadata。
    """

    def __init__(
        self,
        *,
        tool_registry: Any | None = None,
        diagnostics_enabled: bool | None = None,
        tool_timeout_seconds: float | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self.diagnostics_enabled = (
            _env_flag("CLOUD_AGENT_SUPPORT_DIAGNOSTICS_ENABLED", True)
            if diagnostics_enabled is None
            else diagnostics_enabled
        )
        self.tool_timeout_seconds = (
            _env_float("CLOUD_AGENT_SUPPORT_TOOL_TIMEOUT_SECONDS", 5.0)
            if tool_timeout_seconds is None
            else tool_timeout_seconds
        )
        self._registry_config_path = (
            Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"
        )

    def _last_user_message(self, state: AgentState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return ""

        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            return str(last_msg[1])
        if hasattr(last_msg, "content"):
            return str(last_msg.content)
        return str(last_msg)

    def _current_checkpoint(self, state: AgentState) -> dict[str, Any] | None:
        metadata = state.get("metadata", {})
        checkpoint = metadata.get("human_checkpoint")
        return checkpoint if isinstance(checkpoint, dict) else None

    def _build_steps(self, query: str) -> list[str]:
        query_lower = query.lower()

        if "rds" in query_lower or "数据库" in query:
            return [
                "确认 RDS 实例状态正常，连接地址、端口、账号密码配置正确。",
                "检查 ECS 与 RDS 是否在同一 VPC，或者是否已经配置正确的网络互通方案。",
                "检查 RDS 白名单或安全访问策略是否允许 ECS 的内网 IP 访问。",
                "在 ECS 上使用 telnet、nc 或数据库客户端测试 RDS 端口连通性。",
                "如果连接数耗尽或查询变慢，再查看 RDS 监控、连接池配置和应用日志。",
            ]

        if "ssh" in query_lower or "22" in query_lower or "连接" in query or "连不上" in query:
            return [
                "确认 ECS 实例处于 Running 状态，且没有在启动中、迁移中或异常状态。",
                "检查实例是否绑定公网 IP 或 EIP；如果只用内网 IP，需要确认访问端和实例在可达的 VPC / 网络内。",
                "检查安全组入方向是否放通 TCP 22 端口，并限制为可信来源 IP，避免长期暴露 0.0.0.0/0。",
                "检查操作系统内 sshd 服务是否启动；Linux 可通过控制台或云助手执行 systemctl status sshd。",
                "如果认证失败，重点核对密钥对、用户名和密码策略；如果超时，优先排查安全组、路由和公网 IP。",
            ]

        if "安全组" in query or "端口" in query or "不通" in query:
            return [
                "确认目标端口对应的协议类型，例如 SSH 使用 TCP 22，Web 服务通常使用 TCP 80 / 443。",
                "检查 ECS 绑定的安全组入方向规则，确认来源 CIDR、协议和端口范围都匹配访问请求。",
                "如果实例同时绑定多个安全组，需要至少一个安全组明确允许该流量。",
                "检查操作系统内部防火墙，例如 firewalld、iptables 或 Windows 防火墙是否拦截。",
                "从实例内和访问端分别做连通性测试，区分云上安全组、系统防火墙还是应用监听问题。",
            ]

        if "cpu" in query_lower or "内存" in query or "负载" in query or "升高" in query:
            return [
                "先查看云监控中的 CPU、内存、磁盘 IO、网络 PPS 和带宽曲线，确认异常开始时间。",
                "进入实例后使用 top、free、iostat、sar 等命令定位高占用进程或资源瓶颈。",
                "如果 CPU 和内存未满但连接大量失败，需要检查 PPS、带宽上限和连接数是否触及规格限制。",
                "如果是周期性升高，结合定时任务、批处理、备份任务和业务峰值继续定位。",
                "短期可以通过限流、重启异常进程或扩容缓解，长期需要结合监控数据做规格或架构调整。",
            ]

        if "启动" in query or "starting" in query_lower or "状态" in query:
            return [
                "先确认控制台实例生命周期状态，区分 Starting、Stopped、Running 和异常状态。",
                "查看系统事件和最近操作记录，确认是否存在迁移、变配、欠费、库存或底层维护事件。",
                "如果启动卡住，检查系统盘、镜像、实例规格和操作系统版本是否兼容。",
                "如果是 Windows 实例启动失败，确认实例规格是否满足该 Windows 版本的最低 vCPU 要求。",
                "必要时先通过控制台快照保护数据，再尝试重启、变更规格或提工单处理。",
            ]

        return [
            "明确故障对象：实例 ID、地域、可用区、操作系统、发生时间和具体报错。",
            "确认资源状态：查看 ECS / RDS / VPC 等资源是否处于正常运行状态。",
            "检查网络链路：公网 IP / EIP、VPC 路由、安全组、网络 ACL 和系统防火墙。",
            "检查系统与应用：登录方式、服务监听端口、进程状态、日志和资源使用率。",
            "保留报错截图、错误码和最近操作记录；如果无法定位，再提交工单给人工支持。",
        ]

    def _should_run_live_diagnostics(self, query: str, metadata: dict[str, Any]) -> bool:
        if not self.diagnostics_enabled:
            return False
        if metadata.get("support_diagnostics_mode") == "live":
            return True
        return bool(_extract_instance_id(query))

    def _get_tool_registry(self):
        if self._tool_registry is None:
            self._tool_registry = get_global_mcp_tool_registry(self._registry_config_path)
        return self._tool_registry

    async def _call_tool(
        self,
        tool: Any,
        args: dict[str, Any],
        *,
        request_id: str,
        user_id_hash: str,
        user_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        start_ms = now_ms()
        try:
            result = await asyncio.wait_for(
                tool.ainvoke(
                    args,
                    config={
                        "configurable": {
                            "request_id": request_id,
                            "user_id_hash": user_id_hash,
                            "user_id": user_id,
                            "tenant_id": tenant_id,
                            "tool_timeout_seconds": self.tool_timeout_seconds,
                        }
                    },
                ),
                timeout=self.tool_timeout_seconds,
            )
            payload = _tool_payload(result)
            status = str(payload.get("status") or "success")
            emit_event(
                build_event(
                    event_type="support_diagnostic_tool",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    component="support_agent",
                    operation=getattr(tool, "name", "unknown_tool"),
                    status=status,
                    latency_ms=elapsed_ms(start_ms),
                )
            )
            return payload
        except Exception as exc:
            emit_event(
                build_event(
                    event_type="support_diagnostic_tool",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    component="support_agent",
                    operation=getattr(tool, "name", "unknown_tool"),
                    status="error",
                    latency_ms=elapsed_ms(start_ms),
                    error_type=exc.__class__.__name__,
                )
            )
            return {
                "status": "error",
                "data": None,
                "user_message": "只读诊断工具调用失败",
                "error_code": exc.__class__.__name__,
            }

    async def _collect_live_diagnostics(
        self,
        *,
        query: str,
        user_id: str,
        tenant_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = get_request_id(metadata)
        user_id_hash = str(metadata.get("user_id_hash", "unknown"))
        instance_id = _extract_instance_id(query)
        if not instance_id:
            return {
                "status": "needs_instance_id",
                "instance_id": "",
                "tool_names": [],
                "evidence": [],
                "summary": "缺少可直接查询的 instance_id",
            }

        try:
            registry = self._get_tool_registry()
            tools = await registry.get_tools_for_agent(
                "support",
                request_id=request_id,
                user_id_hash=user_id_hash,
                query=query,
            )
        except Exception as exc:
            return {
                "status": "degraded",
                "instance_id": instance_id,
                "tool_names": [],
                "evidence": [],
                "summary": f"support 工具注册表不可用: {exc.__class__.__name__}",
            }

        tool_map = {getattr(tool, "name", ""): tool for tool in tools}
        evidence: list[dict[str, Any]] = []

        if "query_user_instances" in tool_map:
            payload = await self._call_tool(
                tool_map["query_user_instances"],
                {"user_id": user_id, "limit": 5},
                request_id=request_id,
                user_id_hash=user_id_hash,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            evidence.append(self._summarize_instance_list(payload, instance_id))

        if "analyze_instance_usage" in tool_map:
            payload = await self._call_tool(
                tool_map["analyze_instance_usage"],
                {"instance_id": instance_id, "user_id": user_id},
                request_id=request_id,
                user_id_hash=user_id_hash,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            evidence.append(self._summarize_usage(payload))

        evidence = [item for item in evidence if item]
        status = (
            "success"
            if evidence
            and all(str(item.get("status") or "").lower() == "success" for item in evidence)
            else "degraded"
        )
        summary = "，".join(item["summary"] for item in evidence) if evidence else "未收集到有效只读证据"

        emit_event(
            build_event(
                event_type="support_diagnostic",
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="support_agent",
                operation="read_only_diagnostic_loop",
                status=status,
                evidence_count=len(evidence),
                instance_id=instance_id,
                tool_count=len(tool_map),
            )
        )

        return {
            "status": status,
            "instance_id": instance_id,
            "tool_names": sorted(tool_map.keys()),
            "evidence": evidence,
            "summary": summary,
        }

    def _summarize_instance_list(self, payload: dict[str, Any], instance_id: str) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, list):
            return {
                "tool": "query_user_instances",
                "status": str(payload.get("status") or "error"),
                "summary": "实例列表工具没有返回列表数据",
            }

        matched = None
        for item in data:
            if isinstance(item, dict) and str(item.get("instance_id") or "") == instance_id:
                matched = item
                break

        if matched is None and data:
            for item in data:
                if isinstance(item, dict) and str(item.get("status") or "").lower() == "running":
                    matched = item
                    break
        if matched is None and data and isinstance(data[0], dict):
            matched = data[0]

        if matched is None:
            return {
                "tool": "query_user_instances",
                "status": str(payload.get("status") or "success"),
                "summary": f"返回 {len(data)} 台实例，但未在当前页命中目标实例",
            }

        public_ip = "有公网 IP" if matched.get("public_ip") else "未配置公网 IP"
        region_id = matched.get("region_id") or "unknown"
        status = matched.get("status") or "unknown"
        return {
            "tool": "query_user_instances",
            "status": str(payload.get("status") or "success"),
            "summary": f"目标实例 {instance_id} 状态 {status}，地域 {region_id}，{public_ip}",
            "target": {
                "instance_id": matched.get("instance_id") or instance_id,
                "status": status,
                "region_id": region_id,
                "public_ip": bool(matched.get("public_ip")),
            },
        }

    def _summarize_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            return {
                "tool": "analyze_instance_usage",
                "status": str(payload.get("status") or "error"),
                "summary": "实例监控工具没有返回结构化数据",
            }

        metrics = data.get("metrics_7d_avg") if isinstance(data.get("metrics_7d_avg"), dict) else {}
        cpu = metrics.get("cpu_usage_percent")
        memory = metrics.get("memory_usage_percent")
        bandwidth = metrics.get("network_out_bandwidth_mbps")
        diagnosis = data.get("diagnosis") or "unknown"
        return {
            "tool": "analyze_instance_usage",
            "status": str(payload.get("status") or "success"),
            "summary": f"7 日诊断 {diagnosis}，CPU {cpu}%，内存 {memory}%，出网带宽 {bandwidth} Mbps",
            "diagnosis": diagnosis,
            "metrics_7d_avg": metrics,
        }

    def _build_response(
        self,
        steps: list[str],
        diagnostics: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
    ) -> list[str]:
        content = [
            "我会先按云平台故障排查思路帮你定位问题。",
            "",
            "先检查这些基础项：",
            "",
        ]
        if checkpoint and str(checkpoint.get("status")) == "confirmed":
            content.extend(
                [
                    "已确认此前的高风险请求：",
                    f"- {checkpoint.get('action_summary') or checkpoint.get('source_query') or '未知动作'}",
                    "",
                ]
            )
        content.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))

        content.extend(
            [
                "",
                "实时诊断证据：",
            ]
        )

        if diagnostics["status"] == "needs_instance_id":
            content.extend(
                [
                    "- 还没有可直接查询的 instance_id。",
                    "- 你补充 ECS / RDS 实例 ID 后，我可以继续跑只读实例和监控查询。",
                ]
            )
        elif diagnostics["status"] == "degraded":
            if diagnostics.get("instance_id"):
                content.append(f"- 目标实例：{diagnostics['instance_id']}")
            content.append(f"- 只读诊断链路发生降级：{diagnostics['summary']}")
        else:
            if diagnostics.get("instance_id"):
                content.append(f"- 目标实例：{diagnostics['instance_id']}")
            content.append(f"- {diagnostics['summary']}")
            for item in diagnostics.get("evidence", []):
                summary = item.get("summary")
                if summary:
                    content.append(f"- {summary}")

        content.extend(
            [
                "",
                "如果你能补充实例 ID、地域、具体报错、发生时间和最近是否做过变配 / 重启 / 安全组修改，我可以继续把排查路径收敛到更具体的方向。",
                "",
                "答案来源：",
                "- 本地故障排查规则：ecs_troubleshooting_guide.txt",
                "- 本地工单支持规则：ticket_and_support_guide.txt",
            ]
        )
        return content

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        query = self._last_user_message(state)
        checkpoint = self._current_checkpoint(state)
        query_for_analysis = query
        if checkpoint and str(checkpoint.get("status")) == "confirmed":
            query_for_analysis = str(checkpoint.get("source_query") or query)
        steps = self._build_steps(query_for_analysis)
        metadata = ensure_request_metadata(state.get("metadata", {}))
        metadata["handled_by"] = "support_agent"
        request_id = get_request_id(metadata)
        user_id = str(state.get("user_id", "unknown"))
        tenant_id = str(state.get("tenant_id", "default_tenant"))

        diagnostics = {
            "status": "needs_instance_id",
            "instance_id": "",
            "tool_names": [],
            "evidence": [],
            "summary": "缺少可直接查询的 instance_id",
        }
        if self._should_run_live_diagnostics(query_for_analysis, metadata):
            diagnostics = await self._collect_live_diagnostics(
                query=query_for_analysis,
                user_id=user_id,
                tenant_id=tenant_id,
                metadata=metadata,
            )

        metadata["support_diagnostics"] = {
            "status": diagnostics["status"],
            "instance_id": diagnostics.get("instance_id", ""),
            "tool_names": diagnostics.get("tool_names", []),
            "evidence_count": len(diagnostics.get("evidence", [])),
            "summary": diagnostics.get("summary", ""),
        }

        emit_event(
            build_event(
                event_type="support_response",
                request_id=request_id,
                user_id_hash=str(metadata.get("user_id_hash", "unknown")),
                component="support_agent",
                operation="response_generation",
                status=diagnostics["status"],
                evidence_count=len(diagnostics.get("evidence", [])),
            )
        )

        logger.info("SupportAgent request_id=%s status=%s", request_id, diagnostics["status"])

        content = self._build_response(steps, diagnostics, checkpoint=checkpoint)
        return {"messages": [AIMessage(content="\n".join(content))], "metadata": metadata}
