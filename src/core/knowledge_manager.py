"""知识笔记管理器 — 在 data/knowledge/ 下按主题存储和检索知识笔记。"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR

logger = logging.getLogger("lapwing.core.knowledge_manager")

_KNOWLEDGE_DIR = DATA_DIR / "knowledge"
_MAX_NOTE_SIZE = 3000    # 单条笔记最大字符数（超出时截断）
_INJECT_TOTAL_LIMIT = 2000  # 注入 system prompt 的总字符上限


class KnowledgeManager:
    """在文件系统上管理 Lapwing 的知识笔记。

    每条笔记以主题名命名存放在 data/knowledge/{topic}.md。
    检索使用简单的关键词匹配，不依赖向量数据库。
    """

    def __init__(self) -> None:
        _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    def save_note(self, topic: str, source_url: str, content: str) -> Path:
        """保存知识笔记到 data/knowledge/{topic}.md。

        如果同主题笔记已存在，追加新内容而不覆盖。

        Args:
            topic: 笔记主题（用作文件名）
            source_url: 来源 URL
            content: 笔记正文（摘要或要点）

        Returns:
            笔记文件路径
        """
        safe_topic = _sanitize_filename(topic)
        if not safe_topic:
            safe_topic = "未命名"
        path = _KNOWLEDGE_DIR / f"{safe_topic}.md"
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        entry = (
            f"\n\n---\n\n"
            f"来源: {source_url}\n"
            f"日期: {date_str}\n\n"
            f"{content[:_MAX_NOTE_SIZE]}"
        )

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + entry, encoding="utf-8")
        else:
            header = f"# {topic}\n"
            path.write_text(header + entry, encoding="utf-8")

        logger.info(f"[knowledge] 保存笔记: {safe_topic}.md ({len(content)} 字符)")
        return path

    def get_relevant_notes(self, query: str = "", max_chars: int = 2000) -> list[dict]:
        """Load all knowledge notes up to a character budget.

        No matching — the LLM decides what's relevant from context.
        Notes are returned newest-first (by file modification time).
        """
        all_files = list(_KNOWLEDGE_DIR.glob("*.md"))
        if not all_files:
            return []

        # Sort by modification time, newest first
        all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        results = []
        total_chars = 0
        for f in all_files:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("[knowledge] read failed: %s — %s", f.name, exc)
                continue

            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            excerpt = text[:remaining]
            results.append({"topic": f.stem, "content": excerpt})
            total_chars += len(excerpt)

        return results

    def list_topics(self) -> list[str]:
        """列出所有已有的知识主题。"""
        return [f.stem for f in sorted(_KNOWLEDGE_DIR.glob("*.md"))]


def _sanitize_filename(name: str) -> str:
    """将主题名转换为安全的文件名（去除路径分隔符和特殊字符）。"""
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.replace(" ", "_")
    return name[:64]  # 限制文件名长度


