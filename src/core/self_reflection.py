"""自省引擎 — 每日回顾对话并提取学习日志。"""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import JOURNAL_DIR
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.self_reflection")


class SelfReflection:
    """Lapwing 的自省引擎。

    - reflect_on_day: 回顾一天的对话，生成学习日志
    """

    def __init__(self, memory, router) -> None:
        self._memory = memory
        self._router = router
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    async def reflect_on_day(self, chat_id: str, date_str: str) -> str | None:
        """回顾指定日期的对话，提取经验，写入 JOURNAL_DIR/YYYY-MM-DD.md。

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

    def _write_learning(self, date_str: str, content: str) -> None:
        """写入学习日志（同步，在线程中调用）。"""
        path = JOURNAL_DIR / f"{date_str}.md"
        header = f"# {date_str} 学习笔记\n\n"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + "\n" + content, encoding="utf-8")
        else:
            path.write_text(header + content, encoding="utf-8")
