"""Regression test: think_inner must send a non-empty messages list.

Trajectory rows for inner ticks are stored with source_chat_id=NULL.
``_load_history`` calls ``relevant_to_chat(include_inner=False)``, which
filters by ``source_chat_id = chat_id`` and thus returns [] for the
``_inner_tick`` session key. Before the fix, ``think_inner`` relied on
that history path and produced a messages list with only the system row,
which Anthropic's API rejects with ``messages must not be empty``.

This test locks in the behaviour by asserting the recent list passed to
``_render_messages`` (and the messages handed to ``_complete_chat``)
always contains the inner_prompt as a user-role turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def brain(tmp_path):
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.ConversationMemory"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    b.memory.append = AsyncMock()
    b.memory.get = AsyncMock(return_value=[])
    return b


async def test_think_inner_sends_inner_prompt_as_user_turn(brain):
    """Messages must contain the inner_prompt as role=user, not just system."""
    # Trajectory empty for _inner_tick — mirrors production (inner rows use
    # source_chat_id=NULL). The bug: recent list ends up empty, messages list
    # becomes [{system}], Anthropic rejects.
    brain.trajectory_store = AsyncMock()
    brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

    captured: dict = {}

    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        captured["recent"] = recent
        captured["inner"] = inner
        return [{"role": "system", "content": "<sys>"}] + list(recent)

    async def fake_complete(chat_id, messages, user_msg, **kwargs):
        captured["messages"] = messages
        return "无事 [NEXT: 30m]"

    brain._render_messages = fake_render  # type: ignore[method-assign]
    brain._complete_chat = fake_complete  # type: ignore[method-assign]
    brain._skill_activation_tool_enabled = MagicMock(return_value=False)

    reply, next_interval, did_something = await brain.think_inner()

    # Core regression: recent list must carry at least one user turn.
    assert captured["inner"] is True
    recent = captured["recent"]
    assert len(recent) >= 1
    assert recent[-1]["role"] == "user"
    assert recent[-1]["content"].startswith("[内部意识 tick")

    # And the messages list handed to _complete_chat must include a user msg.
    msgs = captured["messages"]
    assert any(m["role"] == "user" for m in msgs), \
        f"messages list has no user role — Anthropic will reject: {msgs}"

    # Sanity on parse_next_interval reach-through.
    assert next_interval == 1800
    assert did_something is False  # "无事" → did_nothing


async def test_think_inner_urgent_items_flow_into_user_turn(brain):
    """Urgency items must be visible in the user turn the LLM sees."""
    brain.trajectory_store = AsyncMock()
    brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

    captured: dict = {}

    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        captured["recent"] = recent
        return [{"role": "system", "content": "<sys>"}] + list(recent)

    async def fake_complete(chat_id, messages, user_msg, **kwargs):
        return "处理了 [NEXT: 10m]"

    brain._render_messages = fake_render  # type: ignore[method-assign]
    brain._complete_chat = fake_complete  # type: ignore[method-assign]
    brain._skill_activation_tool_enabled = MagicMock(return_value=False)

    await brain.think_inner(urgent_items=[
        {"type": "reminder", "content": "给 Kevin 发消息"},
    ])

    user_content = captured["recent"][-1]["content"]
    assert "给 Kevin 发消息" in user_content
    assert "紧急事件" in user_content
