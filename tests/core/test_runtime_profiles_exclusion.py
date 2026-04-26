"""RuntimeProfile 工具暴露互斥测试（修复 A）。

确保同一个 profile 不会同时把 raw 调研工具（research/browse）和委托工具
（delegate_to_researcher/delegate_to_coder）暴露给同一个 LLM ——否则
主脑会在两条路径间纠结，Agent Team 形同虚设。
"""

from __future__ import annotations

from src.core.runtime_profiles import (
    AGENT_CODER_PROFILE,
    AGENT_RESEARCHER_PROFILE,
    CHAT_EXTENDED_PROFILE,
    CHAT_MINIMAL_PROFILE,
    CHAT_SHELL_PROFILE,
    CODER_SNIPPET_PROFILE,
    CODER_WORKSPACE_PROFILE,
    FILE_OPS_PROFILE,
    INNER_TICK_PROFILE,
    TASK_EXECUTION_PROFILE,
    _PROFILES,
    get_runtime_profile,
)
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec


_RESEARCH_NAMES = {"research", "browse"}
_DELEGATE_NAMES = {"delegate_to_researcher", "delegate_to_coder"}


async def _noop_executor(req: ToolExecutionRequest, ctx) -> ToolExecutionResult:
    return ToolExecutionResult(success=True, payload={})


def _make_full_registry() -> ToolRegistry:
    """登记所有 profile 可能引用的工具，让 list_tools / get_tools_for_profile
    能像生产那样跑全。"""
    registry = ToolRegistry()

    def _spec(name: str, capability: str = "general", *,
              visibility: str = "model") -> ToolSpec:
        return ToolSpec(
            name=name,
            description=name,
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor,
            capability=capability,
            visibility=visibility,  # type: ignore[arg-type]
            risk_level="low",
        )

    # research 系
    registry.register(_spec("research", "web"))
    registry.register(_spec("browse", "browser"))
    # delegate 系
    registry.register(_spec("delegate_to_researcher", "agent"))
    registry.register(_spec("delegate_to_coder", "agent"))
    # 其他被 profile 直接引用的工具
    extras = [
        ("get_current_datetime", "general"),
        ("send_message", "general"),
        ("add_correction", "general"),
        ("get_sports_score", "web"),
        ("set_reminder", "schedule"),
        ("view_reminders", "schedule"),
        ("cancel_reminder", "schedule"),
        ("commit_promise", "commitment"),
        ("fulfill_promise", "commitment"),
        ("abandon_promise", "commitment"),
        ("close_focus", "general"),
        ("recall_focus", "general"),
        ("recall", "memory"),
        ("write_note", "memory"),
        ("read_note", "memory"),
        ("list_notes", "memory"),
        ("search_notes", "memory"),
        ("create_skill", "skill"),
        ("run_skill", "skill"),
        # browser_* — needed for INNER_TICK exclusion check
        ("browser_open", "browser"),
        ("browser_click", "browser"),
        # shell + arbitrary file writes — INNER_TICK must exclude these
        ("execute_shell", "shell"),
        ("read_file", "shell"),
        ("write_file", "shell"),
        # identity mutation tools — INNER_TICK must exclude
        ("read_soul", "identity"),
        ("edit_soul", "identity"),
        # CODER_SNIPPET / CODER_WORKSPACE 内部工具
    ]
    for name, cap in extras:
        registry.register(_spec(name, cap))
    internal = [
        ("run_python_code", "code"),
        ("verify_code_result", "verify"),
        ("apply_workspace_patch", "code"),
        ("verify_workspace", "verify"),
        ("ws_file_read", "file"),
        ("ws_file_write", "file"),
        ("ws_file_list", "file"),
    ]
    for name, cap in internal:
        registry.register(_spec(name, cap, visibility="internal"))
    # FILE_OPS_PROFILE 引用的几个 model-facing 文件工具
    for fname in ("file_read_segment", "file_write", "file_append",
                  "file_list_directory"):
        registry.register(_spec(fname, "file"))
    return registry


def _resolve_tool_names(registry: ToolRegistry, profile) -> set[str]:
    """按 profile 的 tool_names + capabilities + exclude_tool_names 解析出
    一组实际暴露的工具名。"""
    specs = registry.get_tools_for_profile(
        profile, include_internal=profile.include_internal,
    )
    return {spec.name for spec in specs}


