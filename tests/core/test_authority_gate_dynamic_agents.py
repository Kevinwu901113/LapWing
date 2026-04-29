"""Dynamic agent tools must be registered in OPERATION_AUTH with OWNER
level, otherwise the auth gate rejects them before the executor runs.
"""

from src.core.authority_gate import OPERATION_AUTH, AuthLevel


def test_dynamic_agent_tools_have_owner_authority():
    for tool in (
        "delegate_to_agent",
        "create_agent",
        "destroy_agent",
        "save_agent",
    ):
        assert tool in OPERATION_AUTH, f"{tool} missing from OPERATION_AUTH"
        assert OPERATION_AUTH[tool] == AuthLevel.OWNER, (
            f"{tool} must require OWNER auth"
        )


def test_named_delegate_tools_have_owner_authority():
    """The two outward seams Lapwing actually uses."""
    for tool in ("delegate_to_researcher", "delegate_to_coder"):
        assert tool in OPERATION_AUTH
        assert OPERATION_AUTH[tool] == AuthLevel.OWNER
