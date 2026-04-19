"""EpisodicExtractor — LLM 驱动的对话 → 情景记忆提取器。

Blueprint v2.0 Step 7 §M3.a. 每次对话结束后触发一次：读 trajectory 窗口，
调 memory_processing slot 摘要，写入 EpisodicStore。

设计：
- 不 retry 失败——记忆系统对偶发丢失容忍度高，下一次对话再提取
- 不阻塞主对话路径——调用方用 asyncio.create_task fire-and-forget
- 提取 prompt 在 prompts/episodic_extract.md，hot-reloadable
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt
from src.core.trajectory_store import (
    TrajectoryEntry,
    TrajectoryEntryType,
)

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.core.trajectory_store import TrajectoryStore
    from src.memory.episodic_store import EpisodicStore

logger = logging.getLogger("lapwing.memory.episodic_extractor")


class EpisodicExtractor:
    """提取最近 trajectory 窗口为一条情景记忆。"""

    def __init__(
        self,
        *,
        router: "LLMRouter",
        trajectory_store: "TrajectoryStore",
        episodic_store: "EpisodicStore",
        window_size: int = 20,
        min_turns: int = 3,
        prompt_name: str = "episodic_extract",
    ) -> None:
        self._router = router
        self._trajectory = trajectory_store
        self._episodic = episodic_store
        self._window_size = window_size
        self._min_turns = min_turns
        self._prompt_name = prompt_name

    async def extract_from_chat(self, chat_id: str) -> bool:
        """Trigger one extraction for ``chat_id``. Returns True on success.

        Pulls the last ``window_size`` user/assistant rows from trajectory.
        If fewer than ``min_turns`` turns exist, skips — not enough signal.
        """
        rows = await self._trajectory.relevant_to_chat(
            chat_id, n=self._window_size, include_inner=False,
        )
        pairs = _user_assistant_pairs(rows)
        if len(pairs) < self._min_turns:
            logger.debug(
                "[episodic] chat %s has %d usable turns (< %d), skipping",
                chat_id, len(pairs), self._min_turns,
            )
            return False

        conversation_text = _format_pairs(pairs)
        trajectory_ids = [r.id for r in rows]

        try:
            prompt_template = load_prompt(self._prompt_name)
        except Exception:
            logger.warning(
                "[episodic] prompt %s not found; using default",
                self._prompt_name,
            )
            prompt_template = _FALLBACK_PROMPT

        prompt = prompt_template.replace("{conversation}", conversation_text)

        try:
            summary_raw = await self._router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=400,
                session_key=f"episodic:{chat_id}",
                origin="memory.episodic_extractor",
            )
        except Exception as exc:
            logger.warning("[episodic] LLM call failed: %s", exc)
            return False

        title, summary = _parse_title_body(summary_raw)
        if not summary:
            logger.debug("[episodic] empty summary; skipping")
            return False

        try:
            await self._episodic.add_episode(
                summary=summary,
                title=title,
                source_trajectory_ids=trajectory_ids,
            )
            return True
        except Exception as exc:
            logger.warning("[episodic] add_episode failed: %s", exc)
            return False


# ── Formatting helpers ──────────────────────────────────────────────

def _user_assistant_pairs(
    rows: list[TrajectoryEntry],
) -> list[tuple[str, str]]:
    """Project trajectory rows into (role, text) pairs.

    Keeps only user/assistant-text content so system / tool-call / inner
    rows don't inflate the prompt. Oldest→newest preserved.
    """
    out: list[tuple[str, str]] = []
    for r in rows:
        text = _row_text(r)
        if not text:
            continue
        if r.entry_type == TrajectoryEntryType.USER_MESSAGE.value:
            out.append(("user", text))
        elif r.entry_type in (
            TrajectoryEntryType.TELL_USER.value,
            TrajectoryEntryType.ASSISTANT_TEXT.value,
        ):
            out.append(("assistant", text))
    return out


def _row_text(entry: TrajectoryEntry) -> str:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
    text = content.get("text")
    if isinstance(text, str):
        return text
    return ""


def _format_pairs(pairs: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for role, text in pairs:
        tag = "Kevin" if role == "user" else "我"
        lines.append(f"{tag}：{text}")
    return "\n".join(lines)


def _parse_title_body(raw: str) -> tuple[str | None, str]:
    """Split ``title\\n\\nbody`` layout; fallback to body-only."""
    if not raw:
        return None, ""
    stripped = raw.strip()
    if not stripped:
        return None, ""
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[1].strip() == "":
        return lines[0].strip() or None, "\n".join(lines[2:]).strip()
    # No blank separator → treat first line as both title and summary.
    return lines[0].strip() or None, stripped


_FALLBACK_PROMPT = (
    "根据以下对话，用第一人称写一句事件标题（≤30字），空行，再写 2-5 句事件正文。\n\n"
    "【对话】\n{conversation}\n"
)
