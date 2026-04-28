"""T-12 / Task 13: dynamic agent tools must be registered in OPERATION_AUTH
with OWNER level, otherwise the auth gate rejects them before the executor
runs.
"""

from src.core.authority_gate import OPERATION_AUTH, AuthLevel


def test_new_dynamic_agent_tools_have_owner_authority():
    for tool in (
        "delegate_to_agent",
        "list_agents",
        "create_agent",
        "destroy_agent",
        "save_agent",
    ):
        assert tool in OPERATION_AUTH, f"{tool} missing from OPERATION_AUTH"
        assert OPERATION_AUTH[tool] == AuthLevel.OWNER, (
            f"{tool} must require OWNER auth"
        )


def test_legacy_delegate_shims_still_have_owner_authority():
    # Legacy tools remain registered for shim compatibility.
    for tool in ("delegate_to_researcher", "delegate_to_coder"):
        assert tool in OPERATION_AUTH
        assert OPERATION_AUTH[tool] == AuthLevel.OWNER
