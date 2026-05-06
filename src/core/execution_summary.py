"""Task-end execution summary observer and curator dry-run interfaces.

Defines protocols and data models for capturing sanitized execution summaries
and running curator dry-run decisions at task end.  This module has no
dependency on the capabilities package — it is a generic core interface that
concrete capability adapters implement.

Phase 5B: capture-only execution summary, no curation, no persistence.
Phase 5C: curator dry-run decision on sanitized summary, in-memory only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class TaskEndContext:
    """Raw data available at task end for summary capture.

    All fields are derived from the task's mutation log rows and message
    history.  No secrets have been redacted yet — the observer is
    responsible for sanitization before storage or display.
    """

    trace_id: str
    user_request: str = ""
    final_result: str = ""
    task_type: str | None = None
    tools_used: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    errors_seen: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    successful_steps: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    user_feedback: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "user_request": self.user_request,
            "final_result": self.final_result,
            "task_type": self.task_type,
            "tools_used": self.tools_used,
            "files_touched": self.files_touched,
            "commands_run": self.commands_run,
            "errors_seen": self.errors_seen,
            "failed_attempts": self.failed_attempts,
            "successful_steps": self.successful_steps,
            "verification": self.verification,
            "user_feedback": self.user_feedback,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


# ── Observer protocol ─────────────────────────────────────────────────────


class ExecutionSummaryObserver(Protocol):
    """Protocol for task-end execution summary capture.

    Concrete implementations may convert TaskEndContext into a TraceSummary,
    persist it, or attach it to debug metadata.  The protocol is intentionally
    minimal so TaskRuntime has no knowledge of capabilities.
    """

    async def capture(self, context: TaskEndContext) -> dict[str, Any] | None:
        """Capture a sanitized summary from task-end context.

        Must be best-effort and failure-safe.  Returns None on any failure.
        Must not raise exceptions that affect task completion.
        """
        ...


# ── Phase 5C: Curator dry-run data model ────────────────────────────────────


@dataclass
class CuratorDryRunResult:
    """Result of a dry-run curator decision on a sanitized execution summary.

    In-memory only — no proposal, draft, index, or mutation log changes.
    """

    trace_id: str
    should_create: bool = False
    recommended_action: str = "no_action"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risk_level: str = "low"
    required_approval: bool = False
    generalization_boundary: str = ""
    suggested_capability_type: str = "skill"
    suggested_triggers: list[str] = field(default_factory=list)
    suggested_tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "dry_run"
    persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "should_create": self.should_create,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "generalization_boundary": self.generalization_boundary,
            "suggested_capability_type": self.suggested_capability_type,
            "suggested_triggers": self.suggested_triggers,
            "suggested_tags": self.suggested_tags,
            "created_at": self.created_at,
            "source": self.source,
            "persisted": self.persisted,
        }


class CuratorDryRunObserver(Protocol):
    """Protocol for task-end curator dry-run decision.

    Concrete implementations may call ExperienceCurator.should_reflect()
    and summarize() but must NOT call propose_capability(), access
    CapabilityStore/Index/LifecycleManager, or persist anything.

    Must be best-effort and failure-safe. Returns None on any failure.
    """

    async def capture(self, summary: dict[str, Any]) -> dict[str, Any] | None:
        """Run curator dry-run on a sanitized execution summary.

        The summary dict is the output of ExecutionSummaryObserver.capture()
        (already sanitized). Returns a CuratorDryRunResult serialized to
        dict, or None on failure.
        """
        ...


# ── Phase 5D: Auto-proposal persistence ──────────────────────────────────


@dataclass
class AutoProposalResult:
    """Result of a task-end auto-proposal persistence attempt.

    In-memory only.  proposal_id and proposed_capability_id are filled
    only when persisted=True.  applied is always False — auto-proposal
    never creates drafts.
    """

    trace_id: str
    attempted: bool = False
    persisted: bool = False
    proposal_id: str | None = None
    proposed_capability_id: str | None = None
    reason: str = ""
    skipped_reason: str | None = None
    confidence: float = 0.0
    risk_level: str = "low"
    required_approval: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "task_end_auto_proposal"
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "attempted": self.attempted,
            "persisted": self.persisted,
            "proposal_id": self.proposal_id,
            "proposed_capability_id": self.proposed_capability_id,
            "reason": self.reason,
            "skipped_reason": self.skipped_reason,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "created_at": self.created_at,
            "source": self.source,
            "applied": self.applied,
        }


class AutoProposalObserver(Protocol):
    """Protocol for task-end auto-proposal persistence.

    Concrete implementations must convert sanitized summary + curator
    dry-run decision into a CapabilityProposal and persist proposal files
    only.  Must NOT create drafts, update indices, write eval records,
    or call CapabilityStore / CapabilityLifecycleManager.

    Must be best-effort and failure-safe.  Returns AutoProposalResult
    serialized to dict, or None on any failure.
    """

    async def capture(
        self,
        summary: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Attempt auto-proposal persistence from summary + curator decision.

        The summary dict is the sanitized output from
        ExecutionSummaryObserver.capture().  The decision dict is the
        CuratorDryRunResult serialized to dict from the curator dry-run
        observer.

        Returns AutoProposalResult serialized to dict, or None on failure.
        Must be best-effort — failure must not affect the user response.
        """
        ...


