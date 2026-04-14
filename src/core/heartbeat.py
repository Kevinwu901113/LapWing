"""Lapwing 心跳引擎 — 自主感知与行动循环。"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("lapwing.core.heartbeat")


@dataclass
class SenseContext:
    """一次心跳的环境快照。"""

    beat_type: str                    # "fast" | "slow" | "minute"
    now: datetime                     # 当前时间（含时区）
    last_interaction: datetime | None # 上次用户发消息的时间
    silence_hours: float              # 距上次对话已沉默多少小时
    user_facts_summary: str           # 用户画像文字摘要
    recent_memory_summary: str        # 最近对话摘要（慢心跳填充，快心跳为空字符串）
    chat_id: str                      # 目标用户的 chat_id
    top_interests_summary: str = "（暂无明显兴趣）"
    now_taipei_hour: int = 0          # 台北时间的小时数，方便 action 判断时段


class HeartbeatAction(ABC):
    """所有心跳 action 实现的抽象基类。"""

    name: str
    description: str
    beat_types: list[str]
    selection_mode: str = "decide"  # "decide" | "always"

    @abstractmethod
    async def execute(self, ctx: SenseContext, brain, send_fn) -> None: ...


class ActionRegistry:
    """注册并检索 HeartbeatAction 实例。"""

    def __init__(self) -> None:
        self._actions: dict[str, HeartbeatAction] = {}

    def register(self, action: HeartbeatAction) -> None:
        self._actions[action.name] = action

    def get_for_beat(
        self,
        beat_type: str,
        selection_mode: str | None = None,
    ) -> list[HeartbeatAction]:
        actions = [a for a in self._actions.values() if beat_type in a.beat_types]
        if selection_mode is None:
            return actions
        return [a for a in actions if getattr(a, "selection_mode", "decide") == selection_mode]

    def get_by_name(self, name: str) -> HeartbeatAction | None:
        return self._actions.get(name)

    def as_descriptions(
        self,
        beat_type: str,
        selection_mode: str = "decide",
    ) -> list[dict]:
        return [
            {"name": a.name, "description": a.description}
            for a in self.get_for_beat(beat_type, selection_mode=selection_mode)
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

        from src.core.vitals import _TAIPEI_TZ
        now_taipei_hour = now.astimezone(_TAIPEI_TZ).hour

        return SenseContext(
            beat_type=beat_type,
            now=now,
            last_interaction=last,
            silence_hours=silence_hours,
            user_facts_summary=user_facts_summary,
            recent_memory_summary=recent_memory_summary,
            chat_id=chat_id,
            top_interests_summary=top_interests_summary,
            now_taipei_hour=now_taipei_hour,
        )

    def _format_top_interests(self, interests: list[dict]) -> str:
        if not interests:
            return "（暂无明显兴趣）"
        return "\n".join(
            f"- {item['topic']}（{item['weight']:.1f}）"
            for item in interests
        )


_HEARTBEAT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要执行的心跳动作列表，空数组表示不执行",
        },
    },
    "required": ["actions"],
}


class ProactiveRuntime:
    """心跳行为编排器：接收 tick，决定并执行 action。"""

    def __init__(self, brain, send_fn, registry: ActionRegistry, sense: SenseLayer) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._registry = registry
        self._sense = sense
        self._decision_prompt: str | None = None

    @property
    def _decision_prompt_text(self) -> str:
        if self._decision_prompt is None:
            from src.core.prompt_loader import load_prompt

            self._decision_prompt = load_prompt("heartbeat_decision")
        return self._decision_prompt

    async def process(self, chat_id: str, beat_type: str) -> None:
        ctx = await self._sense.build(chat_id, beat_type)

        always_actions = self._registry.get_for_beat(beat_type, selection_mode="always")
        await self._execute_actions(always_actions, ctx)

        if beat_type not in {"fast", "slow"}:
            return

        decide_candidates = self._registry.get_for_beat(beat_type, selection_mode="decide")
        if not decide_candidates:
            return

        action_names = await self._decide(ctx, decide_candidates)
        selected_actions: list[HeartbeatAction] = []
        seen: set[str] = set()
        for name in action_names:
            if name in seen:
                continue
            seen.add(name)
            action = self._registry.get_by_name(name)
            if action is None:
                continue
            if beat_type not in action.beat_types:
                continue
            if getattr(action, "selection_mode", "decide") != "decide":
                continue
            selected_actions.append(action)

        await self._execute_actions(selected_actions, ctx)

    async def _execute_actions(self, actions: list[HeartbeatAction], ctx: SenseContext) -> None:
        for action in actions:
            try:
                await action.execute(ctx, self._brain, self._send_fn)
            except Exception as exc:
                logger.exception(f"[{ctx.chat_id}] action {action.name} 执行失败: {exc}")

    async def _decide(self, ctx: SenseContext, available_actions: list[HeartbeatAction]) -> list[str]:
        payload = [
            {"name": action.name, "description": action.description}
            for action in available_actions
        ]
        from src.core.vitals import _TAIPEI_TZ
        now_str = ctx.now.astimezone(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M") + " 台北时间"
        prompt = self._decision_prompt_text.format(
            beat_type=ctx.beat_type,
            now=now_str,
            silence_hours=ctx.silence_hours,
            user_facts_summary=ctx.user_facts_summary,
            top_interests_summary=ctx.top_interests_summary,
            available_actions=json.dumps(payload, ensure_ascii=False),
        )
        try:
            result = await self._brain.router.complete_structured(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请做出判断"},
                ],
                result_schema=_HEARTBEAT_DECISION_SCHEMA,
                result_tool_name="heartbeat_decision",
                result_tool_description="提交心跳决策",
                slot="heartbeat_proactive",
                max_tokens=256,
                session_key=f"chat:{ctx.chat_id}",
                origin=f"heartbeat.decision.{ctx.beat_type}",
            )
            actions = result.get("actions", [])
            return [a for a in actions if isinstance(a, str)]
        except Exception as exc:
            logger.warning(f"[{ctx.chat_id}] 心跳决策失败: {exc}")
            return []


class HeartbeatEngine:
    """心跳引擎：仅负责 tick 调度与分发。"""

    def __init__(self, brain, send_fn) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._sense = SenseLayer(brain.memory)
        self.registry = ActionRegistry()
        self._runtime = ProactiveRuntime(brain=brain, send_fn=send_fn, registry=self.registry, sense=self._sense)
        self._scheduler = None
        self._running_tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
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
            self._run_tick,
            IntervalTrigger(minutes=HEARTBEAT_FAST_INTERVAL_MINUTES),
            args=["fast"],
            id="heartbeat_fast",
        )
        self._scheduler.add_job(
            self._run_tick,
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

    async def _run_tick(self, beat_type: str) -> None:
        """一次 tick：为所有已知用户触发对应主动行为。"""
        from src.core.vitals import update_last_active
        update_last_active()

        chat_ids = await self._brain.memory.get_all_chat_ids()
        for chat_id in chat_ids:
            task = asyncio.create_task(self._process_user(chat_id, beat_type))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _process_user(self, chat_id: str, beat_type: str) -> None:
        try:
            await self._runtime.process(chat_id=chat_id, beat_type=beat_type)
        except Exception as exc:
            logger.exception(f"[{chat_id}] 心跳处理失败: {exc}")
