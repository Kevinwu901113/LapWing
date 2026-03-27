"""自省引擎 — 每日回顾对话并提取学习日志，也处理实时纠正。"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DATA_DIR
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.self_reflection")

_LEARNINGS_DIR = DATA_DIR / "learnings"

# 用户纠正行为的 regex 快速匹配模式
_CORRECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"你不应该|你不要|以后别|以后不要",
        r"记住.*不要|记住.*别",
        r"你说错了|不是这样|你理解错了|你搞错了",
        r"我说的不是这个意思",
        r"别再.{0,10}了|不要再.{0,10}了",
        r"你不用.{0,10}每次|你不需要.{0,10}总是",
    ]
]


def is_correction(message: str) -> bool:
    """快速判断用户消息是否是对 Lapwing 行为的纠正。"""
    return any(p.search(message) for p in _CORRECTION_PATTERNS)


class SelfReflection:
    """Lapwing 的自省引擎。

    - reflect_on_day: 回顾一天的对话，生成学习日志
    - reflect_on_correction: 处理用户实时纠正，追加到当天日志
    """

    def __init__(self, memory, router) -> None:
        self._memory = memory
        self._router = router
        _LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)

    async def reflect_on_day(self, chat_id: str, date_str: str) -> str | None:
        """回顾指定日期的对话，提取经验，写入 data/learnings/YYYY-MM-DD.md。

        Args:
            chat_id: Telegram 对话 ID
            date_str: 日期字符串，格式 YYYY-MM-DD

        Returns:
            学习内容文本（如有），否则 None。
        """
        messages = await self._memory.get_conversations_for_date(chat_id, date_str)
        if not messages:
            logger.debug(f"[self_reflection] {date_str} 无对话记录，跳过")
            return None

        conversation_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
            for m in messages
        )

        prompt = load_prompt("self_reflection").replace("{conversation_text}", conversation_text)
        try:
            result = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=512,
                session_key=f"chat:{chat_id}",
                origin="core.self_reflection.daily",
            )
        except Exception as exc:
            logger.warning(f"[self_reflection] LLM 调用失败: {exc}")
            return None

        result = result.strip()
        if not result or result == "（无）":
            logger.debug(f"[self_reflection] {date_str} 无值得记录的内容")
            return None

        await asyncio.to_thread(self._write_learning, date_str, f"## 来自日常对话\n{result}\n")
        logger.info(f"[self_reflection] {date_str} 学习日志已写入")
        return result

    async def reflect_on_correction(
        self,
        chat_id: str,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """处理用户的实时纠正，追加到当天学习日志。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户发出的纠正消息
            context: 最近的对话历史（用于理解纠正的背景）
        """
        context_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
            for m in context[-6:]  # 最近 6 条
        )

        prompt = (
            "你是 Lapwing，请用一句话总结以下对话中用户的纠正内容，"
            "格式为：「我应该[改变什么]」。不要解释，直接输出那句话。\n\n"
            f"对话片段：\n{context_text}\n\n用户纠正：{user_message}"
        )

        try:
            summary = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=128,
                session_key=f"chat:{chat_id}",
                origin="core.self_reflection.correction",
            )
        except Exception as exc:
            logger.warning(f"[self_reflection] 纠正摘要失败: {exc}")
            return None

        summary = summary.strip()
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"## 来自用户纠正\n- {summary}\n"
        await asyncio.to_thread(self._write_learning, date_str, entry)
        logger.info(f"[self_reflection] 纠正已记录: {summary[:60]}")
        return summary

    def _write_learning(self, date_str: str, content: str) -> None:
        """写入学习日志（同步，在线程中调用）。"""
        path = _LEARNINGS_DIR / f"{date_str}.md"
        header = f"# {date_str} 学习笔记\n\n"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + "\n" + content, encoding="utf-8")
        else:
            path.write_text(header + content, encoding="utf-8")
