"""RuntimeProfile 工具暴露互斥测试（修复 A）。

确保同一个 profile 不会同时把 raw 调研工具（research/browse）和委托工具
（delegate_to_researcher/delegate_to_coder）暴露给同一个 LLM ——否则
主脑会在两条路径间纠结，Agent Team 形同虚设。
"""

from __future__ import annotations

from src.core.runtime_profiles import (
    AGENT_ADMIN_OPERATOR_PROFILE,
    AGENT_CODER_PROFILE,
    AGENT_RESEARCHER_PROFILE,
    BROWSER_OPERATOR_PROFILE,
    COMPOSE_PROACTIVE_PROFILE,
    IDENTITY_OPERATOR_PROFILE,
    STANDARD_PROFILE,
    ZERO_TOOLS_PROFILE,
    CHAT_SHELL_PROFILE,
    CODER_SNIPPET_PROFILE,
    CODER_WORKSPACE_PROFILE,
    FILE_OPS_PROFILE,
    INNER_TICK_PROFILE,
    LOCAL_EXECUTION_PROFILE,
    SKILL_OPERATOR_PROFILE,
    TASK_EXECUTION_PROFILE,
    _PROFILES,
    get_runtime_profile,
)
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec


_RESEARCH_NAMES = {"research", "browse"}
_DELEGATE_NAMES = {"delegate_to_researcher", "delegate_to_coder", "delegate_to_agent"}


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
    # delegate 系 (legacy shims + new dynamic agent tools — Blueprint §7)
    registry.register(_spec("delegate_to_researcher", "agent_delegate"))
    registry.register(_spec("delegate_to_coder", "agent_delegate"))
    registry.register(_spec("delegate_to_agent", "agent_admin"))
    registry.register(_spec("create_agent", "agent_admin"))
    registry.register(_spec("destroy_agent", "agent_admin"))
    registry.register(_spec("save_agent", "agent_admin"))
    registry.register(_spec("list_agents", "agent_admin"))
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
        ("run_skill", "skill"),
        ("create_skill", "skill"),
        ("edit_skill", "skill"),
        ("list_skills", "skill"),
        ("promote_skill", "skill"),
        ("delete_skill", "skill"),
        ("search_skill", "skill"),
        ("install_skill", "skill"),
        # browser_* — needed for INNER_TICK exclusion check
        ("browser_open", "browser"),
        ("browser_click", "browser"),
        ("browser_type", "browser"),
        ("browser_select", "browser"),
        ("browser_scroll", "browser"),
        ("browser_screenshot", "browser"),
        ("browser_get_text", "browser"),
        ("browser_back", "browser"),
        ("browser_tabs", "browser"),
        ("browser_switch_tab", "browser"),
        ("browser_close_tab", "browser"),
        ("browser_wait", "browser"),
        ("browser_login", "browser"),
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
    def test_standard_profile_uses_unified_delegate(self):
        """v1 blueprint §11.1 main-surface contract: STANDARD reaches
        sub-agents through the unified delegate_to_agent. The pre-v1
        delegate_to_researcher / delegate_to_coder shims remain registered
        globally for non-cognitive callers but are NOT on the main surface
        — splitting the seam in two confused both the model and the test.
        """
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, STANDARD_PROFILE)
        assert "delegate_to_agent" in names
        # Legacy shims are off the main surface (Post-v1 A §2.4).
        assert "delegate_to_researcher" not in names
        assert "delegate_to_coder" not in names
        # Raw research / dynamic-agent management not on the chat tier.
        assert "research" not in names
        assert "browse" not in names
        for forbidden in ("create_agent", "destroy_agent", "save_agent"):
            assert forbidden not in names, f"standard must not expose {forbidden}"

    def test_standard_profile_outward_seam_is_delegate_to_agent(self):
        """Standard profile 唯一外向 seam: delegate_to_agent。"""
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, STANDARD_PROFILE)
        assert "research" not in names
        assert "browse" not in names
        assert "browser_open" not in names
        assert "execute_shell" not in names
        assert "delegate_to_agent" in names

    def test_local_execution_does_not_expose_dynamic_agent_admin_tools(self):
        """Phase 6B: local_execution must not expose agent_admin tools."""
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, LOCAL_EXECUTION_PROFILE)
        for forbidden in ("delegate_to_agent", "create_agent",
                          "destroy_agent", "save_agent"):
            assert forbidden not in names, f"local_execution must not expose {forbidden}"
        assert "research" not in names
        assert "browse" not in names

    def test_local_execution_profile_is_frozen(self):
        """LOCAL_EXECUTION_PROFILE is a temporary legacy escape hatch.
        It must only shrink, never grow.
        """
        assert LOCAL_EXECUTION_PROFILE.name == "local_execution"
        assert LOCAL_EXECUTION_PROFILE.capabilities == frozenset()
        assert LOCAL_EXECUTION_PROFILE.tool_names == frozenset({
            "execute_shell",
            "read_file",
            "write_file",
            "file_read_segment",
            "file_write",
            "file_append",
            "file_list_directory",
            "run_skill",
            "list_agents",
        })
        assert "new_capability" not in LOCAL_EXECUTION_PROFILE.capabilities
        
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, LOCAL_EXECUTION_PROFILE)
        
        expected_names = {
            "execute_shell",
            "file_append",
            "file_list_directory",
            "file_read_segment",
            "file_write",
            "read_file",
            "run_skill",
            "write_file",
            "list_agents",
        }
        assert names == expected_names
        
        assert "research" not in names
        assert "browse" not in names
        assert "send_message" not in names
        assert "create_agent" not in names
        assert "destroy_agent" not in names
        assert "save_agent" not in names
        assert "delegate_to_agent" not in names
        assert "delegate_to_researcher" not in names
        assert "delegate_to_coder" not in names
        assert "create_skill" not in names
        assert "edit_skill" not in names
        assert "read_soul" not in names
        assert "edit_soul" not in names
        assert "browser_open" not in names
        assert "browser_click" not in names
        assert "browser_type" not in names

    def test_task_execution_alias_matches_local_execution(self):
        assert TASK_EXECUTION_PROFILE is LOCAL_EXECUTION_PROFILE
        assert get_runtime_profile("task_execution") is LOCAL_EXECUTION_PROFILE

    def test_agent_admin_operator_profile_exposes_agent_admin_tools_only(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, AGENT_ADMIN_OPERATOR_PROFILE)
        assert names == {
            "delegate_to_agent", "create_agent", "destroy_agent", "save_agent",
            "list_agents",
        }

    def test_identity_operator_profile_exposes_identity_tools_only(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, IDENTITY_OPERATOR_PROFILE)
        assert names == {"read_soul", "edit_soul"}

    def test_browser_operator_profile_exposes_browser_tools_only(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, BROWSER_OPERATOR_PROFILE)
        assert names == {
            "browser_open",
            "browser_click",
            "browser_type",
            "browser_select",
            "browser_scroll",
            "browser_screenshot",
            "browser_get_text",
            "browser_back",
            "browser_tabs",
            "browser_switch_tab",
            "browser_close_tab",
            "browser_wait",
            "browser_login",
        }

    def test_skill_operator_profile_exposes_skill_admin_tools_only(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, SKILL_OPERATOR_PROFILE)
        assert names == {
            "create_skill",
            "edit_skill",
            "list_skills",
            "promote_skill",
            "delete_skill",
            "search_skill",
            "install_skill",
        }
        assert "run_skill" not in names

    def test_non_operator_profiles_never_expose_agent_admin_tools(self):
        """v1: delegate_to_agent is now STANDARD's outward seam — not an
        admin tool — so it is intentionally absent from this forbidden set.
        Admin tools (create/destroy/save) remain operator-only.
        """
        registry = _make_full_registry()
        forbidden = {"create_agent", "destroy_agent", "save_agent"}
        profiles = (
            ZERO_TOOLS_PROFILE,
            STANDARD_PROFILE,
            CHAT_SHELL_PROFILE,
            INNER_TICK_PROFILE,
            LOCAL_EXECUTION_PROFILE,
        )
        for profile in profiles:
            names = _resolve_tool_names(registry, profile)
            leaks = names & forbidden
            assert not leaks, f"{profile.name} leaked agent_admin tools: {sorted(leaks)}"

    def test_non_operator_profiles_never_expose_identity_mutation_tools(self):
        registry = _make_full_registry()
        forbidden = {"read_soul", "edit_soul"}
        profiles = (
            ZERO_TOOLS_PROFILE,
            STANDARD_PROFILE,
            CHAT_SHELL_PROFILE,
            INNER_TICK_PROFILE,
            COMPOSE_PROACTIVE_PROFILE,
            LOCAL_EXECUTION_PROFILE,
        )
        for profile in profiles:
            names = _resolve_tool_names(registry, profile)
            leaks = names & forbidden
            assert not leaks, f"{profile.name} leaked identity tools: {sorted(leaks)}"

    def test_non_operator_profiles_never_expose_browser_automation_tools(self):
        registry = _make_full_registry()
        forbidden = {
            "browser_open", "browser_click", "browser_type", "browser_select",
            "browser_scroll", "browser_screenshot", "browser_get_text",
            "browser_back", "browser_tabs", "browser_switch_tab",
            "browser_close_tab", "browser_wait", "browser_login",
        }
        profiles = (
            STANDARD_PROFILE,
            INNER_TICK_PROFILE,
            COMPOSE_PROACTIVE_PROFILE,
            LOCAL_EXECUTION_PROFILE,
        )
        for profile in profiles:
            names = _resolve_tool_names(registry, profile)
            leaks = names & forbidden
            assert not leaks, f"{profile.name} leaked browser tools: {sorted(leaks)}"

    def test_non_operator_profiles_never_expose_skill_admin_tools(self):
        registry = _make_full_registry()
        forbidden = {
            "create_skill",
            "edit_skill",
            "list_skills",
            "promote_skill",
            "delete_skill",
            "search_skill",
            "install_skill",
        }
        profiles = (
            ZERO_TOOLS_PROFILE,
            STANDARD_PROFILE,
            CHAT_SHELL_PROFILE,
            INNER_TICK_PROFILE,
            COMPOSE_PROACTIVE_PROFILE,
            LOCAL_EXECUTION_PROFILE,
        )
        for profile in profiles:
            names = _resolve_tool_names(registry, profile)
            leaks = names & forbidden
            assert not leaks, f"{profile.name} leaked skill admin tools: {sorted(leaks)}"

    def test_chat_minimal_has_no_agent_tools(self):
        """chat_minimal (zero_tools alias) exposes nothing — pure-text
        replies don't need any tool access."""
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, ZERO_TOOLS_PROFILE)
        for n in _RESEARCH_NAMES | _DELEGATE_NAMES | {
            "create_agent", "destroy_agent", "save_agent",
        }:
            assert n not in names, f"chat_minimal 不应暴露 {n}"

    def test_no_profile_exposes_research_and_delegate_simultaneously(self):
        """Blueprint §10.2: raw research/browse mutually exclusive with
        delegate_to_*. Exception: AGENT_RESEARCHER_PROFILE keeps
        research/browse — that's the agent's own surface, not the
        brain's.
        """
        registry = _make_full_registry()
        violations: list[str] = []
        for pname, profile in _PROFILES.items():
            if pname == "agent_researcher":
                continue  # exempt — agent's own profile
            names = _resolve_tool_names(registry, profile)
            has_raw = bool(names & _RESEARCH_NAMES)
            has_delegate = bool(names & _DELEGATE_NAMES)
            if has_raw and has_delegate:
                violations.append(
                    f"{pname}: raw={names & _RESEARCH_NAMES} "
                    f"delegate={names & _DELEGATE_NAMES}"
                )
        assert not violations, "下列 profile 同时暴露 raw + delegate：\n" + "\n".join(violations)


