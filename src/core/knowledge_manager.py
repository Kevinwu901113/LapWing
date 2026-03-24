"""知识笔记管理器 — 在 data/knowledge/ 下按主题存储和检索知识笔记。"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DATA_DIR

logger = logging.getLogger("lapwing.knowledge")

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

    def get_relevant_notes(self, query: str, max_notes: int = 3) -> list[dict]:
        """根据查询文本找到相关知识笔记。

        使用文件名（主题）与查询文本的关键词重叠度评分。

        Args:
            query: 用户消息或当前话题文本
            max_notes: 最多返回几条笔记

        Returns:
            [{"topic": str, "content": str}, ...]，按相关度排序
        """
        all_files = list(_KNOWLEDGE_DIR.glob("*.md"))
        if not all_files:
            return []

        scored: list[tuple[float, Path, str]] = []
        query_lower = query.lower()

        for f in all_files:
            topic = f.stem  # 文件名不含扩展名
            score = _relevance_score(topic, query_lower)
            if score > 0:
                scored.append((score, f, topic))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        total_chars = 0
        for _, f, topic in scored[:max_notes]:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning(f"[knowledge] 读取笔记失败: {f.name} — {exc}")
                continue
            # 控制注入总量
            remaining = _INJECT_TOTAL_LIMIT - total_chars
            if remaining <= 0:
                break
            excerpt = text[:remaining]
            results.append({"topic": topic, "content": excerpt})
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


def _relevance_score(topic: str, query_lower: str) -> float:
    """计算主题与查询的相关度分数（简单字符匹配）。"""
    topic_lower = topic.lower().replace("_", " ")
    score = 0.0

    # 完整主题出现在查询中
    if topic_lower in query_lower:
        score += 3.0
    # 查询中的片段出现在主题中
    elif topic_lower and any(
        part in query_lower for part in topic_lower.split()
        if len(part) >= 2
    ):
        score += 1.5
    # 主题片段在查询中有部分匹配
    elif any(char in query_lower for char in topic_lower if '\u4e00' <= char <= '\u9fff'):
        # 中文字符逐字匹配
        overlap = sum(1 for c in topic_lower if c in query_lower and '\u4e00' <= c <= '\u9fff')
        if overlap >= 2:
            score += overlap * 0.5

    return score
