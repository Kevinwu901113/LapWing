from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InvariantSpec:
    invariant_id: str
    phase: str
    summary: str


INVARIANT_MATRIX: tuple[InvariantSpec, ...] = (
    InvariantSpec("T-INV-1", "P1", "User input reaches EventQueue during busy state."),
    InvariantSpec("T-INV-2", "P1", "Busy is context, not an ingress gate."),
    InvariantSpec("T-INV-3", "P2.5", "Only one cognitive turn is in flight globally."),
    InvariantSpec("T-INV-4", "P2c", "Allowed researcher tasks can run concurrently."),
    InvariantSpec("T-INV-5", "P2.5", "SpeakingArbiter serializes per-chat sends."),
    InvariantSpec("T-INV-6", "P3", "Sub-agent user-facing send paths are forbidden."),
    InvariantSpec("T-INV-7", "P2d", "AGENT_NEEDS_INPUT checkpoints and resumes."),
    InvariantSpec("T-INV-8", "P2.5", "Agent result turns may produce no response."),
    InvariantSpec("T-INV-9", "P2.5", "Empty response and empty operations commit cleanly."),
    InvariantSpec("T-INV-10", "P4", "OperatorControlEvent is auditable."),
    InvariantSpec("T-INV-11", "P4", "Emergency control is disabled by default."),
    InvariantSpec("T-INV-12", "P2c", "Concurrent same-spec tasks get isolated workspaces."),
    InvariantSpec("T-INV-13", "P2a", "Startup recovery marks active tasks failed_orphan."),
    InvariantSpec("T-INV-14", "P2b", "StateView task projection does not over-report."),
    InvariantSpec("T-INV-15", "P3", "Desktop tool-call progress does not force cognition."),
    InvariantSpec("T-INV-16", "P3", "Sub-agents cannot use output channels."),
    InvariantSpec("T-INV-17", "P2.5", "Transactional fold handles conflicting operations."),
    InvariantSpec("T-INV-18", "P1/P2a", "Ingress and task idempotency are enforced."),
    InvariantSpec("T-INV-19", "P2d", "Sub-agent spawning obeys AgentSpec constraints."),
    InvariantSpec("T-INV-20", "P3", "silent notify policy has safety overrides."),
    InvariantSpec("T-INV-21", "P2d", "WAITING_INPUT does not consume active quota."),
    InvariantSpec("T-INV-22", "P2.5", "Cognitive loop is global singleton."),
    InvariantSpec("T-INV-23", "P2c", "Legacy wait wrappers are forbidden in runtime."),
    InvariantSpec("T-INV-24", "P2d", "Needs-input destroys long-lived runtime instance."),
    InvariantSpec("T-INV-25", "P2b", "WAITING_RESOURCE backlog quota is independent."),
    InvariantSpec("T-INV-26", "P2d", "Ambiguous semantic cancel cancels nothing."),
    InvariantSpec("T-INV-27", "P2.5", "High-salience events flush within 250ms."),
)


def invariant_ids() -> set[str]:
    return {item.invariant_id for item in INVARIANT_MATRIX}
