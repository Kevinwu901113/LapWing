"""IdentityFileManager — 身份类 Markdown 文件的快照、diff、回滚。

soul.md 专属的编辑冷却逻辑留在 SoulManager；
本类覆盖 voice.md / constitution.md 这类只能 Kevin 编辑、
但需要版本化（快照 + history + rollback）的场景。
"""

from __future__ import annotations

import difflib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("lapwing.core.identity_file_manager")


class IdentityFileManager:
    """管理一份身份类 Markdown 文件的版本化。

    snapshot 目录里的条目命名为 ``{kind}_{timestamp}.md`` /
    ``{kind}_{timestamp}.meta.json``，`snapshot_id` 即 stem（不含 ``.meta``）。
    """

    def __init__(
        self,
        file_path: Path,
        snapshot_dir: Path,
        *,
        kind: str,
        max_snapshots: int = 100,
        on_after_write: Callable[[], None] | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.kind = kind
        self.max_snapshots = max_snapshots
        self._on_after_write = on_after_write
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        try:
            return self.file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def edit(
        self,
        new_content: str,
        actor: str = "kevin",
        trigger: str = "",
    ) -> dict:
        """写入新内容：先 snapshot 当前版本，再 write，最后触发 hook。"""
        current = self.read()

        diff = list(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"{self.file_path.name} (before)",
                tofile=f"{self.file_path.name} (after)",
                lineterm="",
            )
        )
        added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        diff_summary = f"+{added} 行, -{removed} 行"

        if not diff:
            return {"success": True, "reason": "内容没有变化", "diff_summary": "无修改"}

        self._save_snapshot(current, actor, trigger, diff_summary)

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(new_content, encoding="utf-8")

        if self._on_after_write is not None:
            try:
                self._on_after_write()
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_after_write 回调失败 (%s): %s", self.kind, exc)

        logger.info(
            "%s 已更新 (actor=%s, trigger=%s, %s)",
            self.file_path.name,
            actor,
            trigger,
            diff_summary,
        )
        return {"success": True, "reason": "已更新", "diff_summary": diff_summary}

    def list_snapshots(self, limit: int = 20) -> list[dict]:
        meta_files = sorted(
            self.snapshot_dir.glob(f"{self.kind}_*.meta.json"), reverse=True
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
        snapshot_path = self.snapshot_dir / f"{snapshot_id}.md"
        if not snapshot_path.exists():
            return {"success": False, "reason": f"快照 {snapshot_id} 不存在"}

        current = self.read()
        self._save_snapshot(
            current,
            actor="kevin",
            trigger=f"rollback to {snapshot_id}",
            diff_summary="rollback",
        )

        old_content = snapshot_path.read_text(encoding="utf-8")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(old_content, encoding="utf-8")

        if self._on_after_write is not None:
            try:
                self._on_after_write()
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_after_write 回调失败 (%s): %s", self.kind, exc)

        logger.info("%s 已回滚到 %s", self.file_path.name, snapshot_id)
        return {"success": True, "reason": f"已回滚到 {snapshot_id}"}

    def get_diff(self, snapshot_id: str) -> str:
        snapshot_path = self.snapshot_dir / f"{snapshot_id}.md"
        if not snapshot_path.exists():
            return f"快照 {snapshot_id} 不存在"

        old = snapshot_path.read_text(encoding="utf-8").splitlines(keepends=True)
        current = self.read().splitlines(keepends=True)
        diff = difflib.unified_diff(
            old, current, fromfile=snapshot_id, tofile="current", lineterm=""
        )
        return "\n".join(diff) or "(无差异)"

    def _save_snapshot(
        self, content: str, actor: str, trigger: str, diff_summary: str
    ) -> None:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")

        snapshot_path = self.snapshot_dir / f"{self.kind}_{timestamp}.md"
        snapshot_path.write_text(content, encoding="utf-8")

        meta_path = self.snapshot_dir / f"{self.kind}_{timestamp}.meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "timestamp": now.isoformat(),
                    "actor": actor,
                    "trigger": trigger,
                    "diff_summary": diff_summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self._cleanup_old_snapshots()

    def _cleanup_old_snapshots(self) -> None:
        snapshots = sorted(self.snapshot_dir.glob(f"{self.kind}_*.md"))
        if len(snapshots) > self.max_snapshots:
            for old in snapshots[: -self.max_snapshots]:
                old.unlink(missing_ok=True)
                meta = old.with_name(old.stem + ".meta.json")
                meta.unlink(missing_ok=True)
