"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.core.prompt_loader import load_prompt
from src.memory.conversation import ConversationMemory
from config.settings import MAX_HISTORY_TURNS

logger = logging.getLogger("lapwing.brain")


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, api_key: str, base_url: str, model: str, db_path: Path):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.memory = ConversationMemory(db_path)
        self._system_prompt: str | None = None

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    @property
    def system_prompt(self) -> str:
        """懒加载 system prompt。"""
        if self._system_prompt is None:
            self._system_prompt = load_prompt("lapwing")
            logger.info("已加载 Lapwing 人格 prompt")
        return self._system_prompt

    def reload_persona(self) -> None:
        """重新加载人格 prompt（修改 prompts/lapwing.md 后调用）。"""
        from src.core.prompt_loader import reload_prompt
        self._system_prompt = reload_prompt("lapwing")
        logger.info("已重新加载 Lapwing 人格 prompt")

    async def think(self, chat_id: str, user_message: str) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户发送的消息

        Returns:
            Lapwing 的回复文本
        """
        await self.memory.append(chat_id, "user", user_message)

        history = await self.memory.get(chat_id)
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history

        # 构建完整的消息列表（system prompt + 对话历史）
        messages = [
            {"role": "system", "content": self.system_prompt},
            *recent,
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=messages,
            )
            reply = response.choices[0].message.content
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 移除刚添加的 user message，保持历史一致性
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
