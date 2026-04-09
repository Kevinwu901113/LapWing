"""自动记忆提取器 — 从对话中自动提取值得长期记忆的信息。

触发时机（由 heartbeat AutoMemoryAction 决定）：
- 快心跳时检测到 15 分钟无活动
- 距上次提取至少 30 分钟
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from config.settings import MEMORY_DIR

logger = logging.getLogger("lapwing.memory.auto_extractor")

_EXTRACTION_PROMPT = """\
你是 Lapwing 的记忆管理模块。回顾以下对话，提取值得长期记忆的信息。

## 提取规则

只提取重要度 >= 3 的信息（1-5 分）。不要提取：
- 临时性的（"今天下午三点开会" — 过了就没用了）
- 已经记过的信息
- 太笼统的（"Kevin 今天比较忙" — 没有具体信息）

## 分类

- kevin_fact: 关于 Kevin 的个人信息（偏好、习惯、背景、人际关系）
- decision: 对话中做出的决定（技术方案选择、计划变更）
- knowledge: Lapwing 学到的知识（技术概念、世界事实）
- interest: Kevin 或 Lapwing 表现出兴趣的话题
- correction: Kevin 纠正 Lapwing 的地方（说话方式、事实错误）
- procedural: Kevin 的工作习惯、偏好流程（例如"Kevin 喜欢蓝图式文档"）

## 输出格式

只返回 JSON 数组，不要返回其他内容。如果没有值得提取的信息，返回空数组 []。

```json
[
    {
        "category": "kevin_fact",
        "content": "Kevin 更喜欢用中文讨论技术问题",
        "importance": 4
    }
]
```

## 对话内容

{conversation}
"""

_VALID_CATEGORIES = {"kevin_fact", "decision", "knowledge", "interest", "correction", "procedural"}


class AutoMemoryExtractor:
    """从对话消息列表中自动提取并存储记忆。"""

    def __init__(self, router, memory_index=None) -> None:
        """
        Args:
            router: LLMRouter 实例，用于调用 query_lightweight。
            memory_index: 可选的 MemoryIndex 实例，用于同步索引。
        """
        self._router = router
        self._memory_index = memory_index

    async def extract_from_messages(self, messages: list[dict]) -> list[dict]:
        """从消息列表中提取记忆，写入文件，返回成功存储的条目。

        Args:
            messages: 对话消息列表 [{"role": ..., "content": ...}, ...]

        Returns:
            成功提取并存储的记忆列表。
        """
        if len(messages) < 4:
            logger.debug("对话太短（%d 条），跳过自动提取", len(messages))
            return []

        formatted = self._format_conversation(messages)
        prompt = _EXTRACTION_PROMPT.replace("{conversation}", formatted)

        try:
            raw = await self._router.query_lightweight(
                system="你是一个记忆提取模块。严格按照要求输出 JSON。",
                user=prompt,
                slot="memory_processing",
            )
            items = self._parse_response(raw)
        except Exception as e:
            logger.error("记忆提取 LLM 调用失败: %s", e)
            return []

        stored = []
        for item in items:
            try:
                if await self._store(item):
                    stored.append(item)
            except Exception as e:
                logger.warning("记忆存储失败: %s — %s", item.get("content", "")[:50], e)

        if stored:
            categories = {r["category"] for r in stored}
            logger.info("自动提取了 %d 条记忆 (%s) from %d 条消息",
                        len(stored), ", ".join(sorted(categories)), len(messages))

        return stored

    def _format_conversation(self, messages: list[dict]) -> str:
        """把消息列表格式化为可读文本。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if not content or role == "system":
                continue
            speaker = "Kevin" if role == "user" else "Lapwing"
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{speaker}: {content}")
        return "\n".join(lines)

    def _parse_response(self, raw: str) -> list[dict]:
        """解析 LLM 返回的 JSON。容错处理各种格式问题。"""
        text = raw.strip()
        # 去掉 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n", 1)
            text = lines[1] if len(lines) > 1 else ""
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("无法解析提取响应: %s", text[:200])
            return []

        if not isinstance(items, list):
            return []

        valid = []
        for item in items:
            if (
                isinstance(item, dict)
                and "category" in item
                and "content" in item
                and item["category"] in _VALID_CATEGORIES
                and str(item["content"]).strip()
            ):
                valid.append(item)
        return valid

    async def _store(self, item: dict) -> bool:
        """把一条提取的记忆写入文件（按月组织，去重）。

        Returns:
            True 表示成功写入，False 表示跳过（重复或安全拦截）。
        """
        import asyncio

        from config.settings import MEMORY_GUARD_ENABLED

        category = item["category"]
        content = str(item["content"]).strip()

        # MemoryGuard 安全扫描
        if MEMORY_GUARD_ENABLED and content:
            from src.guards.memory_guard import MemoryGuard
            scan = MemoryGuard().scan(content)
            if not scan.passed:
                logger.warning("自动提取记忆被安全拦截: %s — %s", content[:50], scan.threats)
                return False

        cat_dir = MEMORY_DIR / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        month_key = datetime.now().strftime("%Y-%m")
        file_path = cat_dir / f"{month_key}.md"

        def _write() -> bool:
            if file_path.exists():
                existing = file_path.read_text(encoding="utf-8")
                if content in existing:
                    logger.debug("重复记忆跳过: %s", content[:50])
                    return False
            with open(file_path, "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%m-%d %H:%M")
                f.write(f"- [{timestamp}] {content}\n")
            return True

        wrote = await asyncio.to_thread(_write)
        if wrote and self._memory_index is not None:
            existing = self._memory_index.find_by_content(content)
            if existing is None:
                self._memory_index.add_entry(
                    category=category,
                    source_file=str(file_path.relative_to(MEMORY_DIR)),
                    content_preview=content,
                    importance=item.get("importance", 3),
                )
        return wrote
