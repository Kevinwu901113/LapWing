"""Incident 管理器。

职责：
- 创建、查询、更新 incident
- 去重（同一工具 + 同一错误类型在 1 小时内合并）
- 日限额（每天最多 30 个新 incident，超过后跳过）
- 状态流转（open → investigating → resolved / wont_fix）
- 自动降级（attempts >= 3 降 severity，>= 5 自动 wont_fix）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import threading

logger = logging.getLogger("lapwing.core.incident_manager")

INCIDENTS_DIR = Path("data/memory/incidents")
DAILY_LIMIT = 30
DEDUP_WINDOW_HOURS = 1
AUTO_DOWNGRADE_THRESHOLD = 3
AUTO_WONTFIX_THRESHOLD = 5


class IncidentManager:
    """管理 incident 的完整生命周期。"""

    def __init__(
        self,
        send_notification_fn: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        send_notification_fn: 用于通知 Kevin 的异步回调。
            由 container.py 注入。
        """
        INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._send_notification = send_notification_fn
        self._daily_count: dict[str, int] = {}

    # ── 创建 ──

    async def create(
        self,
        source: str,
        description: str,
        context: dict[str, Any],
        severity: str = "medium",
        related_tool: str | None = None,
    ) -> str | None:
        """创建一个 incident。返回 ID，或 None（被去重/限额拦截）。"""
        # 去重检查
        if source == "tool_failure" and related_tool:
            existing = self._find_duplicate(
                related_tool, context.get("error_type", "")
            )
            if existing:
                existing["occurrence_count"] = existing.get("occurrence_count", 1) + 1
                existing["last_occurrence"] = datetime.now().isoformat()
                self._save_incident(existing)
                logger.info(
                    "[incident] 去重合并到 %s (count=%d)",
                    existing["id"], existing["occurrence_count"],
                )
                return None

        # 日限额检查
        today = datetime.now().strftime("%Y-%m-%d")
        count = self._daily_count.get(today, 0)
        if count >= DAILY_LIMIT:
            logger.warning("[incident] 日限额已达 %d，跳过创建", DAILY_LIMIT)
            return None

        inc_id = self._generate_id()

        incident: dict[str, Any] = {
            "id": inc_id,
            "created_at": datetime.now().isoformat(),
            "source": source,
            "severity": severity,
            "description": description,
            "context": context,
            "related_tool": related_tool,
            "status": "open",
            "attempts": 0,
            "resolution": None,
            "resolved_at": None,
            "linked_rule": None,
            "occurrence_count": 1,
            "last_occurrence": datetime.now().isoformat(),
        }

        self._save_incident(incident)
        self._daily_count[today] = count + 1
        logger.info("[incident] 创建 %s [%s] %s", inc_id, severity, description[:80])
        return inc_id

    # ── 查询 ──

    def get_open_incidents(self, limit: int = 10) -> list[dict[str, Any]]:
        """返回所有 open/investigating 状态的 incident，按 severity 和时间排序。"""
        severity_order = {"high": 0, "medium": 1, "low": 2}
        incidents: list[dict[str, Any]] = []
        for f in INCIDENTS_DIR.glob("INC-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") in ("open", "investigating"):
                    incidents.append(data)
            except Exception:
                continue
        incidents.sort(
            key=lambda x: (
                severity_order.get(x.get("severity", "low"), 9),
                x.get("created_at", ""),
            )
        )
        return incidents[:limit]

    def get_incident(self, inc_id: str) -> dict[str, Any] | None:
        """按 ID 获取单个 incident。"""
        path = INCIDENTS_DIR / f"{inc_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def get_stats(self) -> dict[str, int]:
        """返回 incident 统计。"""
        stats: dict[str, int] = {
            "open": 0, "investigating": 0, "resolved": 0, "wont_fix": 0, "total": 0,
        }
        for f in INCIDENTS_DIR.glob("INC-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                status = data.get("status", "open")
                stats[status] = stats.get(status, 0) + 1
                stats["total"] += 1
            except Exception:
                continue
        return stats

    # ── 状态更新 ──

    async def start_investigating(self, inc_id: str) -> bool:
        """标记为 investigating。"""
        inc = self.get_incident(inc_id)
        if not inc or inc["status"] != "open":
            return False
        inc["status"] = "investigating"
        self._save_incident(inc)
        return True

    async def resolve(self, inc_id: str, resolution: str) -> bool:
        """标记为 resolved。

        调用方负责后续处理：
          1. 通知 Kevin（如果涉及代码修改）
          2. 生成 ExperienceSkill
          3. 清理关联的 TacticalRule
        """
        inc = self.get_incident(inc_id)
        if not inc:
            return False
        inc["status"] = "resolved"
        inc["resolution"] = resolution
        inc["resolved_at"] = datetime.now().isoformat()
        self._save_incident(inc)
        logger.info("[incident] resolved %s: %s", inc_id, resolution[:80])
        return True

    async def record_attempt(self, inc_id: str) -> str | None:
        """记录一次修复尝试。

        返回:
          None — 正常
          "downgraded" — attempts >= 3，已降级
          "wont_fix" — attempts >= 5，自动放弃
        """
        inc = self.get_incident(inc_id)
        if not inc:
            return None
        inc["attempts"] = inc.get("attempts", 0) + 1
        result = None

        if inc["attempts"] >= AUTO_WONTFIX_THRESHOLD:
            inc["status"] = "wont_fix"
            inc["resolution"] = f"自动放弃：尝试 {inc['attempts']} 次未能修复"
            inc["resolved_at"] = datetime.now().isoformat()
            result = "wont_fix"
            logger.info("[incident] wont_fix %s (attempts=%d)", inc_id, inc["attempts"])
            if self._send_notification:
                try:
                    await self._send_notification(
                        f"我试了 {inc['attempts']} 次没修好这个问题，暂时放弃了，可能需要你看一下：\n"
                        f"{inc['description']}"
                    )
                except Exception:
                    logger.warning("[incident] 通知 Kevin 失败")
        elif inc["attempts"] >= AUTO_DOWNGRADE_THRESHOLD:
            inc["severity"] = "low"
            result = "downgraded"
            logger.info(
                "[incident] downgraded %s to low (attempts=%d)", inc_id, inc["attempts"],
            )

        self._save_incident(inc)
        return result

    async def mark_wont_fix(self, inc_id: str, reason: str) -> bool:
        """手动标记为 wont_fix。"""
        inc = self.get_incident(inc_id)
        if not inc:
            return False
        inc["status"] = "wont_fix"
        inc["resolution"] = reason
        inc["resolved_at"] = datetime.now().isoformat()
        self._save_incident(inc)
        logger.info("[incident] wont_fix (manual) %s: %s", inc_id, reason[:80])
        return True

    def link_rule(self, inc_id: str, rule_text: str) -> None:
        """关联一条 TacticalRule 到 incident。"""
        inc = self.get_incident(inc_id)
        if inc:
            inc["linked_rule"] = rule_text
            self._save_incident(inc)

    # ── 归档 ──

    def archive_resolved(self, max_age_days: int = 30) -> int:
        """归档已 resolved/wont_fix 且超过 max_age_days 的 incident。

        移动到 archive/ 子目录，返回归档数量。
        由 memory_maintenance heartbeat action 调用。
        """
        archive_dir = INCIDENTS_DIR / "archive"
        archive_dir.mkdir(exist_ok=True)
        now = datetime.now()
        count = 0
        for f in INCIDENTS_DIR.glob("INC-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") not in ("resolved", "wont_fix"):
                    continue
                resolved_at = data.get("resolved_at")
                if not resolved_at:
                    continue
                age = (now - datetime.fromisoformat(resolved_at)).days
                if age >= max_age_days:
                    f.rename(archive_dir / f.name)
                    count += 1
            except Exception:
                continue
        if count:
            logger.info("[incident] 归档了 %d 个 incident", count)
        return count

    # ── 为意识循环 prompt 生成摘要 ──

    def format_for_consciousness(self, limit: int = 5) -> str | None:
        """生成供意识循环 prompt 注入的 incident 摘要。返回 None 表示无未解决 incident。"""
        incidents = self.get_open_incidents(limit=limit)
        if not incidents:
            return None

        lines = [f"你有 {len(incidents)} 个未解决的问题："]
        for i, inc in enumerate(incidents, 1):
            sev = inc.get("severity", "?")
            inc_id = inc["id"]
            desc = inc["description"][:60]
            extras: list[str] = []
            occ = inc.get("occurrence_count", 1)
            if occ > 1:
                extras.append(f"已发生 {occ} 次")
            att = inc.get("attempts", 0)
            if att > 0:
                extras.append(f"尝试修复 {att} 次")
            if inc.get("severity") == "low" and att >= AUTO_DOWNGRADE_THRESHOLD:
                extras.append("已降级")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"{i}. [{sev}] {inc_id}: {desc}{extra_str}")

        lines.append("")
        lines.append(
            "你可以选择排查其中一个，或者什么都不做。如果要排查，先读取 incident 文件了解详情。"
        )
        return "\n".join(lines)

    # ── 内部 ──

    def _find_duplicate(self, tool_name: str, error_type: str) -> dict[str, Any] | None:
        """在去重窗口内查找同一工具+同一错误类型的 open incident。"""
        cutoff = datetime.now() - timedelta(hours=DEDUP_WINDOW_HOURS)
        for f in INCIDENTS_DIR.glob("INC-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") not in ("open", "investigating"):
                    continue
                if data.get("related_tool") != tool_name:
                    continue
                ctx = data.get("context", {})
                if ctx.get("error_type") != error_type:
                    continue
                last = datetime.fromisoformat(
                    data.get("last_occurrence", data["created_at"])
                )
                if last >= cutoff:
                    return data
            except Exception:
                continue
        return None

    def _generate_id(self) -> str:
        """生成 INC-YYYYMMDD-NNNN 格式的 ID。"""
        today = datetime.now().strftime("%Y%m%d")
        existing = list(INCIDENTS_DIR.glob(f"INC-{today}-*.json"))
        seq = len(existing) + 1
        return f"INC-{today}-{seq:04d}"

    def _save_incident(self, incident: dict[str, Any]) -> None:
        """保存 incident 到文件。"""
        path = INCIDENTS_DIR / f"{incident['id']}.json"
        with self._lock:
            path.write_text(
                json.dumps(incident, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
