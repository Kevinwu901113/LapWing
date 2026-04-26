"""Focus archive abstraction.

Focus owns topic boundaries; archivers own how a closed focus is persisted
into long-term memory. The current implementation writes through EpisodicStore.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.core.prompt_loader import load_prompt
from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType

logger = logging.getLogger("lapwing.core.focus_archiver")


class FocusArchiver(Protocol):
    async def archive(
        self,
        entries: list[TrajectoryEntry],
        metadata: dict[str, Any],
    ) -> str:
        """Archive trajectory entries and return a durable reference id."""

    async def retrieve(self, query: str, n: int = 3) -> list[dict[str, Any]]:
        """Retrieve archived focus summaries."""


class EpisodicArchiver:
    """Archive closed focus slices through the existing EpisodicStore."""

    def __init__(self, episodic_store: Any, llm_router: Any) -> None:
        self._episodic = episodic_store
        self._router = llm_router

    async def archive(
        self,
        entries: list[TrajectoryEntry],
        metadata: dict[str, Any],
    ) -> str:
        conversation_text = _format_entries(entries)
        if not conversation_text:
            conversation_text = str(metadata.get("summary") or "空焦点")

        summary = await self._generate_summary(conversation_text)
        title = str(metadata.get("summary") or "未命名焦点")
        trajectory_ids = [entry.id for entry in entries]

        episode = await self._episodic.add_episode(
            summary=summary,
            title=title,
            source_trajectory_ids=trajectory_ids,
        )
        return getattr(episode, "episode_id", str(episode))

    async def retrieve(self, query: str, n: int = 3) -> list[dict[str, Any]]:
        rows = await self._episodic.query(query, top_k=n)
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append({
                "id": getattr(row, "episode_id", ""),
                "title": getattr(row, "title", ""),
                "summary": getattr(row, "summary", ""),
                "score": getattr(row, "score", 0.0),
            })
        return out

    async def _generate_summary(self, conversation_text: str) -> str:
        try:
            prompt_template = load_prompt("episodic_extract")
        except Exception:
            prompt_template = _FALLBACK_PROMPT
        prompt = prompt_template.replace("{conversation}", conversation_text)
        try:
            raw = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=400,
                origin="focus_archiver.episodic_summary",
            )
        except Exception as exc:
            logger.warning("focus archive summary LLM failed: %s", exc)
            return conversation_text[:400]
        return (raw or "").strip() or conversation_text[:400]


def _format_entries(entries: list[TrajectoryEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        text = _entry_text(entry)
        if not text:
            continue
        if entry.entry_type == TrajectoryEntryType.USER_MESSAGE.value:
            label = "Kevin"
        elif entry.entry_type in (
            TrajectoryEntryType.TELL_USER.value,
            TrajectoryEntryType.ASSISTANT_TEXT.value,
        ):
            label = "我"
        elif entry.entry_type == TrajectoryEntryType.TOOL_CALL.value:
            label = "工具调用"
        elif entry.entry_type == TrajectoryEntryType.TOOL_RESULT.value:
            label = "工具结果"
        else:
            continue
        lines.append(f"{label}：{text}")
    return "\n".join(lines)


def _entry_text(entry: TrajectoryEntry) -> str:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        messages = content.get("messages")
        if isinstance(messages, list) and messages:
            return "\n".join(str(item) for item in messages)
    if entry.entry_type == TrajectoryEntryType.TOOL_CALL.value:
        return str(content.get("tool_name") or content.get("text") or "")
    if entry.entry_type == TrajectoryEntryType.TOOL_RESULT.value:
        text = content.get("result_preview") or content.get("text")
        return str(text or "")
    text = content.get("text")
    return text if isinstance(text, str) else ""


_FALLBACK_PROMPT = (
    "根据以下对话，用第一人称写一句事件标题（≤30字），空行，再写 2-5 句事件正文。\n\n"
    "【对话】\n{conversation}\n"
)
