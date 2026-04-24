"""准备引擎——解析兴趣画像、计算准备状态、格式化 prompt 注入文本。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.ambient.models import Interest, PreparationStatus

if TYPE_CHECKING:
    from src.ambient.ambient_knowledge import AmbientKnowledgeStore

logger = logging.getLogger("lapwing.ambient.preparation_engine")

# ── 优先级映射 ──────────────────────────────────────────────────────

_PRIORITY_MAP: dict[str, str] = {
    "高": "high",
    "中": "medium",
    "低": "low",
}

# ── 字段 key → Interest 字段名 ──────────────────────────────────────

_FIELD_MAP: dict[str, str] = {
    "具体关注": "details",
    "频率": "frequency",
    "典型时段": "typical_time",
    "来源": "source",
    "备注": "notes",
}

_SECTION_RE = re.compile(r"^##\s+(.+?)（")
_INTEREST_RE = re.compile(r"^###\s+(.+)$")
_FIELD_RE = re.compile(r"^-\s+(.+?)：(.+)$")


# ═══════════════════════════════════════════════════════════════════
# InterestProfile
# ═══════════════════════════════════════════════════════════════════

class InterestProfile:
    """解析和管理 kevin_interests.md。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[Interest]:
        """从 markdown 文件解析兴趣列表。缺失字段给默认值。"""
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("无法读取兴趣画像: %s", self._path)
            return []

        interests: list[Interest] = []
        current_priority = "medium"
        current_name: str | None = None
        fields: dict[str, str] = {}

        for line in text.splitlines():
            line = line.rstrip()

            # ## 高优先级（每日关注）
            m = _SECTION_RE.match(line)
            if m:
                # 保存上一个兴趣
                if current_name is not None:
                    interests.append(self._build_interest(current_name, current_priority, fields))
                    current_name = None
                    fields = {}
                section_label = m.group(1).strip()
                for cn_key, en_val in _PRIORITY_MAP.items():
                    if cn_key in section_label:
                        current_priority = en_val
                        break
                continue

            # ### MLB棒球
            m = _INTEREST_RE.match(line)
            if m:
                if current_name is not None:
                    interests.append(self._build_interest(current_name, current_priority, fields))
                current_name = m.group(1).strip()
                fields = {}
                continue

            # - 具体关注：道奇队、NL West赛区
            m = _FIELD_RE.match(line)
            if m and current_name is not None:
                raw_key = m.group(1).strip()
                raw_val = m.group(2).strip()
                field_name = _FIELD_MAP.get(raw_key)
                if field_name is not None:
                    # 频率字段可能带括号注释：daily（赛季中）→ 取第一个词
                    if field_name == "frequency":
                        raw_val = raw_val.split("（")[0].strip()
                    fields[field_name] = raw_val

        # 最后一个
        if current_name is not None:
            interests.append(self._build_interest(current_name, current_priority, fields))

        return interests

    def save(self, interests: list[Interest]) -> None:
        """原子写入兴趣列表到 markdown 文件。"""
        from datetime import date

        lines: list[str] = [
            "# Kevin 兴趣画像",
            "<!-- Lapwing维护。记录Kevin关注的信息领域，驱动准备系统的信息预取。 -->",
            f"<!-- 最后更新：{date.today().isoformat()} -->",
            "",
        ]

        by_priority: dict[str, list[Interest]] = {"high": [], "medium": [], "low": []}
        for interest in interests:
            bucket = by_priority.get(interest.priority, by_priority["medium"])
            bucket.append(interest)

        section_headers = {
            "high": "## 高优先级（每日关注）",
            "medium": "## 中优先级（定期关注）",
            "low": "## 低优先级（事件驱动）",
        }

        for priority in ("high", "medium", "low"):
            bucket = by_priority[priority]
            if not bucket:
                continue
            lines.append(section_headers[priority])
            lines.append("")
            for interest in bucket:
                lines.append(f"### {interest.name}")
                if interest.details:
                    lines.append(f"- 具体关注：{interest.details}")
                if interest.frequency:
                    lines.append(f"- 频率：{interest.frequency}")
                if interest.typical_time:
                    lines.append(f"- 典型时段：{interest.typical_time}")
                if interest.source:
                    lines.append(f"- 来源：{interest.source}")
                if interest.notes:
                    lines.append(f"- 备注：{interest.notes}")
                if not interest.active:
                    lines.append("- 状态：已停用")
                lines.append("")

        content = "\n".join(lines)

        # 原子写入
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(self._path))
        except BaseException:
            os.close(fd) if not os.get_inheritable(fd) else None
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _build_interest(name: str, priority: str, fields: dict[str, str]) -> Interest:
        return Interest(
            name=name,
            priority=priority,
            details=fields.get("details", ""),
            frequency=fields.get("frequency", ""),
            typical_time=fields.get("typical_time", ""),
            source=fields.get("source", ""),
            notes=fields.get("notes", ""),
            active=True,
        )


