"""Agent Team 工具：delegate_to_researcher + delegate_to_coder。

两层架构：主脑 LLM 通过 tool_call 名称（delegate_to_researcher 或
delegate_to_coder）选择目标 agent；AgentRegistry 按名取实例并执行。
没有独立 Dispatcher 组件——"路由"由 LLM 在主脑外层 tool 选择中完成。
"""

from __future__ import annotations

import logging
import traceback
import uuid

from src.agents.types import AgentMessage, AgentResult
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.agent_tools")


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _extract_context_digest(ctx: ToolExecutionContext) -> str:
    """从当前上下文中提取摘要供子 agent 参考。"""
    parts: list[str] = []

    trajectory_store = ctx.services.get("trajectory_store")
    if trajectory_store is not None:
        try:
            recent = trajectory_store.recent(ctx.chat_id, limit=6)
            if recent:
                lines = []
                for entry in recent[-6:]:
                    role = getattr(entry, "role", "")
                    text = getattr(entry, "text", "") or getattr(entry, "content", "")
                    if text:
                        lines.append(f"{role}: {str(text)[:200]}")
                if lines:
                    parts.append("最近对话：\n" + "\n".join(lines))
        except Exception:
            pass

    return "\n\n".join(parts)


def _serialize_agent_result(result: AgentResult, task_id: str) -> ToolExecutionResult:
    """统一将 AgentResult 序列化为 ToolExecutionResult。"""
    trace_tail = result.execution_trace[-5:] if result.execution_trace else []

    if result.status == "done":
        payload: dict = {
            "task_id": task_id,
            "result": result.result,
            "artifacts": result.artifacts,
            "evidence": result.evidence,
        }
        if trace_tail:
            payload["execution_trace"] = trace_tail
        return ToolExecutionResult(
            success=True,
            payload=payload,
            reason="任务完成",
        )
    else:
        payload = {
            "task_id": task_id,
            "status": result.status,
        }
        if result.error_detail:
            payload["error_detail"] = result.error_detail
        if trace_tail:
            payload["execution_trace"] = trace_tail
        return ToolExecutionResult(
            success=False,
            payload=payload,
            reason=result.reason or "任务失败",
        )


async def _run_agent(
    agent_name: str,
    request: str,
    context_digest: str,
    ctx: ToolExecutionContext,
    parent_task_id: str | None = None,
) -> ToolExecutionResult:
    """直接调度指定 agent 执行任务。"""
    agent_registry = ctx.services.get("agent_registry")
    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    agent = agent_registry.get(agent_name)
    if not agent:
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"Agent '{agent_name}' 不可用",
        )

    task_id = _generate_task_id()

    digest = context_digest.strip()
    if not digest:
        digest = _extract_context_digest(ctx)

    message = AgentMessage(
        from_agent="lapwing",
        to_agent=agent_name,
        task_id=task_id,
        content=request,
        context_digest=digest,
        message_type="request",
        parent_task_id=parent_task_id,
    )

    try:
        result = await agent.execute(message)
    except Exception as exc:
        tb = traceback.format_exc()
        tb_tail = "\n".join(tb.strip().splitlines()[-5:])
        return ToolExecutionResult(
            success=False,
            payload={"task_id": task_id, "error_detail": tb_tail},
            reason=f"Agent 执行异常: {exc}",
        )

    return _serialize_agent_result(result, task_id)


async def delegate_to_researcher_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    request = req.arguments.get("request", "").strip()
    if not request:
        return ToolExecutionResult(success=False, payload={}, reason="request 不能为空")

    context_digest = req.arguments.get("context_digest", "")
    return await _run_agent("researcher", request, context_digest, ctx)


async def delegate_to_coder_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    request = req.arguments.get("request", "").strip()
    if not request:
        return ToolExecutionResult(success=False, payload={}, reason="request 不能为空")

    context_digest = req.arguments.get("context_digest", "")
    return await _run_agent("coder", request, context_digest, ctx)


def register_agent_tools(registry, agent_registry=None) -> None:
    """注册 delegate_to_researcher + delegate_to_coder 到 ToolRegistry。"""

    _DELEGATE_SCHEMA = {
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "你的需求——清晰具体地描述任务"},
            "context_digest": {
                "type": "string",
                "description": "当前对话的背景摘要，帮助 agent 理解上下文",
            },
        },
        "required": ["request"],
    }

    registry.register(ToolSpec(
        name="delegate_to_researcher",
        description=(
            "把调研任务交给 Researcher。"
            "擅长：网络搜索、信息整理、多源综合、写摘要。"
            "不擅长：写代码、执行脚本、文件操作。"
        ),
        json_schema=_DELEGATE_SCHEMA,
        executor=delegate_to_researcher_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=3000,
    ))

    registry.register(ToolSpec(
        name="delegate_to_coder",
        description=(
            "把代码任务交给 Coder。"
            "擅长：写代码、调试、跑脚本、文件读写。"
            "不擅长：网络搜索、信息调研。"
        ),
        json_schema=_DELEGATE_SCHEMA,
        executor=delegate_to_coder_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=3000,
    ))

    logger.info("[agent_tools] 已注册 delegate_to_researcher + delegate_to_coder")
