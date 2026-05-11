"""STANDARD_PROFILE main-surface <10 tool invariant (blueprint §15.3 #6 / §15.2 I-4).

Plus structural checks: no raw browser_*, credential_*, or per-kind delegate
tools on the main surface; delegate_to_agent + read_state + update_state +
read_fact are present.
"""
from __future__ import annotations


def test_standard_profile_size_under_10():
    from src.core.runtime_profiles import STANDARD_PROFILE

    size = len(STANDARD_PROFILE.tool_names)
    assert size < 10, (
        f"STANDARD_PROFILE has {size} tools; blueprint §15.3 #6 requires <10. "
        f"Current set: {sorted(STANDARD_PROFILE.tool_names)}"
    )


def test_no_raw_browser_tools_on_main_surface():
    from src.core.runtime_profiles import STANDARD_PROFILE

    forbidden = {
        "browser_open",
        "browser_click",
        "browser_navigate",
        "browser_type",
        "browser_get_text",
        "browser_login",
        "browse",  # legacy verbose-browse alias
    }
    leaked = STANDARD_PROFILE.tool_names & forbidden
    assert leaked == set(), (
        f"STANDARD_PROFILE leaks raw browser tools: {leaked} — they must "
        f"be reached via delegate_to_agent(agent_name='resident_operator') "
        f"or live in lower-trust profiles only (blueprint §15.2 I-4)."
    )


def test_no_raw_credential_tools_on_main_surface():
    from src.core.runtime_profiles import STANDARD_PROFILE

    forbidden = {"credential_use", "credential_get", "credential_create"}
    leaked = STANDARD_PROFILE.tool_names & forbidden
    assert leaked == set(), (
        f"STANDARD_PROFILE leaks raw credential tools: {leaked}"
    )


def test_no_per_kind_delegate_tools():
    from src.core.runtime_profiles import STANDARD_PROFILE

    forbidden = {
        "delegate_to_researcher",
        "delegate_to_coder",
        "delegate_to_resident_browser",
        "delegate_to_resident_operator",
        "delegate_to_resident_agent",
    }
    leaked = STANDARD_PROFILE.tool_names & forbidden
    assert leaked == set(), (
        f"STANDARD_PROFILE has per-kind delegate tools: {leaked}. "
        f"Only the unified delegate_to_agent(agent_name=...) belongs on "
        f"the main surface (blueprint §11.2)."
    )


def test_delegate_to_agent_is_present():
    from src.core.runtime_profiles import STANDARD_PROFILE

    assert "delegate_to_agent" in STANDARD_PROFILE.tool_names


def test_state_facades_are_present():
    from src.core.runtime_profiles import STANDARD_PROFILE

    for required in ("read_state", "update_state", "read_fact"):
        assert required in STANDARD_PROFILE.tool_names, (
            f"STANDARD_PROFILE missing required façade tool {required!r} "
            f"(blueprint §11.1)"
        )


def test_send_message_not_on_main_surface():
    """send_message is proactive-only — must NOT leak to STANDARD_PROFILE."""
    from src.core.runtime_profiles import STANDARD_PROFILE

    assert "send_message" not in STANDARD_PROFILE.tool_names


def test_legacy_granular_tools_dropped_from_standard():
    """The dropped tools must NOT be in STANDARD_PROFILE — but they still
    exist as registered functions and remain in other profiles."""
    from src.core.runtime_profiles import STANDARD_PROFILE

    dropped = {
        "set_reminder", "view_reminders", "cancel_reminder",
        "commit_promise", "fulfill_promise", "abandon_promise",
        "add_correction",
        "close_focus", "recall_focus",
        "read_note", "list_notes", "search_notes", "write_note",
        "list_capabilities", "search_capability", "view_capability",
        "load_capability", "run_capability",
        "list_agents", "convert_timezone",
        "delegate_to_researcher", "delegate_to_coder",
    }
    leaked = STANDARD_PROFILE.tool_names & dropped
    assert leaked == set(), (
        f"Legacy granular tools must be dropped from STANDARD_PROFILE: {leaked}"
    )


def test_legacy_tools_still_on_inner_tick_profile():
    """Verify the dropped tools haven't been globally removed — they
    remain available where needed (autonomous ticks, local execution)."""
    from src.core.runtime_profiles import INNER_TICK_PROFILE

    # Reminders should still be on inner_tick for autonomous follow-up
    for kept in ("set_reminder", "commit_promise", "close_focus"):
        assert kept in INNER_TICK_PROFILE.tool_names, (
            f"{kept!r} dropped from INNER_TICK_PROFILE — autonomous flows broken"
        )
