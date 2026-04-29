"""Tool boundary invariants — agents-as-tools refactor (Step 1).

These tests encode the target architecture from refactor blueprint
2026-04-29 (agents-as-tools / manager pattern):

  Lapwing's tool surface is fixed self-capabilities only. Anything that
  reads or changes the external world (web, shell, files) goes through
  delegate_to_researcher / delegate_to_coder — the only out-going seams.

CRITICAL: All boundary checks use ToolRegistry.get_tools_for_profile()
to resolve the *actual* tool set (capabilities + tool_names + exclude),
not just profile.tool_names. capability-based pulls otherwise leak
silently.
"""

from __future__ import annotations

import pytest

from src.core.runtime_profiles import (
    AGENT_CODER_PROFILE,
    AGENT_RESEARCHER_PROFILE,
    STANDARD_PROFILE,
    ZERO_TOOLS_PROFILE,
    CHAT_SHELL_PROFILE,
    COMPOSE_PROACTIVE_PROFILE,
    INNER_TICK_PROFILE,
    LOCAL_EXECUTION_PROFILE,
    _PROFILES,
)
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec


# ── Tool category labels (source of truth for boundary checks) ────────

SELF_CAPABILITY_TOOLS = frozenset({
    # memory
    "recall", "write_note", "read_note", "list_notes", "search_notes",
    # time
    "get_current_datetime", "convert_timezone", "get_time",
    # reminders
    "set_reminder", "view_reminders", "cancel_reminder",
    # promises
    "commit_promise", "fulfill_promise", "abandon_promise",
    # corrections
    "add_correction",
    # focus
    "close_focus", "recall_focus",
    # delegation (the only outward seams)
    "delegate_to_researcher", "delegate_to_coder",
    # skills
    "run_skill",
    # planning
    "plan_task", "update_plan",
})

PROACTIVE_ONLY_TOOLS = frozenset({
    "send_message",
    "send_image",
    "view_image",
})

EXTERNAL_RETRIEVAL_TOOLS = frozenset({
    "research", "browse", "get_sports_score",
})

EXTERNAL_EXECUTION_TOOLS = frozenset({
    "execute_shell", "read_file", "write_file",
})


# ── Helpers ───────────────────────────────────────────────────────────

async def _noop_executor(req: ToolExecutionRequest, ctx) -> ToolExecutionResult:
    return ToolExecutionResult(success=True, payload={})