class TestChatProfileSendMessageExclusion:
    """send_message 仅允许 proactive 场景（inner_tick / compose_proactive）使用。
    所有普通聊天/任务执行 profile 都必须不暴露它，否则模型会在 user turn 中途
    通过 tool call 发"侧门消息"。
    """

    def test_chat_minimal_does_not_expose_send_message(self):
        assert "send_message" not in ZERO_TOOLS_PROFILE.tool_names

    def test_chat_extended_does_not_expose_send_message(self):
        assert "send_message" not in STANDARD_PROFILE.tool_names

    def test_chat_shell_resolved_excludes_send_message(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, CHAT_SHELL_PROFILE)
        assert "send_message" not in names, (
            "chat_shell 通过 general capability 会拉入 send_message，"
            "必须经由 exclude_tool_names 排除"
        )

    def test_local_execution_resolved_excludes_send_message(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, LOCAL_EXECUTION_PROFILE)
        assert "send_message" not in names, (
            "local_execution 通过 general capability 会拉入 send_message，"
            "必须经由 exclude_tool_names 排除"
        )


class TestExcludeMechanism:
    """exclude_tool_names 字段本身工作正常。"""

    def test_exclude_filters_capability_path(self):
        from src.core.runtime_profiles import RuntimeProfile

        registry = _make_full_registry()
        profile = RuntimeProfile(
            name="test_exclude_via_caps",
            capabilities=frozenset({"web", "agent_delegate"}),
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


class TestCreateSkillExclusion:
    """create_skill must not be in conversational or autonomous profiles.

    Authoring a new skill is a deliberate, reviewed action — not something
    a chat reply or an autonomous tick should do. Skill authoring stays
    out of these surfaces; it can still happen via explicit operator
    workflows that use a different profile.
    """

    def test_chat_extended_excludes_create_skill(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, STANDARD_PROFILE)
        assert "create_skill" not in names, (
            "chat_extended must not expose create_skill — skill authoring "
            "is a deliberate, reviewed action"
        )
        # run_skill stays available — chat needs to be able to invoke
        # already-approved skills (gated by maturity in commit 3).
        assert "run_skill" in names

    def test_inner_tick_excludes_create_skill(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        assert "create_skill" not in names

    def test_no_chat_or_inner_profile_exposes_create_skill(self):
        registry = _make_full_registry()
        for profile in (ZERO_TOOLS_PROFILE, STANDARD_PROFILE, INNER_TICK_PROFILE):
            names = _resolve_tool_names(registry, profile)
            assert "create_skill" not in names, (
                f"{profile.name} must not expose create_skill"
            )


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
        # outward seam — research/browse moved to Researcher; the
        # autonomous tick reaches external info via delegate.
        assert "delegate_to_researcher" in names
        assert "research" not in names
        assert "browse" not in names
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
        # run_skill (gated for autonomous execution by skill maturity)
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

    def test_excludes_coder_delegation(self):
        """inner_tick is a thinking/messaging surface — it should not
        kick off code execution. delegate_to_researcher is allowed
        (autonomous lookups), delegate_to_coder is not.
        """
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        assert "delegate_to_coder" not in names
        # delegate_to_agent (generic) and dynamic agent tools also out
        assert "delegate_to_agent" not in names
        assert "create_agent" not in names

    def test_excludes_identity_mutations(self):
        registry = _make_full_registry()
        names = _resolve_tool_names(registry, INNER_TICK_PROFILE)
        for forbidden in ("read_soul", "edit_soul"):
            assert forbidden not in names, (
                f"inner_tick must not expose {forbidden}"
            )

    def test_shell_policy_disabled(self):
        assert INNER_TICK_PROFILE.shell_policy_enabled is False
