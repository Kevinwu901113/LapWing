"""Lapwing 的记事本工具 — 在对话中主动记录重要信息。"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import KEVIN_NOTES_PATH, SELF_NOTES_PATH

logger = logging.getLogger("lapwing.tools.memory_note")

_VALID_TARGETS = {"kevin", "self"}
_TARGET_PATHS = {
    "kevin": KEVIN_NOTES_PATH,
    "self": SELF_NOTES_PATH,
}


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


async def write_note(target: str, content: str) -> dict:
    """将笔记追加到对应的记忆文件。"""
    target = target.strip().lower()
    if target not in _VALID_TARGETS:
        return {"success": False, "reason": f"无效的 target: {target}，可选: kevin, self"}

    content = content.strip()
    if not content:
        return {"success": False, "reason": "内容为空"}

    path = _TARGET_PATHS[target]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = f"\n\n> {date_str}\n\n{content}\n"

    def _append():
        _ensure_file(path)
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing + entry, encoding="utf-8")

    await asyncio.to_thread(_append)
    logger.info(f"[memory_note] 写入 {target}: {content[:60]}...")
    return {"success": True, "target": target, "path": str(path)}
