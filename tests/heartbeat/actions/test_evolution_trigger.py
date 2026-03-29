"""Tests for evolution trigger in SelfReflectionAction and PromptEvolutionAction."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _make_ctx(weekday=0):
    ctx = MagicMock()
    # Monday=0, Sunday=6
    ctx.now = datetime(2026, 3, 30 if weekday == 0 else 29, tzinfo=timezone.utc)  # Mon or Sun
    ctx.chat_id = "test_chat"
    return ctx


class TestSelfReflectionEvolutionTrigger:
    async def test_triggers_evolution_when_rules_threshold_met(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        # 5 rules — threshold met
        rules_path.write_text(
            "# 行为规则\n"
            "- [2026-01-01] 规则一\n"
            "- [2026-01-02] 规则二\n"
            "- [2026-01-03] 规则三\n"
            "- [2026-01-04] 规则四\n"
            "- [2026-01-05] 规则五\n",
            encoding="utf-8",
        )

        brain = MagicMock()
        brain.self_reflection = MagicMock()
        brain.self_reflection.reflect_on_day = AsyncMock(return_value="日记内容")
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(
            return_value={"success": True, "summary": "微调了一处"}
        )
        brain.reload_persona = MagicMock()

        with patch("src.heartbeat.actions.self_reflection.RULES_PATH", rules_path):
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            action = SelfReflectionAction()
            await action.execute(_make_ctx(), brain, MagicMock())

        brain.evolution_engine.evolve.assert_awaited_once()
        brain.reload_persona.assert_called_once()

    async def test_skips_evolution_when_rules_below_threshold(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        # Only 3 rules — below threshold of 5
        rules_path.write_text(
            "# 行为规则\n"
            "- [2026-01-01] 规则一\n"
            "- [2026-01-02] 规则二\n"
            "- [2026-01-03] 规则三\n",
            encoding="utf-8",
        )

        brain = MagicMock()
        brain.self_reflection = MagicMock()
        brain.self_reflection.reflect_on_day = AsyncMock(return_value="日记")
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(return_value={"success": True})
        brain.reload_persona = MagicMock()

        with patch("src.heartbeat.actions.self_reflection.RULES_PATH", rules_path):
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            action = SelfReflectionAction()
            await action.execute(_make_ctx(), brain, MagicMock())

        brain.evolution_engine.evolve.assert_not_awaited()
        brain.reload_persona.assert_not_called()

    async def test_skips_evolution_when_no_rules_file(self, tmp_path):
        rules_path = tmp_path / "nonexistent_rules.md"

        brain = MagicMock()
        brain.self_reflection = MagicMock()
        brain.self_reflection.reflect_on_day = AsyncMock(return_value="日记")
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(return_value={"success": True})

        with patch("src.heartbeat.actions.self_reflection.RULES_PATH", rules_path):
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            action = SelfReflectionAction()
            await action.execute(_make_ctx(), brain, MagicMock())

        brain.evolution_engine.evolve.assert_not_awaited()

    async def test_handles_evolution_failure_gracefully(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        rules_path.write_text(
            "- [2026-01-01] 规则一\n- [2026-01-02] 规则二\n"
            "- [2026-01-03] 规则三\n- [2026-01-04] 规则四\n- [2026-01-05] 规则五\n",
            encoding="utf-8",
        )

        brain = MagicMock()
        brain.self_reflection = MagicMock()
        brain.self_reflection.reflect_on_day = AsyncMock(return_value="日记")
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(side_effect=RuntimeError("fail"))
        brain.reload_persona = MagicMock()

        with patch("src.heartbeat.actions.self_reflection.RULES_PATH", rules_path):
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            action = SelfReflectionAction()
            # Should not raise
            await action.execute(_make_ctx(), brain, MagicMock())

        brain.reload_persona.assert_not_called()

    async def test_no_trigger_when_evolution_engine_is_none(self, tmp_path):
        rules_path = tmp_path / "rules.md"
        rules_path.write_text(
            "- [2026-01-01] 规则一\n- [2026-01-02] 规则二\n"
            "- [2026-01-03] 规则三\n- [2026-01-04] 规则四\n- [2026-01-05] 规则五\n",
            encoding="utf-8",
        )

        brain = MagicMock()
        brain.self_reflection = MagicMock()
        brain.self_reflection.reflect_on_day = AsyncMock(return_value="日记")
        brain.evolution_engine = None
        brain.reload_persona = MagicMock()

        with patch("src.heartbeat.actions.self_reflection.RULES_PATH", rules_path):
            from src.heartbeat.actions.self_reflection import SelfReflectionAction
            action = SelfReflectionAction()
            await action.execute(_make_ctx(), brain, MagicMock())

        brain.reload_persona.assert_not_called()


class TestPromptEvolutionAction:
    async def test_uses_evolution_engine_not_prompt_evolver(self):
        brain = MagicMock()
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(
            return_value={"success": True, "summary": "微调"}
        )
        brain.reload_persona = MagicMock()
        brain.prompt_evolver = MagicMock()  # Should not be called

        # Sunday
        ctx = _make_ctx(weekday=6)
        ctx.now = datetime(2026, 3, 29, tzinfo=timezone.utc)

        from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
        action = PromptEvolutionAction()
        await action.execute(ctx, brain, MagicMock())

        brain.evolution_engine.evolve.assert_awaited_once()
        brain.reload_persona.assert_called_once()
        brain.prompt_evolver.evolve.assert_not_called()

    async def test_only_triggers_on_sunday(self):
        brain = MagicMock()
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(return_value={"success": True})
        brain.reload_persona = MagicMock()

        # Monday (weekday=0) — should NOT trigger
        ctx = _make_ctx(weekday=0)
        ctx.now = datetime(2026, 3, 30, tzinfo=timezone.utc)

        from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
        action = PromptEvolutionAction()
        await action.execute(ctx, brain, MagicMock())

        brain.evolution_engine.evolve.assert_not_awaited()

    async def test_skips_when_no_evolution_engine(self):
        brain = MagicMock()
        brain.evolution_engine = None
        brain.reload_persona = MagicMock()

        ctx = _make_ctx(weekday=6)
        ctx.now = datetime(2026, 3, 29, tzinfo=timezone.utc)

        from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
        action = PromptEvolutionAction()
        await action.execute(ctx, brain, MagicMock())

        brain.reload_persona.assert_not_called()

    async def test_reloads_persona_on_success(self):
        brain = MagicMock()
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(
            return_value={"success": True, "summary": "一点点变化"}
        )
        brain.reload_persona = MagicMock()

        ctx = _make_ctx(weekday=6)
        ctx.now = datetime(2026, 3, 29, tzinfo=timezone.utc)

        from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
        action = PromptEvolutionAction()
        await action.execute(ctx, brain, MagicMock())

        brain.reload_persona.assert_called_once()

    async def test_skips_reload_on_failure(self):
        brain = MagicMock()
        brain.evolution_engine = MagicMock()
        brain.evolution_engine.evolve = AsyncMock(
            return_value={"success": False, "error": "没有可用材料"}
        )
        brain.reload_persona = MagicMock()

        ctx = _make_ctx(weekday=6)
        ctx.now = datetime(2026, 3, 29, tzinfo=timezone.utc)

        from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
        action = PromptEvolutionAction()
        await action.execute(ctx, brain, MagicMock())

        brain.reload_persona.assert_not_called()
