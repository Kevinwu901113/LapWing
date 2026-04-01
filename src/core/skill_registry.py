"""Skill 使用统计管理 — 维护 skills/_registry.json。"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("lapwing.core.skill_registry")

_DAILY_STATS_MAX_DAYS = 90
_RECENT_MATCHES_MAX = 50

_DEFAULT_REGISTRY: dict[str, Any] = {
    "total_executions": 0,
    "total_with_skill": 0,
    "total_without_skill": 0,
    "skill_match_rate": 0.0,
    "match_level_distribution": {
        "index": 0,
        "semantic": 0,
        "none": 0,
    },
    "daily_stats": [],
    "recent_matches": [],
}


class SkillRegistryManager:
    """读写 skills/_registry.json，记录 Skill 使用统计。"""

    def __init__(self, registry_path: Path) -> None:
        self._path = registry_path
        self._data: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        """加载 registry 文件，不存在则初始化为默认值。"""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                self._loaded = True
                return
            except Exception as exc:
                logger.warning("读取 _registry.json 失败，重置: %s", exc)

        self._data = _deep_copy(_DEFAULT_REGISTRY)
        self._loaded = True
        self._save_atomic()

    def save(self, data: dict[str, Any] | None = None) -> None:
        """写入 registry 文件（线程安全，原子写入）。"""
        if data is not None:
            self._data = data
        self._save_atomic()

    def record_execution(
        self,
        skill_id: str | None,
        match_level: str | None,
        *,
        success: bool = True,
        request_summary: str = "",
    ) -> None:
        """记录一次执行，更新各项统计。"""
        if not self._loaded:
            self.load()

        today = date.today().isoformat()

        # 总计数
        self._data["total_executions"] = self._data.get("total_executions", 0) + 1
        if skill_id:
            self._data["total_with_skill"] = self._data.get("total_with_skill", 0) + 1
        else:
            self._data["total_without_skill"] = self._data.get("total_without_skill", 0) + 1

        # 匹配率
        total = self._data["total_executions"]
        self._data["skill_match_rate"] = round(
            self._data["total_with_skill"] / total, 2
        ) if total > 0 else 0.0

        # 匹配级别分布
        dist = self._data.setdefault("match_level_distribution", {
            "index": 0, "semantic": 0, "none": 0
        })
        # Map "quick" to "index" (quick_match no longer exists)
        if match_level == "quick":
            match_level = "index"
        level_key = match_level if match_level in ("index", "semantic") else "none"
        dist[level_key] = dist.get(level_key, 0) + 1

        # daily_stats
        daily = self._data.setdefault("daily_stats", [])
        today_entry = next((d for d in daily if d.get("date") == today), None)
        if today_entry is None:
            today_entry = {
                "date": today,
                "executions": 0,
                "with_skill": 0,
                "skills_created": 0,
                "skills_updated": 0,
            }
            daily.append(today_entry)
            # 保留最近 90 天
            if len(daily) > _DAILY_STATS_MAX_DAYS:
                daily[:] = daily[-_DAILY_STATS_MAX_DAYS:]

        today_entry["executions"] = today_entry.get("executions", 0) + 1
        if skill_id:
            today_entry["with_skill"] = today_entry.get("with_skill", 0) + 1

        # recent_matches
        if skill_id:
            recent = self._data.setdefault("recent_matches", [])
            recent.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_summary": request_summary[:100],
                "skill_id": skill_id,
                "match_level": match_level or "none",
                "success": success,
            })
            if len(recent) > _RECENT_MATCHES_MAX:
                recent[:] = recent[-_RECENT_MATCHES_MAX:]

        self._save_atomic()

    def get_stats(self) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        return dict(self._data)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _save_atomic(self) -> None:
        """原子写入：先写临时文件，再 rename。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._path)
        except Exception as exc:
            logger.warning("写入 _registry.json 失败: %s", exc)


def _deep_copy(d: dict) -> dict:
    return json.loads(json.dumps(d))
