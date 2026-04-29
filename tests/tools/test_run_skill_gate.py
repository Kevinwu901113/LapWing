"""Approval gate for run_skill.

The gate fires only on the two automated/lossy surfaces:
- chat_extended: stable skills whose trust_required matches the caller's
                 auth_level
- inner_tick:    stable skills explicitly tagged auto_run or inner_tick

Other profiles (CLI, OWNER tooling, task_execution) bypass the gate —
they have their own access controls.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.tools.skill_tools import _gate_run_skill, run_skill_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _stable_skill(*, trust: str = "guest", tags: list | None = None) -> dict:
    return {
        "meta": {
            "id": "s1",
            "maturity": "stable",
            "trust_required": trust,
            "tags": list(tags or []),
        },
        "code": "def run(): return 1",
        "file_path": "/tmp/x",
    }


def _draft_skill(**kw) -> dict:
    skill = _stable_skill(**kw)
    skill["meta"]["maturity"] = "draft"
    return skill


def _testing_skill(**kw) -> dict:
    skill = _stable_skill(**kw)
    skill["meta"]["maturity"] = "testing"
    return skill


def _broken_skill(**kw) -> dict:
    skill = _stable_skill(**kw)
    skill["meta"]["maturity"] = "broken"
    return skill


def _unmarked_skill(**kw) -> dict:
    skill = _stable_skill(**kw)
    skill["meta"].pop("maturity", None)
    return skill


class TestPureGateLogic:
    """_gate_run_skill — unit tests for the pure decision function."""

    def test_non_gated_profile_passes_anything(self):
        # CLI / task_execution / coder_* surfaces are not gated.
        assert _gate_run_skill(
            _draft_skill(), profile="task_execution", auth_level=2
        ) is None
        assert _gate_run_skill(
            _broken_skill(), profile="cli", auth_level=2
        ) is None
        assert _gate_run_skill(None, profile="task_execution", auth_level=2) is None

    def test_chat_extended_requires_stable(self):
        for builder in (_draft_skill, _testing_skill, _broken_skill, _unmarked_skill):
            reason = _gate_run_skill(builder(), profile="standard", auth_level=2)
            assert reason is not None
            assert "stable" in reason

    def test_chat_extended_stable_with_satisfied_trust_passes(self):
        # OWNER (auth_level=2) satisfies any trust_required.
        assert _gate_run_skill(
            _stable_skill(trust="owner"), profile="standard", auth_level=2
        ) is None
        assert _gate_run_skill(
            _stable_skill(trust="trusted"), profile="standard", auth_level=2
        ) is None
        assert _gate_run_skill(
            _stable_skill(trust="guest"), profile="standard", auth_level=0
        ) is None

    def test_chat_extended_blocks_when_trust_insufficient(self):
        # GUEST cannot run a skill that requires OWNER.
        reason = _gate_run_skill(
            _stable_skill(trust="owner"),
            profile="standard",
            auth_level=0,
        )
        assert reason is not None
        assert "trust_required" in reason

    def test_inner_tick_requires_stable(self):
        for builder in (_draft_skill, _testing_skill, _broken_skill, _unmarked_skill):
            reason = _gate_run_skill(
                builder(tags=["auto_run"]),
                profile="inner_tick",
                auth_level=2,
            )
            assert reason is not None

    def test_inner_tick_requires_autonomous_tag(self):
        # Stable but not tagged → blocked.
        reason = _gate_run_skill(
            _stable_skill(tags=["misc"]),
            profile="inner_tick",
            auth_level=2,
        )
        assert reason is not None
        assert "auto_run" in reason or "inner_tick" in reason

    def test_inner_tick_passes_with_auto_run_tag(self):
        assert _gate_run_skill(
            _stable_skill(tags=["auto_run"]),
            profile="inner_tick",
            auth_level=2,
        ) is None

    def test_inner_tick_passes_with_inner_tick_tag(self):
        assert _gate_run_skill(
            _stable_skill(tags=["inner_tick"]),
            profile="inner_tick",
            auth_level=2,
        ) is None

    def test_inner_tick_does_not_check_trust(self):
        # tags pass; trust_required=owner alone shouldn't block inner_tick
        # — the autonomous-tag itself is the operator's "this is safe to
        # run unattended" signal.
        assert _gate_run_skill(
            _stable_skill(trust="owner", tags=["auto_run"]),
            profile="inner_tick",
            auth_level=2,
        ) is None


class _FakeStore:
    def __init__(self, skill: dict | None):
        self._skill = skill

    def read(self, skill_id: str) -> dict | None:
        return self._skill


class _FakeExecutor:
    def __init__(self):
        self.calls: list = []

    async def execute(self, skill_id, *, arguments, timeout):
        self.calls.append((skill_id, arguments, timeout))

        class _R:
            success = True
            output = "ok"
            error = ""
            exit_code = 0
            timed_out = False

        return _R()


def _ctx(*, profile: str, auth_level: int = 2, skill: dict | None = None,
         executor=None) -> ToolExecutionContext:
    services = {"skill_store": _FakeStore(skill), "skill_executor": executor or _FakeExecutor()}
    async def _noop_shell(_):
        from src.tools.shell_executor import ShellResult
        return ShellResult(stdout="", stderr="", return_code=0)
    return ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd="/tmp",
        services=services,
        auth_level=auth_level,
        runtime_profile=profile,
    )


class TestRunSkillExecutorIntegration:
    async def test_chat_extended_blocks_draft_skill(self):
        executor = _FakeExecutor()
        ctx = _ctx(profile="standard", skill=_draft_skill(), executor=executor)
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert "stable" in result.reason
        assert executor.calls == [], "denied skill must not reach SkillExecutor"

    async def test_chat_extended_runs_stable_skill(self):
        executor = _FakeExecutor()
        ctx = _ctx(profile="standard", skill=_stable_skill(trust="guest"), executor=executor)
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is True
        assert result.payload["executed"] is True
        assert len(executor.calls) == 1

    async def test_chat_extended_blocks_when_trust_insufficient(self):
        executor = _FakeExecutor()
        # GUEST trying to run a skill that requires OWNER trust
        ctx = _ctx(
            profile="standard",
            auth_level=0,
            skill=_stable_skill(trust="owner"),
            executor=executor,
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert "trust_required" in result.reason
        assert executor.calls == []

    async def test_inner_tick_blocks_untagged_stable_skill(self):
        executor = _FakeExecutor()
        ctx = _ctx(profile="inner_tick", skill=_stable_skill(tags=[]), executor=executor)
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert executor.calls == []

    async def test_inner_tick_runs_auto_run_tagged_skill(self):
        executor = _FakeExecutor()
        ctx = _ctx(
            profile="inner_tick",
            skill=_stable_skill(tags=["auto_run", "misc"]),
            executor=executor,
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is True
        assert len(executor.calls) == 1

    async def test_inner_tick_blocks_draft_even_when_tagged(self):
        executor = _FakeExecutor()
        ctx = _ctx(
            profile="inner_tick",
            skill=_draft_skill(tags=["auto_run"]),
            executor=executor,
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert executor.calls == []

    async def test_inner_tick_blocks_missing_skill(self):
        executor = _FakeExecutor()
        ctx = _ctx(profile="inner_tick", skill=None, executor=executor)
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "ghost"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert executor.calls == []

    async def test_task_execution_bypasses_gate(self):
        # Other profiles (operator-driven) are not gated — they may run any
        # skill in any state. This is intentional: the gate exists for the
        # automated chat / autonomous surfaces only.
        executor = _FakeExecutor()
        ctx = _ctx(
            profile="task_execution",
            skill=_draft_skill(),
            executor=executor,
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is True
        assert len(executor.calls) == 1


class TestCreateSkillStaysDraft:
    """create_skill output stays draft and is not auto-runnable.

    SkillStore.create() hard-codes maturity="draft" — verified via
    a real store round-trip.
    """

    def test_new_skill_is_draft(self, tmp_path):
        from src.skills.skill_store import SkillStore

        store = SkillStore(skills_dir=tmp_path)
        store.create(
            skill_id="abc",
            name="abc",
            description="d",
            code="def run():\n    return 1\n",
        )
        skill = store.read("abc")
        assert skill is not None
        assert skill["meta"]["maturity"] == "draft"

    async def test_draft_from_create_skill_blocked_in_chat_extended(self, tmp_path):
        from src.skills.skill_store import SkillStore

        store = SkillStore(skills_dir=tmp_path)
        store.create(
            skill_id="abc",
            name="abc",
            description="d",
            code="def run():\n    return 1\n",
        )

        executor = _FakeExecutor()
        services = {"skill_store": store, "skill_executor": executor}
        async def _noop_shell(_):
            from src.tools.shell_executor import ShellResult
            return ShellResult(stdout="", stderr="", return_code=0)
        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services=services,
            auth_level=2,
            runtime_profile="standard",
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "abc"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False
        assert result.payload["denied"] is True
        assert executor.calls == []
