"""未完成任务恢复 Action（v2 —— 零模板设计）。

恢复流程：
1. 从 PendingTaskStore 获取可恢复任务
2. 加载最近对话历史（让 Lapwing 判断话题冲突）
3. 用完整人格 LLM 调用让 Lapwing 自己决定怎么说
4. 如果 Lapwing 决定现在不合适 → 跳过，下轮再试
5. 如果 Lapwing 决定继续 → 发送通知消息 → 重新进入工具循环
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.pending_task import MAX_RETRY_COUNT, MAX_SKIP_COUNT

if TYPE_CHECKING:
    from src.core.brain import LapwingBrain
    from src.core.pending_task import PendingTask, PendingTaskStore

logger = logging.getLogger("lapwing.heartbeat.task_resumption")

SKIP_MARKER = "[SKIP_FOR_NOW]"
CONTINUE_MARKER = "[WILL_CONTINUE]"


class TaskResumptionAction(HeartbeatAction):
    name = "task_resumption"
    description = "检查并恢复未完成的任务"
    beat_types = ["minute"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain: "LapwingBrain", send_fn) -> None:
        store = brain.pending_task_store
        if not store:
            return

        store.cleanup_expired()

        tasks = store.get_actionable()
        if not tasks:
            return

        # 每轮最多恢复 1 个，优先最早创建的
        tasks.sort(key=lambda t: t.created_at)
        await self._resume_task(tasks[0], brain, store, send_fn)

    async def _resume_task(
        self,
        task: "PendingTask",
        brain: "LapwingBrain",
        store: "PendingTaskStore",
        default_send_fn,
    ) -> None:
        logger.info(
            "Considering resumption of task %s: %s",
            task.task_id, task.user_request[:60],
        )

        # ── 获取 send_fn ──
        # 使用心跳引擎传入的 send_fn（绑定到当前活跃通道）
        channel_send_fn = default_send_fn
        if channel_send_fn is None:
            logger.warning(
                "Cannot resume task %s: no send_fn available",
                task.task_id,
            )
            return

        # ── 加载最近对话历史 ──
        recent_messages = await self._get_recent_messages(brain, task.chat_id)

        # ── 构建恢复上下文 ──
        steps_summary = self._format_steps(task.completed_steps)

        # skip_notice：多次跳过后催促
        if task.skip_count >= MAX_SKIP_COUNT:
            skip_notice = (
                "你已经因为觉得不合适推迟了好几次了。"
                "如果有机会的话尽量提一下，不然这件事可能就过去了。"
            )
        else:
            skip_notice = ""

        context = {
            "user_request": task.user_request,
            "completed_steps_summary": steps_summary,
            "partial_result": task.partial_result[:300],
            "remaining_description": task.remaining_description,
            "recent_messages": recent_messages,
            "skip_notice": skip_notice,
        }

        # ── 第一步 LLM 调用：让 Lapwing 自己决定 ──
        from src.core.prompt_builder import build_resumption_prompt
        system_text, instruction = build_resumption_prompt(context)

        try:
            response = await brain.router.query_lightweight(
                system=system_text,
                user=instruction,
                slot="main_conversation",
            )
            response = response.strip()

            # ── Lapwing 决定现在不合适 ──
            if SKIP_MARKER in response:
                logger.info(
                    "Lapwing decided to skip resumption for now: %s (skip_count=%d)",
                    task.task_id, task.skip_count + 1,
                )
                task.skip_count += 1
                store.save(task)
                return

            # ── Lapwing 决定继续 ──
            if CONTINUE_MARKER in response:
                notification_message = response.split(CONTINUE_MARKER)[0].strip()
            else:
                notification_message = response

            if not notification_message:
                logger.warning("Empty notification message from resumption LLM call")
                return

            # ── 发送通知 ──
            await channel_send_fn(notification_message)

            # ── 写入对话历史 ──
            if brain.memory is not None:
                await brain.memory.append(
                    task.chat_id,
                    "assistant",
                    notification_message,
                )

            # ── 标记重试 ──
            task.record_retry()
            store.save(task)

            # ── 第二步：重新进入对话流，继续执行 ──
            await brain.think_conversational(
                chat_id=task.chat_id,
                user_message="",
                send_fn=channel_send_fn,
                adapter=task.adapter,
                user_id=task.user_id,
                metadata={
                    "source": "task_resumption",
                    "task_id": task.task_id,
                    "resumption_context": {
                        "original_task_id": task.original_task_id,
                        "total_resumption_count": task.total_resumption_count,
                        "user_request": task.user_request,
                        "remaining_description": task.remaining_description,
                    },
                },
            )

            # ── 恢复完成，移除旧任务 ──
            # 如果仍未完成，_check_task_completion 会创建新的 PendingTask
            store.remove(task.task_id)
            logger.info("Task %s resumption completed", task.task_id)

        except Exception as e:
            logger.error("Failed to resume task %s: %s", task.task_id, e)
            task.record_retry()
            if task.retry_count >= MAX_RETRY_COUNT:
                logger.warning(
                    "Task %s exceeded max retries (%d), removing",
                    task.task_id, task.retry_count,
                )
                store.remove(task.task_id)
            else:
                store.save(task)

    async def _get_recent_messages(
        self, brain: "LapwingBrain", chat_id: str,
    ) -> str:
        """获取最近对话历史，给 LLM 判断话题冲突用。"""
        if brain.memory is None:
            return "（无法获取最近对话）"

        try:
            messages = await brain.memory.get(chat_id)
        except Exception:
            return "（无法获取最近对话）"

        if not messages:
            return "（最近没有对话）"

        # 最多取最近 10 条
        recent = messages[-10:]
        lines = []
        for msg in recent:
            role = "你" if msg.get("role") == "assistant" else "Kevin"
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 150:
                content = content[:147] + "..."
            elif not isinstance(content, str):
                content = "(非文本)"
            lines.append(f"{role}：{content}")

        return "\n".join(lines)

    @staticmethod
    def _format_steps(steps: list[dict]) -> str:
        """格式化已完成步骤（内部用，不是用户可见的）。"""
        if not steps:
            return "（没有记录到具体步骤）"

        lines = []
        for i, step in enumerate(steps[-5:], 1):
            tool = step.get("tool", "?")
            result = step.get("result_brief", "")[:150]
            lines.append(f"{i}. 用 {tool} → {result}")
        return "\n".join(lines)
