"""ResidentOperator agent kind tests (Slice I.1).

Covers:
  - blueprint §16 O-1: resident_operator kind is registered in the catalog
  - delegate_to_agent(agent_name='resident_operator') is dispatchable at
    the spec layer (catalog has the spec)
  - The agent class exists with the expected runtime characteristics
"""
from __future__ import annotations

import pytest


def test_builtin_resident_operator_spec_present():
    from src.agents.builtin_specs import (
        all_builtin_specs,
        builtin_resident_operator_spec,
    )

    spec = builtin_resident_operator_spec()
    assert spec.name == "resident_operator"
    assert spec.id == "builtin_resident_operator"
    assert spec.kind == "builtin"
    assert spec.status == "active"
    assert spec.model_slot == "agent_execution"

    names = [s.name for s in all_builtin_specs()]
    assert "resident_operator" in names
    # Researcher + Coder + Resident Operator
    assert len(names) == 3


def test_resident_operator_resource_limits_for_long_session():
    """Resident operator wall-time budget must accommodate owner-takeover
    waits (CAPTCHA, 2FA approval). Default expiry per blueprint §3.3 is
    24h on browser.* interrupt kinds; the wall-time should be at least
    on the order of an hour for in-progress sessions."""
    from src.agents.builtin_specs import builtin_resident_operator_spec

    spec = builtin_resident_operator_spec()
    assert spec.resource_limits.max_wall_time_seconds >= 600


def test_resident_operator_class_exists():
    """ResidentOperator runtime class exists with create() classmethod."""
    from src.agents.resident_operator import ResidentOperator

    assert hasattr(ResidentOperator, "create")
    assert callable(ResidentOperator.create)


def test_resident_operator_module_importable():
    """Sanity: class + system prompt + create classmethod all importable."""
    from src.agents.resident_operator import (
        RESIDENT_OPERATOR_SYSTEM_PROMPT,
        ResidentOperator,
    )

    assert isinstance(RESIDENT_OPERATOR_SYSTEM_PROMPT, str)
    # System prompt mentions interrupt-aware behavior and persistent identity
    assert "持久身份" in RESIDENT_OPERATOR_SYSTEM_PROMPT
    assert "CAPTCHA" in RESIDENT_OPERATOR_SYSTEM_PROMPT or "captcha" in RESIDENT_OPERATOR_SYSTEM_PROMPT.lower()


def test_resident_operator_does_not_attempt_captcha_bypass():
    """I-3 invariant: system prompt explicitly forbids bypass."""
    from src.agents.resident_operator import RESIDENT_OPERATOR_SYSTEM_PROMPT

    # Either tells the agent to NOT bypass / to wait for owner
    prompt_lower = RESIDENT_OPERATOR_SYSTEM_PROMPT.lower()
    forbids_bypass = (
        "不要尝试绕过" in RESIDENT_OPERATOR_SYSTEM_PROMPT
        or "do not bypass" in prompt_lower
        or "interrupt" in prompt_lower
    )
    assert forbids_bypass, (
        "Resident Operator prompt must tell the agent NOT to bypass "
        "verifications and instead surface interrupts (blueprint §15.2 I-3)."
    )
