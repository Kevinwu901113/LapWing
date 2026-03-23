"""Lapwing 心跳引擎 — 自主感知与行动循环。"""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("lapwing.heartbeat")


@dataclass
class SenseContext:
    """一次心跳的环境快照。"""
    beat_type: str                    # "fast" | "slow"
    now: datetime                     # 当前时间（含时区）
    last_interaction: datetime | None # 上次用户发消息的时间
    silence_hours: float              # 距上次对话已沉默多少小时
    user_facts_summary: str           # 用户画像文字摘要
    recent_memory_summary: str        # 最近对话摘要（慢心跳填充，快心跳为空字符串）
    chat_id: str                      # 目标用户的 chat_id
    top_interests_summary: str = "（暂无明显兴趣）"


class HeartbeatAction(ABC):
    """所有心跳 action 实现的抽象基类。"""
    name: str
    description: str
    beat_types: list[str]

    @abstractmethod
    async def execute(self, ctx: SenseContext, brain, bot) -> None: ...


class ActionRegistry:
    """注册并检索 HeartbeatAction 实例。"""

    def __init__(self) -> None:
        self._actions: dict[str, HeartbeatAction] = {}

    def register(self, action: HeartbeatAction) -> None:
        self._actions[action.name] = action

    def get_for_beat(self, beat_type: str) -> list[HeartbeatAction]:
        return [a for a in self._actions.values() if beat_type in a.beat_types]

    def get_by_name(self, name: str) -> HeartbeatAction | None:
        return self._actions.get(name)

    def as_descriptions(self, beat_type: str) -> list[dict]:
        return [
            {"name": a.name, "description": a.description}
            for a in self.get_for_beat(beat_type)
        ]



class SenseLayer:
    """为指定 chat_id 构建 SenseContext 快照。"""

    _NO_INTERACTION_HOURS = 24 * 365 * 10  # 无交互历史时的占位大值

    def __init__(self, memory) -> None:
        self._memory = memory

    async def build(self, chat_id: str, beat_type: str) -> SenseContext:
        now = datetime.now(timezone.utc)

        last = await self._memory.get_last_interaction(chat_id)
        silence_hours = (
            (now - last).total_seconds() / 3600
            if last is not None
            else self._NO_INTERACTION_HOURS
        )

        facts = await self._memory.get_user_facts(chat_id)
        user_facts_summary = (
            "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)
            if facts else "（暂无已知信息）"
        )

        top_interests = await self._memory.get_top_interests(chat_id, limit=5)
        top_interests_summary = self._format_top_interests(top_interests)

        recent_memory_summary = ""
        if beat_type == "slow":
            history = await self._memory.get(chat_id)
            recent = history[-20:] if len(history) > 20 else history
            recent_memory_summary = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
                for m in recent
            )

        return SenseContext(
            beat_type=beat_type,
            now=now,
            last_interaction=last,
            silence_hours=silence_hours,
            user_facts_summary=user_facts_summary,
            recent_memory_summary=recent_memory_summary,
            chat_id=chat_id,
            top_interests_summary=top_interests_summary,
        )

    def _format_top_interests(self, interests: list[dict]) -> str:
        if not interests:
            return "（暂无明显兴趣）"
        return "\n".join(
            f"- {item['topic']}（{item['weight']:.1f}）"
            for item in interests
        )


class HeartbeatEngine:
    """心跳引擎：调度、感知、决策、执行。"""

    def __init__(self, brain, bot) -> None:
        self._brain = brain
        self._bot = bot
        self._sense = SenseLayer(brain.memory)
        self.registry = ActionRegistry()
        self._scheduler = None
        self._running_tasks: set[asyncio.Task] = set()
        self._decision_prompt: str | None = None

    @property
    def _decision_prompt_text(self) -> str:
        if self._decision_prompt is None:
            from src.core.prompt_loader import load_prompt
            self._decision_prompt = load_prompt("heartbeat_decision")
        return self._decision_prompt

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
        from config.settings import (
            HEARTBEAT_ENABLED,
            HEARTBEAT_FAST_INTERVAL_MINUTES,
            HEARTBEAT_SLOW_HOUR,
        )

        if not HEARTBEAT_ENABLED:
            logger.info("心跳已禁用（HEARTBEAT_ENABLED=false）")
            return

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._run_beat,
            IntervalTrigger(minutes=HEARTBEAT_FAST_INTERVAL_MINUTES),
            args=["fast"],
            id="heartbeat_fast",
        )
        self._scheduler.add_job(
            self._run_beat,
            CronTrigger(hour=HEARTBEAT_SLOW_HOUR),
            args=["slow"],
            id="heartbeat_slow",
        )
        self._scheduler.start()
        logger.info(
            f"心跳已启动：快心跳每 {HEARTBEAT_FAST_INTERVAL_MINUTES} 分钟，"
            f"慢心跳每天 {HEARTBEAT_SLOW_HOUR:02d}:00"
        )

    async def shutdown(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)
        logger.info("心跳引擎已关闭")

    async def _run_beat(self, beat_type: str) -> None:
        """一次心跳：为所有已知用户执行 Sense → Decide → Act。"""
        chat_ids = await self._brain.memory.get_all_chat_ids()
        for chat_id in chat_ids:
            task = asyncio.create_task(self._process_user(chat_id, beat_type))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _process_user(self, chat_id: str, beat_type: str) -> None:
        try:
            ctx = await self._sense.build(chat_id, beat_type)
            action_names = await self._decide(ctx)
            for name in action_names:
                action = self.registry.get_by_name(name)
                if action:
                    await action.execute(ctx, self._brain, self._bot)
        except Exception as e:
            logger.exception(f"[{chat_id}] 心跳处理失败: {e}")

    async def _decide(self, ctx: SenseContext) -> list[str]:
        """调用 NIM 决定本次心跳执行哪些 actions。"""
        available = self.registry.as_descriptions(ctx.beat_type)
        if not available:
            return []

        now_str = ctx.now.strftime("%Y-%m-%d %H:%M %Z")
        prompt = self._decision_prompt_text.format(
            beat_type=ctx.beat_type,
            now=now_str,
            silence_hours=ctx.silence_hours,
            user_facts_summary=ctx.user_facts_summary,
            top_interests_summary=ctx.top_interests_summary,
            available_actions=json.dumps(available, ensure_ascii=False),
        )
        try:
            response = await self._brain.router.complete(
                [{"role": "system", "content": prompt}, {"role": "user", "content": "请做出判断"}],
                purpose="heartbeat",
                max_tokens=256,
            )
            return self._parse_decision(response)
        except Exception as e:
            logger.warning(f"[{ctx.chat_id}] 心跳决策失败: {e}")
            return []

    def _parse_decision(self, text: str) -> list[str]:
        """防御性解析 NIM 返回的决策 JSON，失败时返回空列表。"""
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            actions = data.get("actions", [])
            if isinstance(actions, list):
                return [a for a in actions if isinstance(a, str)]
        except Exception:
            pass
        return []