def _make_full_registry() -> ToolRegistry:
    """Build a registry with every tool name referenced anywhere in
    profiles, with the right capability tags so capability-based pulls
    work like production.
    """
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

    # External retrieval — only Researcher should see these
    registry.register(_spec("research", "web"))
    registry.register(_spec("browse", "web"))
    registry.register(_spec("get_sports_score", "web"))

    # Delegate seams (the only outgoing edges from Lapwing)
    registry.register(_spec("delegate_to_researcher", "agent"))
    registry.register(_spec("delegate_to_coder", "agent"))
    # Generic delegate + dynamic agent tools (kept for power profiles)
    registry.register(_spec("delegate_to_agent", "agent"))
    registry.register(_spec("create_agent", "agent"))
    registry.register(_spec("destroy_agent", "agent"))
    registry.register(_spec("save_agent", "agent"))

    # Self-capability tools
    extras = [
        ("get_current_datetime", "general"),
        ("convert_timezone", "general"),
        ("get_time", "general"),
        ("send_message", "general"),
        ("send_image", "general"),
        ("view_image", "general"),
        ("add_correction", "general"),
        ("close_focus", "general"),
        ("recall_focus", "general"),
        ("plan_task", "general"),
        ("update_plan", "general"),
        ("set_reminder", "schedule"),
        ("view_reminders", "schedule"),
        ("cancel_reminder", "schedule"),
        ("commit_promise", "commitment"),
        ("fulfill_promise", "commitment"),
        ("abandon_promise", "commitment"),
        ("recall", "memory"),
        ("write_note", "memory"),
        ("read_note", "memory"),
        ("list_notes", "memory"),
        ("search_notes", "memory"),
        ("create_skill", "skill"),
        ("run_skill", "skill"),
        # browser / shell / files — only TASK_EXECUTION should see these
        ("browser_open", "browser"),
        ("browser_click", "browser"),
        ("execute_shell", "shell"),
        ("read_file", "shell"),
        ("write_file", "shell"),
        ("read_soul", "identity"),
        ("edit_soul", "identity"),
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

    for fname in ("file_read_segment", "file_write", "file_append",
                  "file_list_directory"):
        registry.register(_spec(fname, "file"))

    return registry


def _resolved_tool_names(profile) -> set[str]:
    registry = _make_full_registry()
    return {
        spec.name
        for spec in registry.get_tools_for_profile(
            profile, include_internal=profile.include_internal,
        )
    }


# ── Stable invariants (should always hold) ────────────────────────────

class TestStableBoundaries:
    """Invariants that hold throughout the refactor."""

    def test_agent_researcher_has_external_retrieval(self):
        """Researcher's job is external retrieval — it must have research+browse."""
        names = _resolved_tool_names(AGENT_RESEARCHER_PROFILE)
        assert "research" in names
        assert "browse" in names

    def test_agent_researcher_has_no_delegate(self):
        """Researcher is a leaf. It does not delegate further."""
        names = _resolved_tool_names(AGENT_RESEARCHER_PROFILE)
        assert "delegate_to_researcher" not in names
        assert "delegate_to_coder" not in names
        assert "delegate_to_agent" not in names

    def test_send_message_not_in_user_chat_profiles(self):
        """send_message is proactive-only — never in user chat surface."""
        for profile in (ZERO_TOOLS_PROFILE, STANDARD_PROFILE,
                        CHAT_SHELL_PROFILE, LOCAL_EXECUTION_PROFILE):
            names = _resolved_tool_names(profile)
            assert "send_message" not in names, (
                f"{profile.name} must not expose send_message"
            )

    def test_send_message_in_proactive_profiles(self):
        """Proactive paths need send_message to talk to the user."""
        for profile in (INNER_TICK_PROFILE, COMPOSE_PROACTIVE_PROFILE):
            names = _resolved_tool_names(profile)
            assert "send_message" in names, (
                f"{profile.name} must expose send_message (proactive path)"
            )


# ── Target-state invariants (xfail until each commit lands) ───────────

# Per blueprint section 9: these xfail markers come off in Commit 7,
# at which point all tests must pass green.

class TestStandardProfileTarget:
    """The 'standard' profile is the post-refactor name for the chat
    surface. It contains only self-capability tools — every external
    seam goes through delegate_to_*.
    """

    def test_standard_profile_exists(self):
        from src.core.runtime_profiles import STANDARD_PROFILE  # noqa: F401
        assert "standard" in _PROFILES

    def test_standard_profile_has_only_self_capabilities(self):
        from src.core.runtime_profiles import STANDARD_PROFILE
        names = _resolved_tool_names(STANDARD_PROFILE)
        leaks = (
            (names & EXTERNAL_RETRIEVAL_TOOLS)
            | (names & EXTERNAL_EXECUTION_TOOLS)
            | (names & PROACTIVE_ONLY_TOOLS)
        )
        assert not leaks, f"standard exposes non-self-capability tools: {leaks}"

    def test_standard_profile_has_delegate_seams(self):
        from src.core.runtime_profiles import STANDARD_PROFILE
        names = _resolved_tool_names(STANDARD_PROFILE)
        assert "delegate_to_researcher" in names
        assert "delegate_to_coder" in names


class TestZeroToolsProfileTarget:
    def test_zero_tools_profile_has_no_tools(self):
        from src.core.runtime_profiles import ZERO_TOOLS_PROFILE
        names = _resolved_tool_names(ZERO_TOOLS_PROFILE)
        assert names == set()


class TestRetrievalToolsConfinedToResearcher:
    """External retrieval tools must only reach the LLM through the
    Researcher. Lapwing-facing profiles never see them.
    """

    def test_get_sports_score_in_researcher_profile(self):
        names = _resolved_tool_names(AGENT_RESEARCHER_PROFILE)
        assert "get_sports_score" in names

    def test_inner_tick_does_not_have_raw_research(self):
        names = _resolved_tool_names(INNER_TICK_PROFILE)
        assert "research" not in names
        assert "browse" not in names

    def test_no_lapwing_profile_exposes_external_retrieval(self):
        """The set of profiles that face Lapwing as the orchestrator
        (i.e. not Researcher / Coder / internal coder profiles) must
        never expose raw external retrieval tools.
        """
        from src.core.runtime_profiles import STANDARD_PROFILE, ZERO_TOOLS_PROFILE
        lapwing_facing = [
            ZERO_TOOLS_PROFILE,
            STANDARD_PROFILE,
            INNER_TICK_PROFILE,
            COMPOSE_PROACTIVE_PROFILE,
            LOCAL_EXECUTION_PROFILE,
        ]
        for profile in lapwing_facing:
            names = _resolved_tool_names(profile)
            leaks = names & EXTERNAL_RETRIEVAL_TOOLS
            assert not leaks, f"{profile.name} leaks external retrieval: {leaks}"


class TestDelegateExclusivity:
    """Per blueprint section 10: a profile must never expose both raw
    retrieval tools and delegate_to_* seams — that splits the model's
    decision and makes the Agent Team pointless.
    """

    def test_no_profile_has_raw_retrieval_and_delegate(self):
        violations: list[str] = []
        for pname, profile in _PROFILES.items():
            if pname == "agent_researcher":
                continue  # exempt — Researcher's own surface
            names = _resolved_tool_names(profile)
            has_raw = bool(names & EXTERNAL_RETRIEVAL_TOOLS)
            has_delegate = bool(names & {
                "delegate_to_researcher", "delegate_to_coder",
            })
            if has_raw and has_delegate:
                violations.append(
                    f"{pname}: raw={names & EXTERNAL_RETRIEVAL_TOOLS} "
                    f"delegate={names & {'delegate_to_researcher', 'delegate_to_coder'}}"
                )
        assert not violations, (
            "Profiles must not expose both raw retrieval and delegate:\n"
            + "\n".join(violations)
        )

    def test_local_execution_no_external_retrieval_tools(self):
        """local_execution is a temporary legacy escape hatch
        only) — it still has shell/file but must not expose
        raw retrieval tools, those go through Researcher.

        Step 2 (post-blueprint) will migrate shell/file to
        Coder and the escape hatch goes away entirely.
        """
        names = _resolved_tool_names(LOCAL_EXECUTION_PROFILE)
        for tool in EXTERNAL_RETRIEVAL_TOOLS:
            assert tool not in names, (
                f"local_execution must not expose {tool} — goes via Researcher"
            )
