"""Verify dynamic-agent MutationType members are present (Blueprint §11.1)."""

from src.logging.state_mutation_log import MutationType


def test_dynamic_agent_mutation_types_present():
    assert MutationType.AGENT_CREATED.value == "agent.created"
    assert MutationType.AGENT_SAVED.value == "agent.saved"
    assert MutationType.AGENT_DESTROYED.value == "agent.destroyed"
    assert MutationType.AGENT_SPEC_UPDATED.value == "agent.spec_updated"
    assert MutationType.AGENT_BUDGET_EXHAUSTED.value == "agent.budget_exhausted"


def test_existing_agent_members_unchanged():
    # Sanity check that we didn't disturb the original Step 6 members.
    assert MutationType.AGENT_STARTED.value == "agent.task_started"
    assert MutationType.AGENT_COMPLETED.value == "agent.task_done"
    assert MutationType.AGENT_FAILED.value == "agent.task_failed"
    assert MutationType.AGENT_TOOL_CALL.value == "agent.tool_called"
    assert MutationType.TOOL_DENIED.value == "tool.denied"
