"""Agent Team 工具：delegate + delegate_to_agent。

v2.0 Step 6 对齐：delegate 执行完全依赖 ``BaseAgent`` 的 mutation_log
埋点（``AGENT_STARTED`` / ``AGENT_TOOL_CALL`` / ``AGENT_COMPLETED`` /
``AGENT_FAILED``），tool executor 自身不再重复 emit 事件——Phase 6 的
``dispatcher.submit(event_type="agent.task_*")`` 双层 emit 已删除，
Desktop SSE 通过 mutation_log 直接拿到完整生命周期。

工具 description + enum 从 ``AgentRegistry`` 动态填充，避免硬编码
Agent 名称和描述漂移（Step 6 改动 5）。
"""

from __future__ import annotations

import logging
import uuid

from src.agents.types import AgentMessage
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.agent_tools")


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


async def delegate_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Lapwing 调用的 delegate 工具。把任务交给 Team Lead。"""
    request = req.arguments.get("request", "").strip()
    context_str = req.arguments.get("context", "")

    if not request:
        return ToolExecutionResult(success=False, payload={}, reason="请求不能为空")

    agent_registry = ctx.services.get("agent_registry")

    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    team_lead = agent_registry.get("team_lead")
    if not team_lead:
        return ToolExecutionResult(success=False, payload={}, reason="Team Lead 不可用")

    task_id = _generate_task_id()

    message = AgentMessage(
        from_agent="lapwing",
        to_agent="team_lead",
        task_id=task_id,
        content=f"{request}\n\n上下文: {context_str}" if context_str else request,
        message_type="request",
    )

    result = await team_lead.execute(message)

    if result.status == "done":
        return ToolExecutionResult(
            success=True,
            payload={
                "task_id": task_id,
                "result": result.result,
                "artifacts": result.artifacts,
            },
            reason="任务完成",
        )
    else:
        return ToolExecutionResult(
            success=False,
            payload={"task_id": task_id, "status": result.status},
            reason=result.reason or "任务失败",
        )


async def delegate_to_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Team Lead 调用的工具。把子任务派给具体 Agent。"""
    agent_name = req.arguments.get("agent", "").strip()
    instruction = req.arguments.get("instruction", "").strip()

    if not agent_name or not instruction:
        return ToolExecutionResult(
            success=False, payload={},
            reason="agent 和 instruction 不能为空",
        )

    agent_registry = ctx.services.get("agent_registry")

    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    agent = agent_registry.get(agent_name)
    if not agent:
        available = agent_registry.list_names()
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"Agent '{agent_name}' 不存在。可用: {', '.join(available)}",
        )

    subtask_id = _generate_task_id()

    message = AgentMessage(
        from_agent="team_lead",
        to_agent=agent_name,
        task_id=subtask_id,
        content=instruction,
        message_type="request",
    )

    result = await agent.execute(message)

    if result.status == "done":
        return ToolExecutionResult(
            success=True,
            payload={
                "result": result.result,
                "evidence": result.evidence,
                "artifacts": result.artifacts,
            },
            reason="ok",
        )
    else:
        return ToolExecutionResult(
            success=False,
            payload={"status": result.status},
            reason=result.reason or "失败",
        )


def _build_delegate_description(agent_registry) -> str:
    """从 AgentRegistry 动态生成团队成员列表。"""
    base = "把任务交给你的工作团队。告诉 Team Lead 你需要什么。"
    if agent_registry is None:
        return base
    specs = agent_registry.list_specs()
    if not specs:
        return base
    lines = [base, "", "团队成员："]
    for spec in specs:
        lines.append(f"- {spec['name']}: {spec['description']}")
    return "\n".join(lines)


def _build_delegate_to_agent_description(agent_registry) -> str:
    base = "把子任务派给一个具体的 Agent。"
    if agent_registry is None:
        return base
    specs = agent_registry.list_specs()
    if not specs:
        return base
    lines = [base, "", "可用 Agent："]
    for spec in specs:
        lines.append(f"- {spec['name']}: {spec['description']}")
    return "\n".join(lines)


def _agent_enum(agent_registry) -> list[str]:
    if agent_registry is None:
        return []
    return [s["name"] for s in agent_registry.list_specs()]


def register_agent_tools(registry, agent_registry=None) -> None:
    """注册 Agent Team 工具到 ToolRegistry。

    ``agent_registry`` 用于动态生成工具 description 与 ``agent`` enum。
    container 在组装 AgentRegistry 后调用这里注册，description 随当前
    成员列表生效——新增/删除 Agent 不用改工具 schema。
    """

    enum = _agent_enum(agent_registry)
    to_agent_schema_agent: dict = {
        "type": "string",
        "description": "Agent 名称",
    }
    if enum:
        to_agent_schema_agent["enum"] = enum

    registry.register(ToolSpec(
        name="delegate",
        description=_build_delegate_description(agent_registry),
        json_schema={
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "你的需求"},
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "紧急程度",
                    "default": "normal",
                },
                "context": {"type": "string", "description": "相关上下文（可选）"},
            },
            "required": ["request"],
        },
        executor=delegate_executor,
        capability="general",
        risk_level="low",
        max_result_tokens=3000,
    ))

    registry.register(ToolSpec(
        name="delegate_to_agent",
        description=_build_delegate_to_agent_description(agent_registry),
        json_schema={
            "type": "object",
            "properties": {
                "agent": to_agent_schema_agent,
                "instruction": {
                    "type": "string",
                    "description": "给 Agent 的指令",
                },
            },
            "required": ["agent", "instruction"],
        },
        executor=delegate_to_agent_executor,
        capability="agent",
        risk_level="low",
    ))

    logger.info("[agent_tools] 已注册 delegate + delegate_to_agent")
