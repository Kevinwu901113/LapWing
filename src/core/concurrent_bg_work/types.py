from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Union


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TaskStatus(_StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_RESOURCE = "waiting_resource"
    WAITING_INPUT = "waiting_input"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SalienceLevel(_StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class NotifyPolicy(_StrEnum):
    AUTO = "auto"
    SILENT = "silent"


class SideEffectClass(_StrEnum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    EXTERNAL_ACTION = "external_action"


class CancellationScope(_StrEnum):
    CURRENT_FOREGROUND = "current_foreground"
    TASK_ID = "task_id"
    SEMANTIC_MATCH = "semantic_match"
    CHAT_BACKGROUND = "chat_background"
    ALL_OWNER_TASKS = "all_owner_tasks"


class AgentEventType(_StrEnum):
    AGENT_STARTED = "agent_started"
    AGENT_TOOL_CALL = "agent_tool_call"
    AGENT_PROGRESS_SUMMARY = "agent_progress_summary"
    AGENT_NEEDS_INPUT = "agent_needs_input"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    AGENT_BUDGET_EXHAUSTED = "agent_budget_exhausted"
    AGENT_CANCELLED = "agent_cancelled"


class InputRelationKind(_StrEnum):
    NEW_REQUEST = "new_request"
    AMENDMENT = "amendment"
    CORRECTION = "correction"
    ANSWER_TO_CLARIFICATION = "answer_to_clarification"
    CANCELLATION = "cancellation"
    SMALLTALK = "smalltalk"
    META_CONTROL = "meta_control"
    NEW_CONSTRAINT = "new_constraint"


@dataclass(slots=True)
class AgentTaskRecord:
    task_id: str
    chat_id: str
    owner_user_id: str
    parent_event_id: str
    parent_turn_id: str | None
    parent_task_id: str | None
    root_task_id: str
    spawned_by: Literal["lapwing", "agent", "system"]
    replaces_task_id: str | None
    spec_id: str
    spec_version: str | None
    instance_id: str
    objective: str
    user_visible_summary: str
    semantic_tags: list[str]
    expected_output: str | None
    status: TaskStatus
    status_reason: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    last_event_at: datetime | None
    workspace_path: str
    result_summary: str | None
    error_summary: str | None
    artifact_refs: list[str]
    last_progress_summary: str | None
    checkpoint_id: str | None
    checkpoint_question: str | None
    cancellation_requested: bool
    cancellation_reason: str | None
    notify_policy: NotifyPolicy
    salience: SalienceLevel
    priority: int
    idempotency_key: str


@dataclass(slots=True)
class AgentEvent:
    event_id: str
    task_id: str
    chat_id: str
    type: AgentEventType
    occurred_at: datetime
    summary_for_lapwing: str
    summary_for_owner: str | None
    raw_payload_ref: str | None
    salience: SalienceLevel | None
    payload: dict[str, Any]
    sequence_in_task: int


@dataclass(slots=True)
class AgentTaskSnapshot:
    task_id: str
    spec_id: str
    objective: str
    status: TaskStatus
    started_at: datetime | None
    elapsed_seconds: float | None
    last_progress_summary: str | None
    recent_events_summary: list[str]
    result_summary: str | None
    error_summary: str | None
    artifact_refs: list[str]
    salience: SalienceLevel
    is_blocked_by_input: bool
    pending_question: str | None


@dataclass(slots=True)
class AgentNeedsInputPayload:
    question_for_lapwing: str
    question_for_owner: str | None
    expected_answer_shape: str | None
    blocking: bool = True
    timeout_at: datetime | None = None


@dataclass(slots=True)
class AgentRuntimeCheckpoint:
    checkpoint_id: str
    task_id: str
    created_at: datetime
    conversation_state: dict[str, Any]
    scratchpad_summary: str
    pending_question: AgentNeedsInputPayload
    tool_context: dict[str, Any]
    workspace_snapshot_ref: str | None
    rounds_consumed: int


@dataclass(slots=True)
class InputRelationAnnotation:
    relation: InputRelationKind
    target_type: Literal["task", "turn", "message", "question", "chat"]
    target_id: str | None
    confidence: float
    evidence: str | None
    is_primary: bool = False


@dataclass(slots=True)
class StartAgentTaskOp:
    spec_id: str
    objective: str
    context: dict[str, Any] = field(default_factory=dict)
    expected_output: str | None = None
    notify_policy: NotifyPolicy = NotifyPolicy.AUTO
    salience: SalienceLevel = SalienceLevel.NORMAL
    priority: int = 0
    replaces_task_id: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True)
class CancelAgentTaskOp:
    scope: CancellationScope
    task_id: str | None = None
    semantic_query: str | None = None
    reason: str = "lapwing_decision"


@dataclass(slots=True)
class RespondToAgentInputOp:
    task_id: str
    answer: str | dict


@dataclass(slots=True)
class ReadAgentTaskOp:
    task_id: str


CognitiveOperation = Union[
    StartAgentTaskOp,
    CancelAgentTaskOp,
    RespondToAgentInputOp,
    ReadAgentTaskOp,
]


@dataclass(slots=True)
class CognitiveTurnDecision:
    turn_id: str
    snapshot_id: str
    input_relations: dict[str, list[InputRelationAnnotation]] = field(default_factory=dict)
    operations: list[CognitiveOperation] = field(default_factory=list)
    response: str | None = None
    reasoning_summary: str | None = None


@dataclass(slots=True)
class InterruptedTaskInfo:
    task_id: str
    spec_id: str
    objective: str
    previous_status: TaskStatus
    ran_for_seconds: float | None
    last_progress_summary: str | None
    recovered_status: Literal["failed_orphan"]
    recovered_at: datetime


@dataclass(slots=True)
class RecoveryNotice:
    interrupted_tasks: list[InterruptedTaskInfo]
    last_shutdown_at: datetime | None
    recovery_at: datetime


@dataclass(slots=True)
class CognitiveStateView:
    chat_id: str | None
    turn_id: str
    snapshot_at: datetime
    pending_events: list[Any] = field(default_factory=list)
    in_flight_tasks: list[AgentTaskSnapshot] = field(default_factory=list)
    delivered_this_turn: list[AgentTaskSnapshot] = field(default_factory=list)
    recently_completed: list[AgentTaskSnapshot] = field(default_factory=list)
    recovery_notice: RecoveryNotice | None = None
    busy_hint: Literal["idle", "thinking", "speaking", "waiting_on_background"] = "idle"
    similar_in_flight_for_pending: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTaskHandle:
    task_id: str
    status: TaskStatus
    estimated_first_progress_at: datetime | None
    workspace_path: str


@dataclass(slots=True)
class CancellationResult:
    cancelled_task_ids: list[str]
    skipped_task_ids: list[str]
    ambiguous_match: list[AgentTaskSnapshot] | None
    cancellation_initiated_at: datetime


@dataclass(slots=True)
class RespondResult:
    accepted: bool
    new_status: TaskStatus