# ── Helper: build context from mutation-log rows ──────────────────────────

# Tool names whose arguments may contain file paths.
_FILE_TOUCHING_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "file_read", "file_write",
    "file_append", "apply_workspace_patch", "edit_file",
})

# Tool name for shell commands.
_SHELL_TOOL_NAMES: frozenset[str] = frozenset({"execute_shell", "run_python_code"})


def build_task_end_context(
    *,
    iteration_id: str,
    messages: list[dict[str, Any]],
    final_reply: str,
    mutation_rows: list[Any] | None = None,
) -> TaskEndContext:
    """Build a TaskEndContext from task-end data.

    Extracts user_request from the first user message, final_result from
    the reply, and tools/commands/files/errors from mutation log rows.

    Args:
        iteration_id: The iteration's unique id (used as trace_id).
        messages: The full message history for the task.
        final_reply: The final assistant reply text.
        mutation_rows: Rows from StateMutationLog.query_by_iteration(), or None.

    Returns:
        TaskEndContext with extracted data.  No secrets redaction is
        performed — that is the observer's responsibility.
    """
    # Extract user request from the first user message.
    user_request = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                user_request = content.strip()
                break

    # Derive a simple task_type heuristic from the user request.
    task_type = _derive_task_type(user_request)

    tools_used: list[str] = []
    commands_run: list[str] = []
    files_touched: list[str] = []
    errors_seen: list[str] = []

    if mutation_rows:
        for row in mutation_rows:
            event_type = getattr(row, "event_type", "")
            payload = getattr(row, "payload", {}) or {}

            if event_type in ("tool.called", "tool_called"):
                tool_name = str(payload.get("tool_name", ""))
                if tool_name:
                    tools_used.append(tool_name)
                    args = payload.get("arguments", {}) or {}

                    if tool_name in _SHELL_TOOL_NAMES:
                        cmd = _extract_command(args)
                        if cmd:
                            commands_run.append(cmd)

                    if tool_name in _FILE_TOUCHING_TOOLS:
                        path = _extract_file_path(args)
                        if path:
                            files_touched.append(path)

            elif event_type in ("tool.result", "tool_result"):
                success = payload.get("success", True)
                if not success:
                    reason = str(payload.get("reason", ""))
                    tool_name = str(payload.get("tool_name", ""))
                    if reason:
                        errors_seen.append(f"{tool_name}: {reason}"[:300])
                    else:
                        errors_seen.append(f"{tool_name}: failed")

    # Deduplicate while preserving order.
    tools_used = list(dict.fromkeys(tools_used))
    commands_run = list(dict.fromkeys(commands_run))
    files_touched = list(dict.fromkeys(files_touched))
    errors_seen = list(dict.fromkeys(errors_seen))

    return TaskEndContext(
        trace_id=iteration_id,
        user_request=user_request,
        final_result=final_reply,
        task_type=task_type,
        tools_used=tools_used,
        files_touched=files_touched,
        commands_run=commands_run,
        errors_seen=errors_seen,
        failed_attempts=[],
        successful_steps=[],
        verification=[],
        user_feedback=None,
        metadata={},
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _derive_task_type(user_request: str) -> str | None:
    """Derive a simple task_type from the user request text."""
    if not user_request:
        return None
    lower = user_request.lower()
    for keyword, task_type in _TASK_TYPE_KEYWORDS:
        if keyword in lower:
            return task_type
    return None


_TASK_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("deploy", "deploy"),
    ("fix", "bug-fix"),
    ("bug", "bug-fix"),
    ("refactor", "refactor"),
    ("test", "testing"),
    ("build", "build"),
    ("analyze", "analysis"),
    ("migrate", "migration"),
    ("setup", "setup"),
    ("install", "setup"),
    ("configure", "setup"),
    ("review", "review"),
    ("document", "documentation"),
    ("explain", "explanation"),
    ("search", "search"),
    ("find", "search"),
]


def _extract_command(args: dict[str, Any]) -> str | None:
    """Extract a shell command string from tool arguments."""
    cmd = args.get("command") or args.get("cmd") or args.get("shell_command") or ""
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip()[:2000]
    return None


def _extract_file_path(args: dict[str, Any]) -> str | None:
    """Extract a file path from tool arguments."""
    path = args.get("file_path") or args.get("path") or args.get("filename") or ""
    if isinstance(path, str) and path.strip():
        return path.strip()[:1000]
    return None
