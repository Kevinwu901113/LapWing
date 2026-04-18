"""tell_user — Lapwing 唯一对外说话工具。

Blueprint v2.0 Step 5。模型唯一对用户可见的输出路径。所有 LLM 直接
返回的裸文本现在都被视为内部独白，只有 tool_use → tell_user 的调用
结果才会通过 ``send_fn`` 发送给用户。这是 Step 5 的核心结构性保证：
不是过滤，是契约——函数调用是强约束，模型要么调 tell_user 要么不说话。

每次调用发送一条消息；想连发多条就多次调用 tell_user。
"""
from __future__ import annotations

import logging

from src.logging.state_mutation_log import (
    MutationType,
    current_iteration_id,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)

logger = logging.getLogger("lapwing.tools.tell_user")


TELL_USER_DESCRIPTION = (
    "向用户发送一条消息。这是你和用户沟通的唯一方式——"
    "你直接返回的文字属于内心独白，不会被用户看到，必须通过 tell_user 才能说话。"
    "每次调用发送一条消息；想连发多条就多次调用 tell_user。"
)


TELL_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "要发送给用户的消息内容（一条），不要在里面塞 [SPLIT] 或 \\n 拼接多条。",
        }
    },
    "required": ["text"],
    "additionalProperties": False,
}


async def tell_user_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    text = str(request.arguments.get("text", "")).strip()
    if not text:
        return ToolExecutionResult(
            success=False,
            payload={"delivered": False, "reason": "text 不能为空"},
            reason="tell_user 调用缺少 text 参数",
        )

    if context.send_fn is None:
        # 内部 tick / agent / heartbeat 等无用户通道的上下文。
        # 不静默吞掉——告诉模型这条话没发出去，让它选择别的路径
        # （例如通过 send_proactive_message 主动消息给指定 chat）。
        return ToolExecutionResult(
            success=False,
            payload={"delivered": False, "reason": "当前上下文没有用户通道"},
            reason="tell_user 在没有 send_fn 的上下文中被调用（如内部 tick）",
        )

    try:
        await context.send_fn(text)
    except Exception as exc:
        logger.warning("tell_user send_fn 调用失败: %s", exc, exc_info=True)
        return ToolExecutionResult(
            success=False,
            payload={"delivered": False, "reason": str(exc)},
            reason=f"send_fn 失败: {exc}",
        )

    services = context.services or {}

    # 累加到本轮 tell_user 缓冲——brain.think_conversational 在 complete_chat
    # 返回后用它计算 memory_text（"她真正说了什么"）。
    buffer = services.get("tell_user_buffer")
    if isinstance(buffer, list):
        buffer.append(text)

    # 写入 trajectory（actor=lapwing，entry_type=tell_user）
    trajectory_id: int | None = None
    trajectory_store = services.get("trajectory_store")
    if trajectory_store is not None:
        try:
            from src.core.trajectory_store import TrajectoryEntryType

            trajectory_id = await trajectory_store.append(
                TrajectoryEntryType.TELL_USER,
                context.chat_id or None,
                "lapwing",
                {"text": text},
                related_iteration_id=current_iteration_id(),
            )
        except Exception:
            logger.warning("tell_user trajectory append 失败", exc_info=True)

    # mutation_log 留一份（durable，跨进程可查询）
    mutation_log = services.get("mutation_log")
    if mutation_log is not None:
        try:
            await mutation_log.record(
                MutationType.TELL_USER,
                {
                    "text": text,
                    "chat_id": context.chat_id,
                    "adapter": context.adapter,
                    "trajectory_id": trajectory_id,
                },
                iteration_id=current_iteration_id(),
                chat_id=context.chat_id or None,
            )
        except Exception:
            logger.warning("tell_user mutation_log mirror 失败", exc_info=True)

    return ToolExecutionResult(
        success=True,
        payload={
            "delivered": True,
            "text": text,
            "trajectory_id": trajectory_id,
        },
    )
