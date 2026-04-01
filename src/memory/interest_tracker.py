"""兴趣追踪器 - 从对话中提取用户兴趣并更新图谱。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from config.settings import INTEREST_EXTRACT_TURN_THRESHOLD, INTERESTS_PATH
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
        self._tasks_by_chat: dict[str, set[asyncio.Task]] = {}
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
        chat_tasks = self._tasks_by_chat.setdefault(chat_id, set())
        chat_tasks.add(task)

        def _cleanup(done_task: asyncio.Task) -> None:
            self._tasks.discard(done_task)
            bucket = self._tasks_by_chat.get(chat_id)
            if not bucket:
                return
            bucket.discard(done_task)
            if not bucket:
                self._tasks_by_chat.pop(chat_id, None)

        task.add_done_callback(_cleanup)

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
                slot="memory_processing",
                max_tokens=256,
                session_key=f"chat:{chat_id}",
                origin="memory.interest_tracker.extract",
            )
            topics = self._parse_result(response)
            for item in topics:
                await self._memory.bump_interest(chat_id, item["topic"], item["weight"])

            if topics:
                logger.info(f"[{chat_id}] 提取了 {len(topics)} 个兴趣话题")
                await self._update_interests_file(chat_id, topics)
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

    async def _update_interests_file(self, chat_id: str, topics: list[dict]) -> None:
        """将新发现的兴趣追加到 interests.md。"""
        if not INTERESTS_PATH.exists():
            return

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_entries = "\n".join(
            f"- {t['topic']}（{date_str}，权重 {t['weight']:.1f}）"
            for t in topics
        )

        def _update():
            text = INTERESTS_PATH.read_text(encoding="utf-8")
            if "## Kevin 的兴趣" in text:
                idx = text.index("## Kevin 的兴趣")
                next_section = text.find("\n## ", idx + 1)
                if next_section == -1:
                    text_new = text.rstrip() + "\n" + new_entries + "\n"
                else:
                    text_new = text[:next_section] + new_entries + "\n\n" + text[next_section:]
                INTERESTS_PATH.write_text(text_new, encoding="utf-8")
            else:
                logger.warning(f"interests.md 中找不到 '## Kevin 的兴趣' 段落，追加到文件末尾")
                text_new = text.rstrip() + "\n" + new_entries + "\n"
                INTERESTS_PATH.write_text(text_new, encoding="utf-8")

        try:
            await asyncio.to_thread(_update)
        except Exception as exc:
            logger.warning(f"[{chat_id}] 更新兴趣文件失败: {exc}")

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._tasks_by_chat.clear()

    async def clear_chat_state(self, chat_id: str) -> None:
        """清理某个 chat 的兴趣提取状态，并取消其待处理任务。"""
        self._turn_counts.pop(chat_id, None)

        tasks = list(self._tasks_by_chat.pop(chat_id, set()))
        for task in tasks:
            task.cancel()
            self._tasks.discard(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._extracting.discard(chat_id)