class TestProfileExclusivity:
    def test_chat_extended_has_research_no_delegate(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, CHAT_EXTENDED_PROFILE)
        assert "research" in names
        assert "browse" in names
        assert "delegate_to_researcher" not in names
        assert "delegate_to_coder" not in names

    def test_task_execution_has_delegate_no_research(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, TASK_EXECUTION_PROFILE)
        assert "delegate_to_researcher" in names
        assert "delegate_to_coder" in names
        assert "research" not in names, (
            "task_execution 应通过 delegate_to_researcher 走 Agent Team，"
            "不应让主脑直接调 research"
        )
        assert "browse" not in names

    def test_chat_minimal_has_neither(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, CHAT_MINIMAL_PROFILE)
        for n in _RESEARCH_NAMES | _DELEGATE_NAMES:
            assert n not in names, f"chat_minimal 不应暴露 {n}"

    def test_no_profile_exposes_research_and_delegate_simultaneously(self):
        """全部 profile 扫一遍，断言不变量：不能同时持有两类工具。"""
        registry = _make_full_registry()
        violations: list[str] = []
        for pname, profile in _PROFILES.items():
            names = _resolve_tool_names(registry, profile)
            has_raw = bool(names & _RESEARCH_NAMES)
            has_delegate = bool(names & _DELEGATE_NAMES)
            if has_raw and has_delegate:
                violations.append(
                    f"{pname}: raw={names & _RESEARCH_NAMES} "
                    f"delegate={names & _DELEGATE_NAMES}"
                )
        assert not violations, "下列 profile 同时暴露 raw + delegate：\n" + "\n".join(violations)


class TestExcludeMechanism:
    """exclude_tool_names 字段本身工作正常。"""

    def test_exclude_filters_capability_path(self):
        from src.core.runtime_profiles import RuntimeProfile

        registry = _make_full_registry()
        profile = RuntimeProfile(
            name="test_exclude_via_caps",
            capabilities=frozenset({"web", "agent"}),
            exclude_tool_names=frozenset({"research"}),
        )
        names = _resolve_tool_names(registry, profile)
        assert "research" not in names
        # 同 capability 的 sports 仍在
        assert "get_sports_score" in names
        # delegate 仍在
        assert "delegate_to_researcher" in names

    def test_exclude_filters_tool_names_path(self):
        from src.core.runtime_profiles import RuntimeProfile

        registry = _make_full_registry()
        profile = RuntimeProfile(
            name="test_exclude_via_whitelist",
            capabilities=frozenset(),
            tool_names=frozenset({"research", "browse",
                                  "delegate_to_researcher"}),
            exclude_tool_names=frozenset({"browse"}),
        )
        names = _resolve_tool_names(registry, profile)
        assert "research" in names
        assert "browse" not in names
        assert "delegate_to_researcher" in names


class TestInnerTickProfile:
    """inner_tick is the autonomous self-initiated thinking surface.

    Companion-aligned: must include time / memory / commitments / focus /
    reminders / lightweight research+browse / proactive messaging / run_skill.
    Must exclude shell / arbitrary file writes / Playwright browser_* /
    agent delegation / identity mutations / create_skill.
    """

    def test_resolvable_by_name(self):
        assert get_runtime_profile("inner_tick") is INNER_TICK_PROFILE

    def test_includes_companion_surface(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        # time
        assert "get_current_datetime" in names
        # proactive messaging
        assert "send_message" in names
        # research/browse (lightweight — not delegated)
        assert "research" in names
        assert "browse" in names
        # reminders
        assert "set_reminder" in names
        assert "view_reminders" in names
        assert "cancel_reminder" in names
        # commitments
        assert "commit_promise" in names
        assert "fulfill_promise" in names
        assert "abandon_promise" in names
        # focus
        assert "close_focus" in names
        assert "recall_focus" in names
        # memory continuity
        assert "recall" in names
        assert "write_note" in names
        assert "read_note" in names
        assert "list_notes" in names
        assert "search_notes" in names
        # corrections
        assert "add_correction" in names
        # run_skill (gated for autonomous execution in commit 3)
        assert "run_skill" in names

    def test_excludes_create_skill(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        assert "create_skill" not in names, (
            "inner_tick must not author skills autonomously"
        )

    def test_excludes_shell_and_file_writes(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        for forbidden in ("execute_shell", "read_file", "write_file"):
            assert forbidden not in names, (
                f"inner_tick must not expose {forbidden}"
            )

    def test_excludes_browser_automation(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        for forbidden in ("browser_open", "browser_click"):
            assert forbidden not in names, (
                f"inner_tick must not expose {forbidden}"
            )

    def test_excludes_agent_delegation(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        assert "delegate_to_researcher" not in names
        assert "delegate_to_coder" not in names

    def test_excludes_identity_mutations(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        for forbidden in ("read_soul", "edit_soul"):
            assert forbidden not in names, (
                f"inner_tick must not expose {forbidden}"
            )

    def test_shell_policy_disabled(self):
        assert INNER_TICK_PROFILE.shell_policy_enabled is False
