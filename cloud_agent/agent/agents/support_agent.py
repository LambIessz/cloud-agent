from typing import Any, Dict, List

from langchain_core.messages import AIMessage

from core.workflow.state import AgentState
from core.workflow.request_context import ensure_request_metadata, get_request_id


class SupportAgentNode:
    """
    Provides deterministic first-line troubleshooting guidance.

    This first version deliberately avoids live infrastructure operations.
    Later MCP tools can be added for instance status, security group rules,
    network ACLs, and monitoring metrics without changing the graph contract.
    """

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

    def _build_steps(self, query: str) -> List[str]:
        query_lower = query.lower()

        if "ssh" in query_lower or "22" in query_lower or "连接" in query:
            return [
                "确认 ECS 实例处于 Running 状态，且系统没有处于启动中、迁移中或异常状态。",
                "检查实例是否绑定公网 IP 或 EIP；如果只使用内网 IP，需要确认访问端与实例在可达的 VPC/网络内。",
                "检查安全组入方向规则是否放通 TCP 22 端口，并限制为可信来源 IP，避免 0.0.0.0/0 长期暴露。",
                "检查操作系统内 sshd 服务是否启动，Linux 可通过控制台或云助手执行 systemctl status sshd。",
                "如果提示认证失败，重点核对密钥对、用户名和密码策略；如果提示超时，优先排查安全组、路由和公网 IP。",
            ]

        if "安全组" in query or "端口" in query or "不通" in query:
            return [
                "确认目标端口对应的协议类型，例如 SSH 使用 TCP 22，Web 服务通常使用 TCP 80/443。",
                "检查 ECS 绑定的安全组入方向规则，确认来源 CIDR、协议和端口范围均匹配访问请求。",
                "如果实例同时绑定多个安全组，需要确认至少一个安全组明确允许该流量。",
                "检查操作系统内部防火墙，例如 firewalld、iptables 或 Windows 防火墙是否拦截。",
                "从实例内和访问端分别做连通性测试，区分是云上安全组问题、系统防火墙问题还是应用监听问题。",
            ]

        if "cpu" in query_lower or "内存" in query or "负载" in query or "升高" in query:
            return [
                "先查看云监控中的 CPU、内存、磁盘 IO、网络 PPS 和带宽曲线，确认异常开始时间。",
                "进入实例后使用 top、free、iostat、sar 等命令定位高占用进程或资源瓶颈。",
                "如果 CPU 和内存未满但连接大量失败，需要检查 PPS、带宽上限和连接数是否触及规格限制。",
                "如果是周期性升高，结合定时任务、批处理、备份任务和业务峰值进一步定位。",
                "短期可通过限流、重启异常进程或扩容缓解；长期应结合监控数据做规格调整或架构优化。",
            ]

        if "rds" in query_lower or "数据库" in query:
            return [
                "确认 RDS 实例状态正常，连接地址、端口、账号和密码配置正确。",
                "检查 ECS 与 RDS 是否在同一 VPC，或是否已配置正确的网络互通方案。",
                "检查 RDS 白名单或安全访问策略是否允许 ECS 的内网 IP 访问。",
                "在 ECS 上使用 telnet、nc 或数据库客户端测试 RDS 端口连通性。",
                "如果连接数耗尽或慢查询增加，需要查看 RDS 监控、连接池配置和应用错误日志。",
            ]

        if "启动" in query or "starting" in query_lower or "状态" in query:
            return [
                "先确认控制台实例生命周期状态，区分 Starting、Stopped、Running 和异常状态。",
                "查看系统事件和最近操作记录，确认是否存在迁移、变配、欠费、库存或底层维护事件。",
                "如果启动卡住，检查系统盘、镜像、实例规格和操作系统版本是否兼容。",
                "如果是 Windows 实例启动失败，确认实例规格是否满足该 Windows 版本的最低 vCPU 要求。",
                "必要时先通过控制台快照保护数据，再尝试重启、变更规格或提交工单处理。",
            ]

        return [
            "明确故障对象：实例 ID、地域、可用区、操作系统、发生时间和具体报错。",
            "确认资源状态：查看 ECS/RDS/VPC 等资源是否处于正常运行状态。",
            "检查网络链路：公网 IP/EIP、VPC 路由、安全组、网络 ACL 和系统防火墙。",
            "检查系统与应用：登录方式、服务监听端口、进程状态、日志和资源使用率。",
            "保留报错截图、错误码和最近操作记录；如果无法定位，再提交工单给人工支持。",
        ]

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        query = self._last_user_message(state)
        steps = self._build_steps(query)
        metadata = ensure_request_metadata(state.get("metadata", {}))
        metadata["handled_by"] = "support_agent"
        print(f"[SupportAgent] request_id={get_request_id(metadata)} generated troubleshooting checklist")

        content = [
            "我会按云平台故障排查思路帮你先定位问题。当前建议先做这些检查：",
            "",
        ]
        content.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        content.extend(
            [
                "",
                "如果你能补充实例 ID、地域、具体报错、发生时间和最近是否做过变配/重启/安全组修改，"
                "我可以继续帮你把排查路径收敛到更具体的方向。",
                "",
                "答案来源：",
                "- 本地故障排查规则：ecs_troubleshooting_guide.md",
                "- 本地工单支持规则：ticket_and_support_guide.md",
            ]
        )

        return {"messages": [AIMessage(content="\n".join(content))], "metadata": metadata}
