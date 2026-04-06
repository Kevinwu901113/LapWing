"""记忆索引管理 — data/memory/_index.json 的读写与查询。

索引存储所有记忆条目的元数据，记忆内容仍存 markdown（保持人类可读 + Git 友好）。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from threading import Lock

from config.settings import MEMORY_DIR

logger = logging.getLogger("lapwing.memory.memory_index")

INDEX_PATH = MEMORY_DIR / "_index.json"

# category → 衰减窗口（天）。决定/纠正类衰减更慢，对人格发展更重要。
_DECAY_WINDOWS: dict[str, int] = {
    "kevin_fact": 180,
    "knowledge": 180,
    "decision": 360,
    "interest": 240,
    "correction": 360,
    "procedural": 360,
}
_DEFAULT_DECAY_WINDOW = 180
_ALL_CATEGORIES = set(_DECAY_WINDOWS.keys())


class MemoryIndex:
    """data/memory/_index.json 的线程安全读写接口。"""

    def __init__(self, path: Path = INDEX_PATH) -> None:
        self._path = path
        self._lock = Lock()
        self._data: dict = {"version": 1, "entries": {}, "next_id": 1}
        self._load()

    # ── 内部 I/O ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("索引文件损坏，使用空索引: %s", e)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_entry(
        self,
        *,
        category: str,
        source_file: str,
        content_preview: str,
        importance: int = 3,
        tags: list[str] | None = None,
    ) -> str:
        """添加一条记忆条目，返回 mem_id。"""
        with self._lock:
            mem_id = f"mem_{self._data['next_id']:04d}"
            self._data["next_id"] += 1
            now = datetime.now().isoformat()
            self._data["entries"][mem_id] = {
                "id": mem_id,
                "category": category,
                "source_file": source_file,
                "content_preview": content_preview[:200],
                "importance": max(1, min(5, importance)),
                "created_at": now,
                "last_referenced": now,
                "reference_count": 0,
                "tags": tags or [],
            }
            self._save()
        return mem_id

    def remove_entry(self, mem_id: str) -> bool:
        """删除条目。返回 True 表示找到并删除。"""
        with self._lock:
            removed = self._data["entries"].pop(mem_id, None)
            if removed:
                self._save()
            return removed is not None

    def get_entry(self, mem_id: str) -> dict | None:
        return self._data["entries"].get(mem_id)

    def update_referenced(self, mem_id: str) -> None:
        """标记一条记忆被引用（更新时间和计数）。"""
        with self._lock:
            entry = self._data["entries"].get(mem_id)
            if entry:
                entry["last_referenced"] = datetime.now().isoformat()
                entry["reference_count"] = entry.get("reference_count", 0) + 1
                self._save()

    def all_entries(self) -> list[dict]:
        return list(self._data["entries"].values())

    def find_by_source_file(self, source_file: str) -> list[dict]:
        return [e for e in self._data["entries"].values() if e["source_file"] == source_file]

    def find_by_content(self, content_preview: str) -> dict | None:
        """按 content_preview 去重查找。"""
        needle = content_preview[:200]
        for entry in self._data["entries"].values():
            if entry["content_preview"] == needle:
                return entry
        return None

    def remove_by_source_file(self, source_file: str) -> list[str]:
        """删除指定源文件的所有条目。返回被删除的 mem_id 列表。"""
        to_remove = [e["id"] for e in self.find_by_source_file(source_file)]
        with self._lock:
            for mem_id in to_remove:
                self._data["entries"].pop(mem_id, None)
            if to_remove:
                self._save()
        return to_remove

    # ── 重要性计算 ─────────────────────────────────────────────────────────────

    def compute_importance(self, entry: dict) -> float:
        """计算当前动态重要性分数（0~max）。

        公式：base × recency × max(1, log2(ref_count+1))
        改编自 Auto-Dream，保护决定/纠正类慢衰减。
        """
        now = datetime.now()
        try:
            created = datetime.fromisoformat(entry["created_at"])
        except (ValueError, KeyError):
            created = now
        days_old = (now - created).days

        decay_window = _DECAY_WINDOWS.get(entry.get("category", ""), _DEFAULT_DECAY_WINDOW)
        recency = max(0.1, 1.0 - days_old / decay_window)
        ref_boost = math.log2(entry.get("reference_count", 0) + 1)
        base = entry.get("importance", 3)

        return base * recency * max(1.0, ref_boost)

    def ranked_entries(self, limit: int = 30, exclude_archived: bool = True) -> list[dict]:
        """按动态重要性排序，返回 top N 条目。"""
        entries = self.all_entries()
        if exclude_archived:
            entries = [e for e in entries if not e.get("archived")]
        scored = [(self.compute_importance(e), e) for e in entries]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:limit]]

    # ── 健康评分 ───────────────────────────────────────────────────────────────

    def health_score(self) -> dict:
        """计算记忆健康评分（四维度，总分 0-100）。"""
        entries = self.all_entries()
        total = len(entries)
        if total == 0:
            return {"score": 0, "total": 0, "dimensions": {}}

        now = datetime.now()

        # 新鲜度：30 天内被引用的占比
        recent_refs = 0
        for e in entries:
            try:
                lr = datetime.fromisoformat(e.get("last_referenced", ""))
                if (now - lr).days <= 30:
                    recent_refs += 1
            except (ValueError, TypeError):
                pass
        freshness = recent_refs / total

        # 覆盖率：14 天内有新条目的 category 占比
        active_cats: set[str] = set()
        for e in entries:
            try:
                ca = datetime.fromisoformat(e.get("created_at", ""))
                if (now - ca).days <= 14:
                    active_cats.add(e.get("category", ""))
            except (ValueError, TypeError):
                pass
        coverage = len(active_cats & _ALL_CATEGORIES) / len(_ALL_CATEGORIES)

        # 连贯性：有 tags 的条目占比
        tagged = sum(1 for e in entries if e.get("tags"))
        coherence = tagged / total

        # 效率：非归档条目占比
        active = sum(1 for e in entries if not e.get("archived"))
        efficiency = active / total

        score = (freshness * 0.25 + coverage * 0.25 + coherence * 0.20 + efficiency * 0.30) * 100

        return {
            "score": round(score, 1),
            "total": total,
            "dimensions": {
                "freshness": round(freshness, 2),
                "coverage": round(coverage, 2),
                "coherence": round(coherence, 2),
                "efficiency": round(efficiency, 2),
            },
        }

    # ── 维护 ───────────────────────────────────────────────────────────────────

    def archive_stale(self, max_age_days: int = 90, min_importance: float = 0.2) -> list[str]:
        """归档过期且低重要性的条目，返回归档的 mem_id 列表。

        不删除，只标记 archived=True。
        豁免：correction 和 decision 类别永不归档。
        """
        archived_ids: list[str] = []
        now = datetime.now()
        exempt = {"correction", "decision"}

        with self._lock:
            for mem_id, entry in self._data["entries"].items():
                if entry.get("archived"):
                    continue
                if entry.get("category", "") in exempt:
                    continue
                try:
                    lr = datetime.fromisoformat(entry.get("last_referenced", ""))
                    age = (now - lr).days
                except (ValueError, TypeError):
                    age = 999
                if age >= max_age_days and self.compute_importance(entry) < min_importance:
                    entry["archived"] = True
                    archived_ids.append(mem_id)

            if archived_ids:
                self._save()

        return archived_ids
