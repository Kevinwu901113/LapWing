"""兴趣追踪器 - 从对话中提取用户兴趣并更新图谱。"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from config.settings import INTEREST_EXTRACT_TURN_THRESHOLD
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.interest_tracker")

_EXTRACTION_WINDOW = 10


class InterestTracker:
    """从对话中提取兴趣话题并更新权重图谱。"""

    def __init__(self, memory, router) -> None:
        self._memory = memory
        self._router = router
        self._turn_counts: dict[str, int] = {}
        self._extracting: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._prompt_template: str | None = None

    @property
    def _extract_prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("interest_extract")
        return self._prompt_template

    def notify(self, chat_id: str) -> None:
        self._turn_counts[chat_id] = self._turn_counts.get(chat_id, 0) + 1
        if self._turn_counts[chat_id] < INTEREST_EXTRACT_TURN_THRESHOLD:
            return

        self._turn_counts[chat_id] = 0
        task = asyncio.create_task(self._extract(chat_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _extract(self, chat_id: str) -> None:
        if chat_id in self._extracting:
            logger.debug(f"[{chat_id}] 兴趣提取已在进行中，跳过")
            return

        self._extracting.add(chat_id)
        try:
            history = await self._memory.get(chat_id)
            recent = history[-_EXTRACTION_WINDOW:] if len(history) > _EXTRACTION_WINDOW else history
            if not recent:
                return

            conversation = "\n".join(
                f"{'用户' if msg['role'] == 'user' else 'Lapwing'}: {msg['content']}"
                for msg in recent
            )
            prompt = self._extract_prompt.format(conversation=conversation)

            response = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=256,
            )
            topics = self._parse_result(response)
            for item in topics:
                await self._memory.bump_interest(chat_id, item["topic"], item["weight"])

            if topics:
                logger.info(f"[{chat_id}] 提取了 {len(topics)} 个兴趣话题")
        except Exception as exc:
            logger.warning(f"[{chat_id}] 兴趣提取失败: {exc}")
        finally:
            self._extracting.discard(chat_id)

    @staticmethod
    def _parse_result(text: str) -> list[dict]:
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            if not isinstance(data, list):
                return []

            result = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                topic = str(item.get("topic", "")).strip()
                if not topic:
                    continue
                try:
                    weight = float(item.get("weight", 1.0))
                except (TypeError, ValueError):
                    continue
                result.append({"topic": topic, "weight": weight})
            return result
        except Exception as exc:
            logger.debug(f"解析兴趣提取结果失败: {exc!r}")
            return []

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
