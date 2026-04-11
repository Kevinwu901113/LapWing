"""用户画像提取器 — 从对话中自动提取用户信息并存储。"""

import asyncio
import json
import logging
import re

from config.settings import FACT_EXTRACT_IDLE_SECONDS, FACT_EXTRACT_TURN_THRESHOLD
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.memory.fact_extractor")

# 提取时使用的最近对话条数（控制 token 用量）
_EXTRACTION_WINDOW = 10


class FactExtractor:
    """从对话中自动提取用户画像信息。

    触发条件（满足任一即触发）：
    - 对话轮次达到 FACT_EXTRACT_TURN_THRESHOLD
    - 用户空闲超过 FACT_EXTRACT_IDLE_SECONDS 秒
    """

    def __init__(self, memory, router):
        self._memory = memory
        self._router = router
        self._turn_counts: dict[str, int] = {}
        self._idle_tasks: dict[str, asyncio.Task] = {}
        self._running_tasks: dict[str, set[asyncio.Task]] = {}
        self._extracting: set[str] = set()
        self._prompt_template: str | None = None

    @property
    def _extract_prompt(self) -> str:
        """懒加载提取 prompt 模板。"""
        if self._prompt_template is None:
            self._prompt_template = load_prompt("memory_extract")
        return self._prompt_template

    def notify(self, chat_id: str) -> None:
        """通知有新消息到达，更新轮次计数并重置空闲计时器。

        每次用户发消息时调用。如果达到轮次阈值，立即触发提取（fire-and-forget）。
        同时启动空闲计时器，在用户沉默 FACT_EXTRACT_IDLE_SECONDS 秒后也触发提取。
        """
        # 取消旧的空闲计时器，重新开始计时
        if chat_id in self._idle_tasks:
            self._idle_tasks[chat_id].cancel()

        # 启动新的空闲计时器
        self._idle_tasks[chat_id] = asyncio.create_task(self._idle_trigger(chat_id))

        # 更新轮次计数
        self._turn_counts[chat_id] = self._turn_counts.get(chat_id, 0) + 1

        # 达到轮次阈值时立即触发提取，同时取消刚创建的空闲计时器（避免重复提取）
        if self._turn_counts[chat_id] >= FACT_EXTRACT_TURN_THRESHOLD:
            self._turn_counts[chat_id] = 0
            if chat_id in self._idle_tasks:
                self._idle_tasks[chat_id].cancel()
                del self._idle_tasks[chat_id]
            self._spawn_extraction(chat_id)

    def _spawn_extraction(self, chat_id: str) -> None:
        """创建并跟踪提取任务，便于按 chat_id 取消。"""
        task = asyncio.create_task(self._run_extraction(chat_id))
        bucket = self._running_tasks.setdefault(chat_id, set())
        bucket.add(task)

        def _cleanup(_task: asyncio.Task) -> None:
            tasks = self._running_tasks.get(chat_id)
            if not tasks:
                return
            tasks.discard(_task)
            if not tasks:
                self._running_tasks.pop(chat_id, None)

        task.add_done_callback(_cleanup)

    async def _idle_trigger(self, chat_id: str) -> None:
        """空闲计时器协程 — 等待后触发提取，被取消时安静退出。"""
        await asyncio.sleep(FACT_EXTRACT_IDLE_SECONDS)
        await self._run_extraction(chat_id)

    async def _run_extraction(self, chat_id: str) -> None:
        """核心提取逻辑：分析对话，调用 LLM 提取 facts，写入存储。"""
        # 防止同一 chat 并发提取
        if chat_id in self._extracting:
            logger.debug(f"[{chat_id}] 提取已在进行中，跳过")
            return

        self._extracting.add(chat_id)
        try:
            # 获取最近对话历史
            history = await self._memory.get(chat_id)
            recent = history[-_EXTRACTION_WINDOW:] if len(history) > _EXTRACTION_WINDOW else history

            if not recent:
                return

            # 获取已知 facts（注入 prompt 以避免重复提取）
            existing = await self._memory.get_user_facts(chat_id)

            # 组装提取 prompt
            conversation_text = self._format_conversation(recent)
            existing_text = self._format_existing_facts(existing)
            prompt = self._extract_prompt.format(
                conversation=conversation_text,
                existing_facts=existing_text,
            )

            # 用 tool 模型（低成本）执行提取
            messages = [{"role": "user", "content": prompt}]
            response = await self._router.complete(
                messages,
                slot="memory_processing",
                max_tokens=512,
                session_key=f"chat:{chat_id}",
                origin="memory.fact_extractor.extract",
            )

            # 解析并存储提取结果
            facts = self._parse_result(response)

            # MemoryGuard 安全扫描
            from config.settings import MEMORY_GUARD_ENABLED
            if MEMORY_GUARD_ENABLED:
                from src.guards.memory_guard import MemoryGuard
                guard = MemoryGuard()
                safe_facts = []
                for fact in facts:
                    scan = guard.scan(str(fact.get("fact_value", "")))
                    if scan.passed:
                        safe_facts.append(fact)
                    else:
                        logger.warning("用户画像提取被安全拦截: %s — %s",
                                       fact.get("fact_key", ""), scan.threats)
                facts = safe_facts

            for fact in facts:
                await self._memory.set_user_fact(chat_id, fact["fact_key"], fact["fact_value"])

            if facts:
                logger.info(f"[{chat_id}] 提取了 {len(facts)} 条用户画像信息")
                from src.logging.event_logger import events
                for fact in facts:
                    events.log("memory", "fact_extracted",
                        content=f"{fact['fact_key']}: {fact['fact_value']}",
                        chat_id=chat_id,
                    )
            else:
                logger.debug(f"[{chat_id}] 本轮对话无新用户信息可提取")

        except Exception as e:
            logger.warning(f"[{chat_id}] 用户画像提取失败: {e}")
        finally:
            self._extracting.discard(chat_id)

    def _format_conversation(self, messages: list[dict]) -> str:
        """将消息列表格式化为可读文本。"""
        lines = []
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "Lapwing"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def _format_existing_facts(self, facts: list[dict]) -> str:
        """将已知 facts 格式化为可读文本。"""
        if not facts:
            return "（暂无已知信息）"
        return "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)

    def _parse_result(self, text: str) -> list[dict]:
        """防御性 JSON 解析，返回有效的 fact 列表。失败时返回空列表。"""
        try:
            # 去掉 markdown code fence（LLM 可能包裹在 ```json ... ``` 中）
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
            cleaned = cleaned.strip()

            data = json.loads(cleaned)

            if not isinstance(data, list):
                return []

            result = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                key = item.get("fact_key", "")
                value = item.get("fact_value", "")
                if key and value:
                    result.append({"fact_key": key, "fact_value": value})
            return result

        except Exception as e:
            logger.debug(f"解析提取结果失败: {e!r}")
            return []

    async def force_extraction(self, chat_id: str) -> None:
        """外部主动触发一次提取（供 HeartbeatEngine 的慢心跳调用）。"""
        await self._run_extraction(chat_id)

    async def clear_chat_state(self, chat_id: str) -> None:
        """清理某个 chat 的提取器状态，并取消其待处理任务。"""
        self._turn_counts.pop(chat_id, None)

        idle_task = self._idle_tasks.pop(chat_id, None)
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)

        running_tasks = list(self._running_tasks.pop(chat_id, set()))
        for task in running_tasks:
            task.cancel()
        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)

        self._extracting.discard(chat_id)

    async def shutdown(self) -> None:
        """关闭时取消所有待处理的空闲计时器。"""
        for task in self._idle_tasks.values():
            task.cancel()
        if self._idle_tasks:
            await asyncio.gather(*self._idle_tasks.values(), return_exceptions=True)
        self._idle_tasks.clear()

        all_running_tasks: list[asyncio.Task] = []
        for tasks in self._running_tasks.values():
            all_running_tasks.extend(tasks)
        for task in all_running_tasks:
            task.cancel()
        if all_running_tasks:
            await asyncio.gather(*all_running_tasks, return_exceptions=True)
        self._running_tasks.clear()
