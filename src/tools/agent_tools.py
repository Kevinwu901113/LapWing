"""Agent Team 工具：delegate + delegate_to_agent。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from src.agents.types import AgentMessage, AgentResult
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
    dispatcher = ctx.services.get("dispatcher")

    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    team_lead = agent_registry.get("team_lead")
    if not team_lead:
        return ToolExecutionResult(success=False, payload={}, reason="Team Lead 不可用")

    task_id = _generate_task_id()

    if dispatcher:
        await dispatcher.submit(
            event_type="agent.task_created",
            actor="lapwing",
            task_id=task_id,
            payload={"request": request, "assigned_to": "team_lead"},
        )

    message = AgentMessage(
        from_agent="lapwing",
        to_agent="team_lead",
        task_id=task_id,
        content=f"{request}\n\n上下文: {context_str}" if context_str else request,
        message_type="request",
    )

    result = await team_lead.execute(message)

    if dispatcher:
        await dispatcher.submit(
            event_type=f"agent.task_{result.status}",
            actor="team_lead",
            task_id=task_id,
            payload={"result": result.result[:500] if result.result else ""},
        )

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
    dispatcher = ctx.services.get("dispatcher")

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

    if dispatcher:
        await dispatcher.submit(
            event_type="agent.task_assigned",
            actor="team_lead",
            task_id=subtask_id,
            payload={"agent": agent_name, "instruction": instruction},
        )

    message = AgentMessage(
        from_agent="team_lead",
        to_agent=agent_name,
        task_id=subtask_id,
        content=instruction,
        message_type="request",
    )

    result = await agent.execute(message)

    if dispatcher:
        await dispatcher.submit(
            event_type=f"agent.task_{result.status}",
            actor=agent_name,
            task_id=subtask_id,
            payload={
                "result": result.result[:500] if result.result else "",
                "evidence": result.evidence,
            },
        )

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


def register_agent_tools(registry) -> None:
    """注册 Agent Team 工具到 ToolRegistry。"""

    registry.register(ToolSpec(
        name="delegate",
        description="把任务交给你的工作团队。告诉 Team Lead 你需要什么。",
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
        description="把子任务派给一个具体的 Agent。",
        json_schema={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent 名称 (researcher / coder)",
                },
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
