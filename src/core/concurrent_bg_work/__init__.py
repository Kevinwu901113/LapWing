"""Concurrent background work subsystem.

This package is intentionally feature-flagged at the wiring boundary. The
types and pure helpers are import-safe when every runtime flag is disabled.
"""

from src.core.concurrent_bg_work.types import (
    AgentEvent,
    AgentEventType,
    AgentNeedsInputPayload,
    AgentRuntimeCheckpoint,
    AgentTaskRecord,
    AgentTaskSnapshot,
    CancellationScope,
    NotifyPolicy,
    SalienceLevel,
    SideEffectClass,
    TaskStatus,
)

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "AgentNeedsInputPayload",
    "AgentRuntimeCheckpoint",
    "AgentTaskRecord",
    "AgentTaskSnapshot",
    "CancellationScope",
    "NotifyPolicy",
    "SalienceLevel",
    "SideEffectClass",
    "TaskStatus",
]
