"""Transitional shim: TrajectoryEntry → legacy ``{"role", "content"}`` dict.

Blueprint v2.0 Step 2g. Callers that historically consumed
``ConversationMemory.get(chat_id)`` / ``get_session_messages`` expect a
list of ``{"role": "user"|"assistant", "content": str}`` dicts. Step 2g
switches the underlying source to ``TrajectoryStore`` but keeps the same
output shape so the downstream code paths (``_recent_messages``,
``PromptBuilder`` injection, LLM routing) don't have to change in the
same commit.

This helper is **temporary**. Step 3 lands ``StateSerializer``, which
renders the full state directly into prompt bytes; the legacy dict
shape becomes unnecessary and this file disappears.
"""

from __future__ import annotations

from typing import Iterable

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType


# Trajectory entry types that map to conversation messages.
_ROLE_MAP: dict[str, str] = {
    TrajectoryEntryType.USER_MESSAGE.value: "user",
    TrajectoryEntryType.TELL_USER.value: "assistant",
    TrajectoryEntryType.ASSISTANT_TEXT.value: "assistant",
}


def trajectory_entries_to_legacy_messages(
    entries: Iterable[TrajectoryEntry],
    *,
    include_inner: bool = False,
) -> list[dict]:
    """Convert trajectory entries into the legacy ``[{"role", "content"}]`` shape.

    Entry-type handling:
      - ``USER_MESSAGE`` → ``role="user"``, content from ``entry.content["text"]``
      - ``ASSISTANT_TEXT`` → ``role="assistant"``
      - ``TELL_USER`` → ``role="assistant"``; if the payload holds a
        ``messages`` list (Step 5 multi-segment output) they're joined
        with newlines
      - ``INNER_THOUGHT`` → kept only if ``include_inner=True``; rendered
        as a ``role="system"`` note with a ``[内部思考]`` prefix so the
        model recognises it is not a user-facing exchange
      - Any other type (``TOOL_CALL``, ``TOOL_RESULT``, ``STATE_CHANGE``,
        ``STAY_SILENT``) is dropped — those belong in Step 3's state
        serializer view, not in legacy message history.

    The output preserves the input iteration order (trajectory reads
    already return oldest→newest, so passing the store's output straight
    through is correct).
    """
    out: list[dict] = []
    for entry in entries:
        role = _ROLE_MAP.get(entry.entry_type)
        if role is not None:
            text = _extract_text(entry)
            if text is None:
                continue
            out.append({"role": role, "content": text})
            continue

        if entry.entry_type == TrajectoryEntryType.INNER_THOUGHT.value:
            if not include_inner:
                continue
            text = _extract_text(entry)
            if text is None:
                continue
            out.append({"role": "system", "content": f"[内部思考] {text}"})
            continue

        # other types skipped silently
    return out


def _extract_text(entry: TrajectoryEntry) -> str | None:
    """Return the user-facing text payload, or None if the row is unusable."""
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
        text = content.get("text")
        if isinstance(text, str):
            return text
        return None
    text = content.get("text")
    if isinstance(text, str):
        return text
    return None
