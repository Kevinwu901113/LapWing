"""Lapwing brain callable for DeepEval.

Provides a minimal Brain instance for evaluation, bypassing adapters
and background services. Conversation state is maintained per-session
via chat_id.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from config.settings import DB_PATH


_brain_instance = None
_session_id: str | None = None


async def _get_brain():
    global _brain_instance
    if _brain_instance is not None:
        return _brain_instance

    from src.core.brain import LapwingBrain
    from src.core.state_view_builder import StateViewBuilder

    brain = LapwingBrain(db_path=DB_PATH)
    brain.state_view_builder = StateViewBuilder()
    _brain_instance = brain
    return brain


async def lapwing_callback(user_input: str) -> str:
    """Send a user message to Lapwing and collect the reply text.

    Maintains conversation context across calls within the same session.
    Call reset_session() between independent scenarios.
    """
    global _session_id
    if _session_id is None:
        _session_id = f"eval-{uuid.uuid4().hex[:8]}"

    brain = await _get_brain()
    collected: list[str] = []

    async def _collect(text: str) -> None:
        collected.append(text)

    await brain.think_conversational(
        chat_id=_session_id,
        user_message=user_input,
        send_fn=_collect,
        adapter="desktop",
        user_id="eval_kevin",
    )
    return "\n\n".join(collected) if collected else ""


def reset_session() -> None:
    """Clear conversation context between scenarios."""
    global _session_id
    _session_id = None
