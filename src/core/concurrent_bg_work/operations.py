from __future__ import annotations

from collections import defaultdict

from src.core.concurrent_bg_work.types import (
    CancelAgentTaskOp,
    CognitiveOperation,
    RespondToAgentInputOp,
    StartAgentTaskOp,
)


def reduce_operations(_snapshot, ops: list[CognitiveOperation]) -> tuple[list[CognitiveOperation], list[str]]:
    warnings: list[str] = []
    by_task: dict[str, list[CognitiveOperation]] = defaultdict(list)
    standalone: list[CognitiveOperation] = []

    for op in ops:
        target = _extract_target_task_id(op)
        if target:
            by_task[target].append(op)
        else:
            standalone.append(op)

    reduced = list(standalone)
    for task_id, group in by_task.items():
        has_cancel = any(isinstance(op, CancelAgentTaskOp) for op in group)
        has_start = any(isinstance(op, StartAgentTaskOp) for op in group)
        has_respond = any(isinstance(op, RespondToAgentInputOp) for op in group)
        if has_cancel and has_start:
            warnings.append(f"start+cancel same task {task_id}: cancel wins")
            reduced.append(next(op for op in group if isinstance(op, CancelAgentTaskOp)))
            continue
        if has_cancel and has_respond:
            warnings.append(f"respond+cancel same task {task_id}: cancel wins")
            reduced.append(next(op for op in group if isinstance(op, CancelAgentTaskOp)))
            continue
        cancels = [op for op in group if isinstance(op, CancelAgentTaskOp)]
        if len(cancels) > 1:
            warnings.append(f"duplicate cancel {task_id}: keep first")
            reduced.append(cancels[0])
            continue
        starts = [op for op in group if isinstance(op, StartAgentTaskOp)]
        if len(starts) > 1 and _same_idempotency(starts):
            warnings.append("duplicate start same key: merging")
            reduced.append(starts[0])
            continue
        reduced.extend(group)
    return reduced, warnings


def _extract_target_task_id(op: CognitiveOperation) -> str | None:
    if isinstance(op, CancelAgentTaskOp):
        return op.task_id
    if isinstance(op, RespondToAgentInputOp):
        return op.task_id
    if isinstance(op, StartAgentTaskOp):
        return op.replaces_task_id or op.idempotency_key
    return None


def _same_idempotency(starts: list[StartAgentTaskOp]) -> bool:
    keys = {op.idempotency_key for op in starts if op.idempotency_key}
    return len(keys) == 1 and all(op.idempotency_key for op in starts)
