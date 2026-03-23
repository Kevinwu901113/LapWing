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
