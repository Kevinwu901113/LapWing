"""SoulManager — soul.md 版本化、编辑冷却和快照管理。"""

from __future__ import annotations

import difflib
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("lapwing.core.soul_manager")


class SoulManager:
    """管理 soul.md 的版本化、编辑冷却和快照。"""

    SOUL_PATH = Path("data/identity/soul.md")
    SNAPSHOT_DIR = Path("data/identity/soul_snapshots")
    MAX_SNAPSHOTS = 100
    COOLDOWN_HOURS = 24

    def __init__(
        self,
        soul_path: Path | None = None,
        snapshot_dir: Path | None = None,
    ):
        if soul_path is not None:
            self.SOUL_PATH = soul_path
        if snapshot_dir is not None:
            self.SNAPSHOT_DIR = snapshot_dir
        self.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        """读取当前 soul.md 全文。"""
        try:
            return self.SOUL_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def edit(
        self,
        new_content: str,
        actor: str = "lapwing",
        trigger: str = "",
    ) -> dict:
        """编辑 soul.md。

        1. 检查冷却时间（Kevin 豁免）
        2. 保存快照
        3. 计算 diff
        4. 写入新内容

        返回: {"success": bool, "reason": str, "diff_summary": str}
        """
        # 检查冷却（Kevin 不受限制）
        if actor != "kevin":
            last_edit = self._get_last_edit_time()
            if last_edit:
                now = datetime.now(ZoneInfo("Asia/Taipei"))
                elapsed = (now - last_edit).total_seconds()
                if elapsed < self.COOLDOWN_HOURS * 3600:
                    remaining = self.COOLDOWN_HOURS - (elapsed / 3600)
                    return {
                        "success": False,
                        "reason": (
                            f"距离上次修改不足 {self.COOLDOWN_HOURS} 小时，"
                            f"还需等待 {remaining:.1f} 小时"
                        ),
                        "diff_summary": "",
                    }

        # 读取当前内容
        current = self.read()

        # 计算 diff
        diff = list(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile="soul.md (before)",
                tofile="soul.md (after)",
                lineterm="",
            )
        )
        diff_text = "\n".join(diff) if diff else "(no changes)"

        # 生成 diff 摘要
        added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(
            1 for l in diff if l.startswith("-") and not l.startswith("---")
        )
        diff_summary = f"+{added} 行, -{removed} 行"

        # 如果没有变化
        if not diff or diff_text == "(no changes)":
            return {
                "success": True,
                "reason": "内容没有变化",
                "diff_summary": "无修改",
            }

        # 保存快照
        self._save_snapshot(current, actor, trigger, diff_summary)

        # 写入新内容
        self.SOUL_PATH.write_text(new_content, encoding="utf-8")

        logger.info(
            "soul.md 已更新 (actor=%s, trigger=%s, %s)", actor, trigger, diff_summary
        )

        return {
            "success": True,
            "reason": "已更新",
            "diff_summary": diff_summary,
        }

    def list_snapshots(self, limit: int = 20) -> list[dict]:
        """列出快照历史。"""
        meta_files = sorted(
            self.SNAPSHOT_DIR.glob("soul_*.meta.json"), reverse=True
        )[:limit]

        result = []
        for meta_path in meta_files:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["snapshot_id"] = meta_path.stem.replace(".meta", "")
                result.append(meta)
            except Exception:
                continue
        return result

    def rollback(self, snapshot_id: str) -> dict:
        """回滚到指定快照。"""
        snapshot_path = self.SNAPSHOT_DIR / f"{snapshot_id}.md"
        if not snapshot_path.exists():
            return {"success": False, "reason": f"快照 {snapshot_id} 不存在"}

        # 先保存当前版本为快照
        current = self.read()
        self._save_snapshot(
            current,
            actor="kevin",
            trigger=f"rollback to {snapshot_id}",
            diff_summary="rollback",
        )

        # 恢复
        old_content = snapshot_path.read_text(encoding="utf-8")
        self.SOUL_PATH.write_text(old_content, encoding="utf-8")

        logger.info("soul.md 已回滚到 %s", snapshot_id)
        return {"success": True, "reason": f"已回滚到 {snapshot_id}"}

    def get_diff(self, snapshot_id: str) -> str:
        """获取某个快照与当前版本的 diff。"""
        snapshot_path = self.SNAPSHOT_DIR / f"{snapshot_id}.md"
        if not snapshot_path.exists():
            return f"快照 {snapshot_id} 不存在"

        old = snapshot_path.read_text(encoding="utf-8").splitlines(keepends=True)
        current = self.read().splitlines(keepends=True)
        diff = difflib.unified_diff(
            old,
            current,
            fromfile=snapshot_id,
            tofile="current",
            lineterm="",
        )
        return "\n".join(diff) or "(无差异)"

    def _save_snapshot(
        self, content: str, actor: str, trigger: str, diff_summary: str
    ) -> None:
        """保存快照 + 元数据。"""
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")

        # 写快照文件
        snapshot_path = self.SNAPSHOT_DIR / f"soul_{timestamp}.md"
        snapshot_path.write_text(content, encoding="utf-8")

        # 写元数据
        meta_path = self.SNAPSHOT_DIR / f"soul_{timestamp}.meta.json"
        meta = {
            "timestamp": now.isoformat(),
            "actor": actor,
            "trigger": trigger,
            "diff_summary": diff_summary,
        }
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 清理旧快照
        self._cleanup_old_snapshots()

    def _cleanup_old_snapshots(self) -> None:
        """保留最近 MAX_SNAPSHOTS 个快照。"""
        snapshots = sorted(self.SNAPSHOT_DIR.glob("soul_*.md"))
        if len(snapshots) > self.MAX_SNAPSHOTS:
            for old in snapshots[: -self.MAX_SNAPSHOTS]:
                old.unlink(missing_ok=True)
                meta = old.with_name(old.stem + ".meta.json")
                meta.unlink(missing_ok=True)

    def _get_last_edit_time(self) -> datetime | None:
        """获取最近一次非 kevin 编辑时间。"""
        meta_files = sorted(
            self.SNAPSHOT_DIR.glob("soul_*.meta.json"), reverse=True
        )
        for f in meta_files:
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
                if meta.get("actor") != "kevin":
                    return datetime.fromisoformat(meta["timestamp"])
            except Exception:
                continue
        return None
