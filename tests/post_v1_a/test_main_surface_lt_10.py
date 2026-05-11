"""V-A1: main surface stays under 10 and exposes only the unified delegate.

Post-v1 A §5 V-A1 acceptance test. Mirrors the V-A1 example from the spec.
"""
from __future__ import annotations


def test_standard_profile_tool_count():
    from src.core.runtime_profiles import STANDARD_PROFILE
    assert len(STANDARD_PROFILE.tool_names) < 10, (
        f"STANDARD_PROFILE has {len(STANDARD_PROFILE.tool_names)} tools "
        f"(>= 10): {sorted(STANDARD_PROFILE.tool_names)}"
    )


def test_no_legacy_delegate_on_main_surface():
    from src.core.runtime_profiles import STANDARD_PROFILE
    for forbidden in (
        "delegate_to_researcher",
        "delegate_to_coder",
        "delegate_to_resident_operator",
    ):
        assert forbidden not in STANDARD_PROFILE.tool_names, (
            f"{forbidden!r} must not be on the cognitive main surface; "
            f"cognition reaches sub-agents via delegate_to_agent only."
        )
    assert "delegate_to_agent" in STANDARD_PROFILE.tool_names
