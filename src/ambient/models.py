"""环境感知数据模型——TimeContext、AmbientEntry、Interest、PreparationStatus。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimeContext:
    """当前时间的丰富语境信息。纯计算产物，零外部调用。"""

    datetime_str: str       # "2026年4月22日 15:24"
    weekday: str            # "星期三"
    time_period: str        # "下午"
    lunar_date: str | None  # "三月初六"
    season: str             # "春季"
    upcoming_events: tuple[str, ...]  # ("劳动节假期（9天后）",)

    def to_prompt_text(self) -> str:
        """格式化为可注入 system prompt 的文本段落。"""
        parts: list[str] = []
        parts.append(
            f"现在是{self.datetime_str}，{self.weekday}，"
            f"{self.time_period}。"
        )
        if self.lunar_date:
            parts[0] = parts[0].rstrip("。") + f"（农历{self.lunar_date}）。"
        parts.append(f"{self.season}。")
        if self.upcoming_events:
            parts.append("".join(self.upcoming_events))
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class AmbientEntry:
    """环境知识缓存条目——从 AmbientKnowledgeStore 读出的视图对象。"""

    key: str
    category: str
    topic: str
    data: str               # JSON 字符串
    summary: str
    fetched_at: str         # ISO 8601
    expires_at: str         # ISO 8601
    source: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Interest:
    """kevin_interests.md 中的单个兴趣条目。"""

    name: str               # "MLB棒球"
    priority: str           # "high" | "medium" | "low"
    details: str            # "道奇队、NL West赛区"
    frequency: str          # "daily" | "weekly" | "event_driven"
    typical_time: str       # "morning" | "evening" | "anytime"
    source: str             # "显式声明" | "观察"
    notes: str              # 备注
    active: bool = True


@dataclass(frozen=True, slots=True)
class PreparationStatus:
    """某个兴趣主题的准备状态——用于注入 inner tick prompt。"""

    interest_name: str
    priority: str           # "high" | "medium" | "low"
    has_data: bool
    is_fresh: bool          # True = 缓存未过期
    cached_summary: str     # "" = 无数据
    staleness_hours: float  # 距上次获取的小时数，无数据时为 0.0
