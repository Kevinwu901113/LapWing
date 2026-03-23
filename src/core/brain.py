"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import logging
from pathlib import Path

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from config.settings import MAX_HISTORY_TURNS

logger = logging.getLogger("lapwing.brain")


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path):
        self.router = LLMRouter()
        self.memory = ConversationMemory(db_path)
        self.fact_extractor = FactExtractor(self.memory, self.router)
        self._system_prompt: str | None = None
        self.dispatcher = None  # Set externally by main.py (AgentDispatcher | None)

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    @property
    def system_prompt(self) -> str:
        """懒加载 system prompt（基础人格）。"""
        if self._system_prompt is None:
            self._system_prompt = load_prompt("lapwing")
            logger.info("已加载 Lapwing 人格 prompt")
        return self._system_prompt

    def reload_persona(self) -> None:
        """重新加载人格 prompt（修改 prompts/lapwing.md 后调用）。"""
        from src.core.prompt_loader import reload_prompt
        self._system_prompt = reload_prompt("lapwing")
        logger.info("已重新加载 Lapwing 人格 prompt")

    async def _build_system_prompt(self, chat_id: str) -> str:
        """组合基础人格 prompt 和用户画像信息。"""
        base = self.system_prompt
        facts = await self.memory.get_user_facts(chat_id)
        if not facts:
            return base

        facts_text = "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)
        return (
            f"{base}\n\n"
            f"## 你对这个用户的了解\n\n"
            f"以下是你从之前对话中了解到的关于这个用户的信息。"
            f"在合适的时候可以自然地引用，但不要刻意提起。\n\n"
            f"{facts_text}"
        )

    async def think(self, chat_id: str, user_message: str) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户发送的消息

        Returns:
            Lapwing 的回复文本
        """
        await self.memory.append(chat_id, "user", user_message)

        # 通知提取器有新消息（异步触发轮次/空闲计时逻辑）
        self.fact_extractor.notify(chat_id)

        # Try agent dispatch first
        if self.dispatcher is not None:
            try:
                agent_reply = await self.dispatcher.try_dispatch(chat_id, user_message)
                if agent_reply is not None:
                    await self.memory.append(chat_id, "assistant", agent_reply)
                    return agent_reply
            except Exception as e:
                logger.warning(f"[{chat_id}] Agent dispatch failed, falling back: {e}")

        history = await self.memory.get(chat_id)
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history

        # 动态组合 system prompt（基础人格 + 用户画像）
        system_content = await self._build_system_prompt(chat_id)

        messages = [
            {"role": "system", "content": system_content},
            *recent,
        ]

        try:
            reply = await self.router.complete(messages, purpose="chat")
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
