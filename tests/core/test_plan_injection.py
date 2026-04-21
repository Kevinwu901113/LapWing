"""_with_plan_context 注入测试。"""

from unittest.mock import MagicMock

from src.core.plan_state import PlanState
from src.core.task_runtime import TaskRuntime


def _make_runtime() -> TaskRuntime:
    router = MagicMock()
    registry = MagicMock()
    return TaskRuntime(router=router, tool_registry=registry)


class TestWithPlanContext:
    def test_no_plan_returns_unchanged(self):
        rt = _make_runtime()
        msgs = [{"role": "user", "content": "hi"}]
        result = rt._with_plan_context(msgs, {})
        assert result is msgs

    def test_none_services_returns_unchanged(self):
        rt = _make_runtime()
        msgs = [{"role": "user", "content": "hi"}]
        result = rt._with_plan_context(msgs, None)
        assert result is msgs

    def test_appends_to_existing_system_message(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "step A"},
            {"description": "step B"},
        ])
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "do it"},
        ]
        result = rt._with_plan_context(msgs, {"plan_state": plan})

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "You are helpful." in result[0]["content"]
        assert "当前计划" in result[0]["content"]
        assert "step A" in result[0]["content"]
        assert "step B" in result[0]["content"]

    def test_creates_system_message_when_none_exists(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "step A"},
            {"description": "step B"},
        ])
        msgs = [{"role": "user", "content": "do it"}]
        result = rt._with_plan_context(msgs, {"plan_state": plan})

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "当前计划" in result[0]["content"]
        assert result[1]["role"] == "user"

    def test_does_not_mutate_original_messages(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "step A"},
            {"description": "step B"},
        ])
        original_system = {"role": "system", "content": "Original content."}
        msgs = [original_system, {"role": "user", "content": "do it"}]
        result = rt._with_plan_context(msgs, {"plan_state": plan})

        # Original dict not mutated
        assert original_system["content"] == "Original content."
        # Result is a different list
        assert result is not msgs
        # Result system message is a different dict
        assert result[0] is not original_system