# ═══════════════════════════════════════════════════════════════════
# PreparationEngine
# ═══════════════════════════════════════════════════════════════════

class PreparationEngine:
    """根据兴趣画像和 AmbientKnowledgeStore 计算准备状态。"""

    def __init__(
        self,
        interest_profile: InterestProfile,
        ambient_store: AmbientKnowledgeStore,
    ) -> None:
        self._profile = interest_profile
        self._store = ambient_store

    async def get_preparation_status(self) -> list[PreparationStatus]:
        """对每个活跃兴趣，计算其缓存数据的新鲜度。"""
        interests = self._profile.load()
        statuses: list[PreparationStatus] = []
        now = datetime.now(timezone.utc)

        for interest in interests:
            if not interest.active:
                continue
            entries = await self._store.get_by_category(interest.name)
            if not entries:
                statuses.append(PreparationStatus(
                    interest_name=interest.name,
                    priority=interest.priority,
                    has_data=False,
                    is_fresh=False,
                    cached_summary="",
                    staleness_hours=0.0,
                ))
                continue

            # 取最新的一条
            best = max(entries, key=lambda e: e.fetched_at)
            try:
                fetched_dt = datetime.fromisoformat(best.fetched_at)
                if fetched_dt.tzinfo is None:
                    fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
                staleness = (now - fetched_dt).total_seconds() / 3600.0
            except (ValueError, TypeError):
                staleness = 999.0

            try:
                expires_dt = datetime.fromisoformat(best.expires_at)
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                is_fresh = now < expires_dt
            except (ValueError, TypeError):
                is_fresh = False

            statuses.append(PreparationStatus(
                interest_name=interest.name,
                priority=interest.priority,
                has_data=True,
                is_fresh=is_fresh,
                cached_summary=best.summary,
                staleness_hours=round(staleness, 1),
            ))

        return statuses

    async def format_for_prompt(self) -> str:
        """渲染准备状态为 prompt 可注入文本。空列表返回空字符串。"""
        statuses = await self.get_preparation_status()
        if not statuses:
            return ""

        lines: list[str] = []
        for s in statuses:
            if not s.has_data:
                lines.append(f"- {s.interest_name}（{s.priority}）：❌ 无数据")
            elif s.is_fresh:
                age = self._format_age(s.staleness_hours)
                lines.append(f"- {s.interest_name}（{s.priority}）：✅ {s.cached_summary}（{age}前更新）")
            else:
                age = self._format_age(s.staleness_hours)
                lines.append(f"- {s.interest_name}（{s.priority}）：🔄 数据已过期（{age}前更新）")

        return "\n".join(lines)

    @staticmethod
    def _format_age(hours: float) -> str:
        if hours < 1:
            return f"{int(hours * 60)}分钟"
        if hours < 24:
            return f"{hours:.0f}小时"
        return f"{hours / 24:.0f}天"
