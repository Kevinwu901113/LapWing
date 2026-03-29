"""文件化记忆读写工具。"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("lapwing.memory.file_memory")

_CHAR_LIMIT = 2000  # 单个文件注入 system prompt 的字符上限


async def read_memory_file(path: Path, max_chars: int = _CHAR_LIMIT) -> str:
    """异步读取记忆文件，截断到 max_chars。"""
    if not path.exists():
        return ""
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        text = text.strip()
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n\n（...内容过长已截断）"
        return text
    except Exception as exc:
        logger.warning(f"读取记忆文件失败 {path}: {exc}")
        return ""


async def read_recent_summaries(
    summaries_dir: Path,
    max_files: int = 5,
    max_chars: int = 1500,
) -> str:
    """读取最近的对话摘要文件。"""
    if not summaries_dir.exists():
        return ""
    files = sorted(summaries_dir.glob("*.md"), reverse=True)[:max_files]
    if not files:
        return ""

    parts = []
    total = 0
    for f in files:
        try:
            text = await asyncio.to_thread(f.read_text, encoding="utf-8")
            text = text.strip()
            if total + len(text) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(text[:remaining].rstrip() + "...")
                break
            parts.append(text)
            total += len(text)
        except Exception:
            continue

    return "\n\n---\n\n".join(parts)
