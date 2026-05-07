from __future__ import annotations

from dataclasses import dataclass, field

from src.core.concurrent_bg_work.types import TaskStatus


class ResourceExhaustedError(RuntimeError):
    pass


@dataclass(slots=True)
class ConcurrencyPolicy:
    global_max_tasks: int = 6
    per_provider_max_tasks: dict[str, int] = field(default_factory=lambda: {
        "volcano_coding_lite": 3,
        "nim": 2,
        "minimax_vlm": 2,
        "claude_opus": 1,
    })
    per_spec_max_tasks: dict[str, int] = field(default_factory=lambda: {
        "researcher": 3,
        "coder": 1,
        "_default": 2,
    })
    per_chat_max_tasks: int = 4
    per_owner_max_tasks: int = 6
    backlog_max_global: int = 20
    backlog_max_per_chat: int = 6
    backlog_max_per_owner: int = 10


@dataclass(frozen=True, slots=True)
class NewTaskRequest:
    spec_id: str
    chat_id: str
    owner_user_id: str
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ConcurrencyDecision:
    allowed: bool
    reason: str | None
    target_status: TaskStatus
    pending_position: int | None


class ConcurrencyGate:
    def __init__(self, store, policy: ConcurrencyPolicy | None = None):
        self._store = store
        self._policy = policy or ConcurrencyPolicy()

    async def check(self, request: NewTaskRequest) -> ConcurrencyDecision:
        counts = await self._store.count_for_policy(
            spec_id=request.spec_id,
            chat_id=request.chat_id,
            owner_user_id=request.owner_user_id,
        )
        if counts["global_active"] >= self._policy.global_max_tasks:
            return self._consider_backlog(counts, "global_max")
        if counts["owner_active"] >= self._policy.per_owner_max_tasks:
            return self._consider_backlog(counts, "owner_max")
        if counts["chat_active"] >= self._policy.per_chat_max_tasks:
            return self._consider_backlog(counts, "chat_max")
        spec_limit = self._policy.per_spec_max_tasks.get(
            request.spec_id,
            self._policy.per_spec_max_tasks["_default"],
        )
        if counts["spec_active"] >= spec_limit:
            return self._consider_backlog(counts, "spec_max")
        return ConcurrencyDecision(True, None, TaskStatus.PENDING, None)

    def _consider_backlog(self, counts: dict[str, int], reason: str) -> ConcurrencyDecision:
        if counts["global_backlog"] >= self._policy.backlog_max_global:
            raise ResourceExhaustedError(f"backlog_max_global ({self._policy.backlog_max_global})")
        if counts["owner_backlog"] >= self._policy.backlog_max_per_owner:
            raise ResourceExhaustedError(f"backlog_max_per_owner ({self._policy.backlog_max_per_owner})")
        if counts["chat_backlog"] >= self._policy.backlog_max_per_chat:
            raise ResourceExhaustedError(f"backlog_max_per_chat ({self._policy.backlog_max_per_chat})")
        return ConcurrencyDecision(
            False,
            reason,
            TaskStatus.WAITING_RESOURCE,
            counts["global_backlog"] + 1,
        )
