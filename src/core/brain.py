"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from config.settings import MAX_HISTORY_TURNS

if TYPE_CHECKING:
    from src.memory.interest_tracker import InterestTracker
    from src.core.self_reflection import SelfReflection
    from src.core.knowledge_manager import KnowledgeManager

logger = logging.getLogger("lapwing.brain")


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path):
        self.router = LLMRouter()
        self.memory = ConversationMemory(db_path)
        self.fact_extractor = FactExtractor(self.memory, self.router)
        self.interest_tracker: InterestTracker | None = None
        self.self_reflection: SelfReflection | None = None
        self.knowledge_manager: KnowledgeManager | None = None
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

    def _split_facts(self, facts: list[dict]) -> tuple[list[dict], list[dict]]:
        """将普通事实与 memory summary 分离。"""
        regular_facts: list[dict] = []
        memory_summaries: list[dict] = []
        for fact in facts:
            if str(fact.get("fact_key", "")).startswith("memory_summary_"):
                memory_summaries.append(fact)
            else:
                regular_facts.append(fact)
        return regular_facts, memory_summaries

    def _format_recent_memory_summaries(self, summaries: list[dict]) -> str:
        """格式化最近聊过的事摘要。"""
        latest = sorted(
            summaries,
            key=lambda item: str(item.get("fact_key", "")),
            reverse=True,
        )[:3]
        return "\n".join(
            f"- {item['fact_key'].removeprefix('memory_summary_')}: {item['fact_value']}"
            for item in latest
        )

    async def _build_system_prompt(self, chat_id: str, user_message: str = "") -> str:
        """组合基础人格 prompt、用户画像信息和相关知识笔记。"""
        base = self.system_prompt
        facts = await self.memory.get_user_facts(chat_id)
        sections = [base]

        if facts:
            regular_facts, memory_summaries = self._split_facts(facts)

            if regular_facts:
                facts_text = "\n".join(
                    f"- {fact['fact_key']}: {fact['fact_value']}" for fact in regular_facts
                )
                sections.append(
                    "## 你对这个用户的了解\n\n"
                    "以下是你从之前对话中了解到的关于这个用户的信息。"
                    "在合适的时候可以自然地引用，但不要刻意提起。\n\n"
                    f"{facts_text}"
                )

            if memory_summaries:
                summaries_text = self._format_recent_memory_summaries(memory_summaries)
                sections.append(
                    "## 最近聊过的事\n\n"
                    "以下是你们最近几次对话的重要脉络。"
                    "当用户延续之前的话题时，可以自然接上。\n\n"
                    f"{summaries_text}"
                )

        # 注入相关知识笔记
        if user_message and self.knowledge_manager is not None:
            notes = self.knowledge_manager.get_relevant_notes(user_message)
            if notes:
                notes_text = "\n\n".join(
                    f"### {note['topic']}\n{note['content']}"
                    for note in notes
                )
                sections.append(
                    "## 你积累的相关知识\n\n"
                    "以下是你之前浏览网页时记录的笔记，与当前话题可能相关。"
                    "如果对话中用到了，可以自然地引用或补充。\n\n"
                    f"{notes_text}"
                )

        return "\n\n".join(sections)

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
        if self.interest_tracker is not None:
            self.interest_tracker.notify(chat_id)

        # 实时纠正检测：异步触发自省，不阻塞主回复流程
        if self.self_reflection is not None:
            from src.core.self_reflection import is_correction
            if is_correction(user_message):
                import asyncio
                history = await self.memory.get(chat_id)
                asyncio.create_task(
                    self.self_reflection.reflect_on_correction(
                        chat_id, user_message, list(history)
                    )
                )

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

        # 动态组合 system prompt（基础人格 + 用户画像 + 知识笔记）
        system_content = await self._build_system_prompt(chat_id, user_message)

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
