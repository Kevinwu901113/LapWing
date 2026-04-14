"""背景自动回顾 — 对话后异步自省。

每 N 轮用户消息后，在主回复完成之后，用 LLM 审视最近对话，
决定是否需要保存记忆。在后台协程中运行，不阻塞主对话。

关键设计：
- 不使用 think_conversational（避免污染对话历史）
- 用 router.complete() 做独立 LLM 调用
- 如果 LLM 返回值得记住的内容，调用 write_note 持久化
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.memory.conversation import ConversationMemory

logger = logging.getLogger("lapwing.core.background_review")

# 默认每 10 轮用户消息触发一次回顾
DEFAULT_REVIEW_INTERVAL = 10

REVIEW_PROMPT = (
    "回顾我们刚才的对话，想想有没有什么值得记住的：\n\n"
    "1. Kevin 有没有透露个人偏好、习惯、工作方式、生活细节？\n"
    "2. 他有没有纠正过我的行为，或者表达过不满？\n"
    "3. 我有没有发现关于他的环境、项目、工具的新信息？\n\n"
    "如果有值得记住的，按以下格式每条一行输出：\n"
    "REMEMBER: <target> | <内容>\n"
    "其中 target 是 kevin 或 self。\n\n"
    "如果没有值得记住的，只输出：NOTHING\n"
    "不要编造信息。"
)


class BackgroundReviewer:
    """管理背景回顾的触发和执行。"""

    def __init__(self, interval: int = DEFAULT_REVIEW_INTERVAL):
        self._interval = max(interval, 1)  # 至少为 1
        self._turns_since_review = 0
        self._review_running = False

    def tick(self) -> bool:
        """记录一轮用户消息，返回是否应该触发回顾。"""
        self._turns_since_review += 1
        if self._turns_since_review >= self._interval:
            self._turns_since_review = 0
            return True
        return False

    async def maybe_review(
        self,
        router: "LLMRouter",
        memory: "ConversationMemory",
        chat_id: str,
    ) -> None:
        """如果条件满足，在后台执行回顾。

        不阻塞主对话。用 asyncio.create_task 在后台运行。
        """
        if not self.tick():
            return

        if self._review_running:
            logger.debug("回顾已在运行中，跳过")
            return

        self._review_running = True
        asyncio.create_task(self._run_review(router, memory, chat_id))

    async def _run_review(
        self,
        router: "LLMRouter",
        memory: "ConversationMemory",
        chat_id: str,
    ) -> None:
        """执行一次回顾（在后台协程中运行）。"""
        try:
            # 获取最近几轮对话作为上下文（从缓存读取）
            recent = await memory.get(chat_id)
            recent = recent[-20:]  # 最近 20 条
            if not recent:
                logger.debug("无最近对话，跳过回顾")
                return

            # 构建独立消息列表（不污染对话历史）
            messages = [
                {"role": "system", "content": "你是 Lapwing，正在回顾刚才和 Kevin 的对话。"},
            ]
            for msg in recent:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": REVIEW_PROMPT})

            # 独立 LLM 调用
            reply = await router.complete(
                messages=messages,
                purpose="tool",
                slot="memory_processing",
                max_tokens=512,
                origin="background_review",
            )

            # 解析回复，提取 REMEMBER 行
            await self._process_review_result(reply)
            logger.info("背景回顾完成")

        except Exception as e:
            logger.warning("背景回顾失败（非致命）: %s", e)
        finally:
            self._review_running = False

    async def _process_review_result(self, reply: str) -> None:
        """解析回顾结果，将 REMEMBER 行写入记忆。"""
        if not reply or "NOTHING" in reply.upper():
            logger.debug("回顾结果：无需记忆")
            return

        from src.tools.memory_note import write_note

        for line in reply.strip().split("\n"):
            line = line.strip()
            if not line.upper().startswith("REMEMBER:"):
                continue
            payload = line[len("REMEMBER:"):].strip()
            if "|" not in payload:
                continue
            target, content = payload.split("|", 1)
            target = target.strip().lower()
            content = content.strip()
            if target in ("kevin", "self") and content:
                result = await write_note(target, content)
                if result.get("success"):
                    logger.info("回顾写入记忆 [%s]: %s", target, content[:60])
                else:
                    logger.debug("回顾写入失败: %s", result.get("reason"))
