"""Brain must reset BrowserGuard's per-session action budget at session
boundaries — otherwise long-running processes accumulate counts forever
and `max_actions_per_session` becomes effectively a hard "do once and
quit" cap.

Boundaries that reset the budget:
- think_conversational entry (new user turn)
- think_inner entry (inner tick session)

The reset is best-effort — when no guard is mounted (Phase 0 / browser
disabled), the call is a no-op.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.browser_guard import BrowserGuard


@pytest.fixture
def brain(tmp_path):
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


def test_reset_session_budgets_clears_browser_guard_count(brain):
    guard = BrowserGuard(block_internal_network=False, max_actions_per_session=5)
    # Spend some budget across two browser sessions
    guard.check_action("click", "OK")
    guard.check_action("click", "OK")
    assert guard.actions_used() == 2

    brain.task_runtime._browser_guard = guard
    brain._reset_session_budgets()
    assert guard.actions_used() == 0


def test_reset_session_budgets_is_noop_without_guard(brain):
    """Phase 0 / disabled browser path: no guard mounted, call is safe."""
    brain.task_runtime._browser_guard = None
    # Must not raise
    brain._reset_session_budgets()


@pytest.mark.asyncio
async def test_think_inner_resets_budget_at_entry(brain):
    """An inner tick is a fresh autonomous session — its action budget
    must start at zero regardless of prior turns / earlier inner ticks."""
    guard = BrowserGuard(block_internal_network=False, max_actions_per_session=3)
    for _ in range(3):
        guard.check_action("click", "OK")
    assert guard.actions_used() == 3

    brain.task_runtime._browser_guard = guard
    brain.trajectory_store = AsyncMock()
    brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        return list(recent)

    async def fake_complete(*args, **kwargs):
        return "ok [NEXT: 30m]"

    brain._render_messages = fake_render  # type: ignore[method-assign]
    brain._complete_chat = fake_complete  # type: ignore[method-assign]

    await brain.think_inner()
    # Reset must have fired. The fake _complete_chat doesn't run any
    # browser actions, so actions_used should remain 0.
    assert guard.actions_used() == 0


def test_budget_does_not_accumulate_across_independent_sessions(brain):
    """Locking the spec: across two independent sessions, the second
    session sees a fresh budget, not the carry-over from the first."""
    guard = BrowserGuard(block_internal_network=False, max_actions_per_session=3)
    brain.task_runtime._browser_guard = guard

    # Session 1: spend budget
    for _ in range(3):
        guard.check_action("click", "OK")
    assert guard.actions_used() == 3
    # 4th would normally block; reset fires at session boundary instead
    brain._reset_session_budgets()
    # Session 2: budget is fresh
    assert guard.check_action("click", "OK").action == "allow"
    assert guard.actions_used() == 1
