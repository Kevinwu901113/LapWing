"""StateViewBuilder — assemble a StateView from live state.

Blueprint v2.0 Step 3 §3. The builder is the only place in the codebase
that knows how to read identity files, query AttentionManager, select a
trajectory window, and project open commitments/reminders/tasks into
the ``StateView`` schema. Everything downstream (StateSerializer, brain
entry points) takes the frozen snapshot as input and never touches a
store directly.

Two entry points:

- ``build_for_chat(chat_id, ...)`` — the conversational render. Uses the
  trajectory window relevant to that chat_id; current_conversation is
  taken from AttentionManager (which brain has already updated at the
  entry point).
- ``build_for_inner()`` — the consciousness-loop render. Pulls the
  cross-channel recent window (``TrajectoryStore.recent``) because the
  inner loop is not bound to any one chat.

Both routes collapse missing stores (``None``) to empty sections rather
than raising. This matches the pre-Step-3 behaviour where PromptBuilder
tolerated partial wiring (phase-0, unit tests, pre-container boot).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.core.prompt_loader import load_prompt
from src.core.state_view import (
    AttentionContext,
    CommitmentView,
    IdentityDocs,
    MemorySnippet,
    MemorySnippets,
    SkillSummary,
    StateView,
    TrajectoryTurn,
    TrajectoryWindow,
)
from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType

from src.ambient.time_context import TimeContextProvider

if TYPE_CHECKING:
    from src.ambient.ambient_knowledge import AmbientKnowledgeStore
    from src.core.attention import AttentionManager
    from src.core.commitments import CommitmentStore
    from src.core.task_model import TaskStore
    from src.core.trajectory_store import TrajectoryStore
    from src.memory.working_set import WorkingSet

logger = logging.getLogger("lapwing.core.state_view_builder")

_TAIPEI = ZoneInfo("Asia/Taipei")
_OFFLINE_THRESHOLD_HOURS = 4.0


# Types accepted as a "reminder source" — we only require the async
# ``get_due_reminders`` method. DurableScheduler satisfies this.
# Kept as a duck-typed Protocol to keep the builder decoupled.


class StateViewBuilder:
    """Gather live state into an immutable ``StateView``.

    All store references are optional; construct the builder with only
    the pieces currently wired (matches the container's incremental
    boot order).
    """

    def __init__(
        self,
        *,
        soul_path: Path | str = "data/identity/soul.md",
        constitution_path: Path | str = "data/identity/constitution.md",
        voice_prompt_name: str = "lapwing_voice",
        attention_manager: "AttentionManager | None" = None,
        trajectory_store: "TrajectoryStore | None" = None,
        commitment_store: "CommitmentStore | None" = None,
        task_store: "TaskStore | None" = None,
        reminder_source: object = None,  # duck-typed: has async get_due_reminders
        previous_state_reader=None,       # callable → dict | None (vitals.get_previous_state)
        working_set: "WorkingSet | None" = None,  # Step 7: memory retrieval
        history_turns: int = 30,
        inner_history_turns: int = 50,
        memory_top_k: int = 10,
        memory_query_chat_turns: int = 3,
    ) -> None:
        self._soul_path = Path(soul_path)
        self._constitution_path = Path(constitution_path)
        self._voice_prompt_name = voice_prompt_name
        self._attention = attention_manager
        self._trajectory = trajectory_store
        self._commitments = commitment_store
        self._tasks = task_store
        self._reminders = reminder_source
        # Indirection for the offline-gap probe keeps the builder
        # decoupled from ``src.core.vitals`` for tests.
        self._previous_state_reader = previous_state_reader
        self._working_set = working_set
        self._history_turns = history_turns
        self._inner_history_turns = inner_history_turns
        self._memory_top_k = memory_top_k
        self._memory_query_chat_turns = memory_query_chat_turns
        self._skill_store = None  # set by container when skill system is enabled
        self._ambient: AmbientKnowledgeStore | None = None
        self._time_provider = TimeContextProvider()

    # ── Entry points ─────────────────────────────────────────────────

    async def build_for_chat(
        self,
        chat_id: str,
        *,
        channel: str = "desktop",
        actor_id: str | None = None,
        actor_name: str | None = None,
        auth_level: int = 3,
        group_id: str | None = None,
        trajectory_turns_override: tuple[TrajectoryTurn, ...] | None = None,
    ) -> StateView:
        """Build the snapshot a user-facing ``think_*`` call will render.

        ``trajectory_turns_override`` lets brain hand in an already-
        prepared window (trust-tagged, effective-user-message-swapped).
        When ``None``, the builder queries ``TrajectoryStore`` itself —
        used by call paths that have no pre-processing need.
        """
        identity_docs = self._load_identity_docs()
        attention_context = self._build_attention_context(
            channel=channel,
            actor_id=actor_id,
            actor_name=actor_name,
            auth_level=auth_level,
            group_id=group_id,
        )
        if trajectory_turns_override is not None:
            trajectory_window = TrajectoryWindow(turns=trajectory_turns_override)
        else:
            trajectory_window = await self._build_trajectory_for_chat(chat_id)
        commitments_active = await self._build_commitments_active(chat_id=chat_id)
        memory_snippets = await self._build_memory_snippets(trajectory_window)
        skill_summary = self._build_skill_summary()
        time_context = self._time_provider.get_context(attention_context.now)
        ambient_entries = await self._build_ambient_entries()

        return StateView(
            identity_docs=identity_docs,
            attention_context=attention_context,
            trajectory_window=trajectory_window,
            memory_snippets=memory_snippets,
            commitments_active=commitments_active,
            skill_summary=skill_summary,
            time_context=time_context,
            ambient_entries=ambient_entries,
        )

    async def build_for_inner(
        self,
        *,
        trajectory_turns_override: tuple[TrajectoryTurn, ...] | None = None,
    ) -> StateView:
        """Build the snapshot the consciousness-loop render will use.

        No current speaker, no channel — the inner loop is Lapwing alone.
        Channel tag is ``""`` to match the pre-Step-3 ``adapter=""``
        convention used at the consciousness entry point. The optional
        ``trajectory_turns_override`` parallels ``build_for_chat``.
        """
        identity_docs = self._load_identity_docs()
        attention_context = self._build_attention_context(
            channel="",
            actor_id=None,
            actor_name=None,
            auth_level=3,
            group_id=None,
        )
        if trajectory_turns_override is not None:
            trajectory_window = TrajectoryWindow(turns=trajectory_turns_override)
        else:
            trajectory_window = await self._build_trajectory_for_inner()
        commitments_active = await self._build_commitments_active(chat_id=None)
        memory_snippets = await self._build_memory_snippets(trajectory_window)
        skill_summary = self._build_skill_summary()
        time_context = self._time_provider.get_context(attention_context.now)
        ambient_entries = await self._build_ambient_entries()

        return StateView(
            identity_docs=identity_docs,
            attention_context=attention_context,
            trajectory_window=trajectory_window,
            memory_snippets=memory_snippets,
            commitments_active=commitments_active,
            skill_summary=skill_summary,
            time_context=time_context,
            ambient_entries=ambient_entries,
        )

    # ── Identity ─────────────────────────────────────────────────────

    def _load_identity_docs(self) -> IdentityDocs:
        return IdentityDocs(
            soul=_read_text(self._soul_path),
            constitution=_read_text(self._constitution_path),
            voice=_load_prompt_or_empty(self._voice_prompt_name),
        )

    # ── Attention ────────────────────────────────────────────────────

    def _build_attention_context(
        self,
        *,
        channel: str,
        actor_id: str | None,
        actor_name: str | None,
        auth_level: int,
        group_id: str | None,
    ) -> AttentionContext:
        now = datetime.now(tz=_TAIPEI)
        current_conversation: str | None = None
        mode: str = "idle"
        if self._attention is not None:
            state = self._attention.get()
            current_conversation = state.current_conversation
            mode = state.mode
        offline_hours = self._compute_offline_hours()
        return AttentionContext(
            channel=channel,
            actor_id=actor_id,
            actor_name=actor_name,
            auth_level=auth_level,
            group_id=group_id,
            current_conversation=current_conversation,
            mode=mode,
            now=now,
            offline_hours=offline_hours,
        )

    def _compute_offline_hours(self) -> float | None:
        """Return hours since last_active, or None if unknown/short.

        Only values above the ``_OFFLINE_THRESHOLD_HOURS`` threshold are
        returned — the serializer emits the warning strictly on that
        signal. Returning the raw gap would leak state across the pure-
        function boundary only to be discarded.
        """
        reader = self._previous_state_reader
        if reader is None:
            return None
        try:
            prev = reader()
        except Exception:
            return None
        if not prev:
            return None
        last_active_str = prev.get("last_active")
        if not isinstance(last_active_str, str):
            return None
        try:
            last_dt = datetime.fromisoformat(last_active_str)
        except ValueError:
            return None
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        gap = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
        return gap if gap > _OFFLINE_THRESHOLD_HOURS else None

    # ── Trajectory ───────────────────────────────────────────────────

    async def _build_trajectory_for_chat(
        self, chat_id: str
    ) -> TrajectoryWindow:
        if self._trajectory is None:
            return TrajectoryWindow(turns=())
        entries = await self._trajectory.relevant_to_chat(
            chat_id, n=self._history_turns * 2, include_inner=False,
        )
        return TrajectoryWindow(turns=tuple(_entries_to_turns(entries)))

    async def _build_trajectory_for_inner(self) -> TrajectoryWindow:
        if self._trajectory is None:
            return TrajectoryWindow(turns=())
        entries = await self._trajectory.recent(n=self._inner_history_turns)
        return TrajectoryWindow(turns=tuple(_entries_to_turns(entries)))

    # ── Memory snippets (Step 7) ─────────────────────────────────────

    async def _build_memory_snippets(
        self, trajectory_window: TrajectoryWindow,
    ) -> MemorySnippets:
        """Retrieve relevant memory via WorkingSet.

        Query text is the concatenation of the last ``memory_query_chat_turns``
        user/assistant turns — enough lexical signal for semantic search,
        short enough that the embedding stays focused. If no WorkingSet
        is wired (phase-0 / unit tests without memory) or the trajectory
        is empty, returns empty snippets — serializer skips the layer.
        """
        if self._working_set is None:
            return MemorySnippets(snippets=())
        query_text = _trajectory_query_text(
            trajectory_window, self._memory_query_chat_turns,
        )
        if not query_text:
            return MemorySnippets(snippets=())
        try:
            return await self._working_set.retrieve(
                query_text, top_k=self._memory_top_k,
            )
        except Exception:
            logger.debug("WorkingSet.retrieve failed", exc_info=True)
            return MemorySnippets(snippets=())

    # ── Commitments / reminders / tasks ──────────────────────────────

    async def _build_commitments_active(
        self, *, chat_id: str | None
    ) -> tuple[CommitmentView, ...]:
        views: list[CommitmentView] = []

        # CommitmentStore — open promises (Step 5: 标记 overdue)
        if self._commitments is not None:
            try:
                import time as _time
                opens = await self._commitments.list_open(chat_id=chat_id)
                now_epoch = _time.time()
                for c in opens:
                    deadline = getattr(c, "deadline", None)
                    is_overdue = bool(
                        deadline is not None and deadline < now_epoch
                    )
                    due_at_iso: str | None = None
                    if deadline is not None:
                        due_at_iso = datetime.fromtimestamp(
                            deadline, tz=timezone.utc
                        ).isoformat()
                    views.append(
                        CommitmentView(
                            id=c.id,
                            description=c.content,
                            status=c.status,
                            kind="promise",
                            due_at=due_at_iso,
                            is_overdue=is_overdue,
                        )
                    )
            except Exception:
                logger.debug("CommitmentStore.list_open failed", exc_info=True)

        # DurableScheduler / reminder_source — due reminders
        if self._reminders is not None:
            try:
                now_utc = datetime.now(timezone.utc)
                due = await self._reminders.get_due_reminders(
                    chat_id="__all__",
                    now=now_utc,
                    grace_seconds=1800,
                    limit=3,
                )
                for i, r in enumerate(due or ()):
                    views.append(
                        CommitmentView(
                            id=f"reminder-{i}",
                            description=str(r.get("content", "")),
                            status="open",
                            kind="reminder",
                            due_at=str(r.get("next_trigger_at", "")),
                        )
                    )
            except Exception:
                logger.debug("reminder_source.get_due_reminders failed", exc_info=True)

        # TaskStore — active tasks
        if self._tasks is not None:
            try:
                active = await self._tasks.list_active()
                for t in active[:5]:
                    views.append(
                        CommitmentView(
                            id=t.task_id,
                            description=t.request,
                            status=t.status,
                            kind="task",
                            due_at=None,
                        )
                    )
            except Exception:
                logger.debug("task_store.list_active failed", exc_info=True)

        return tuple(views)

    def _build_skill_summary(self) -> SkillSummary | None:
        if self._skill_store is None:
            return None
        try:
            # 使用轻量级缓存索引，避免读取所有技能文件
            index = self._skill_store.get_skill_index()
        except Exception:
            return None
        if not index:
            return None

        counts = {"draft": 0, "testing": 0, "stable": 0, "broken": 0}
        stable_names = []
        testing_details = []
        for s in index:
            m = s.get("maturity", "draft")
            if m == "broken":
                counts["broken"] += 1
                continue
            counts[m] = counts.get(m, 0) + 1
            if m == "stable":
                stable_names.append(s.get("name", s.get("id", "")))
            elif m == "testing":
                testing_details.append(s.get("name", s.get("id", "")))

        return SkillSummary(
            stable_count=counts["stable"],
            testing_count=counts["testing"],
            draft_count=counts["draft"],
            broken_count=counts["broken"],
            stable_names=tuple(stable_names),
            testing_details=tuple(testing_details),
        )

    # ── Ambient knowledge ───────────────────────────────────────────

    async def _build_ambient_entries(self) -> tuple:
        if self._ambient is None:
            return ()
        try:
            return await self._ambient.get_all_fresh()
        except Exception:
            logger.debug("AmbientKnowledgeStore.get_all_fresh failed", exc_info=True)
            return ()


# ── Module-private helpers ──────────────────────────────────────────

def _read_text(path: Path) -> str:
    """Return file text, or empty string if missing / unreadable.

    Identical to PromptBuilder._load_file; kept here to avoid a back-
    reference to the module we're deleting in M2.f.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _load_prompt_or_empty(name: str) -> str:
    """Safe wrapper around prompt_loader.load_prompt.

    ``load_prompt`` raises when the template is missing (e.g., during
    Phase 0 which uses ``soul_test``). Builder treats that as an empty
    voice layer — serializer skips the layer — matching the pre-Step-3
    PromptBuilder.get_voice_reminder fallback.
    """
    try:
        return load_prompt(name) or ""
    except Exception:
        return ""


_ROLE_MAP: dict[str, str] = {
    TrajectoryEntryType.USER_MESSAGE.value: "user",
    TrajectoryEntryType.TELL_USER.value: "assistant",
    TrajectoryEntryType.ASSISTANT_TEXT.value: "assistant",
}


def _entries_to_turns(
    entries: list[TrajectoryEntry],
) -> list[TrajectoryTurn]:
    """Convert trajectory rows into TrajectoryTurn values.

    Mirrors ``trajectory_store.trajectory_entries_to_messages`` but
    emits ``TrajectoryTurn`` values instead of legacy dicts. user/
    assistant rows pass through; tool/state rows drop; inner-thoughts
    drop here (chat path passes ``include_inner=False`` above). Input
    is oldest→newest already.
    """
    out: list[TrajectoryTurn] = []
    for entry in entries:
        role = _ROLE_MAP.get(entry.entry_type)
        if role is None:
            continue
        text = _extract_entry_text(entry)
        if text is None:
            continue
        out.append(TrajectoryTurn(role=role, content=text))
    return out


def _trajectory_query_text(
    window: TrajectoryWindow, last_n_turns: int,
) -> str:
    """Concatenate the last N user/assistant turn contents.

    Step 7: WorkingSet uses this as the semantic-retrieval query. ``system``
    turns (voice reminders, summaries) are excluded to keep the query
    close to the *actual* topic rather than the scaffolding. Empty string
    when the window has no user/assistant rows.
    """
    if not window.turns:
        return ""
    usable = [t for t in window.turns if t.role in ("user", "assistant")]
    if not usable:
        return ""
    tail = usable[-last_n_turns:]
    return "\n".join(t.content for t in tail if t.content).strip()


def _extract_entry_text(entry: TrajectoryEntry) -> str | None:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
    text = content.get("text")
    if isinstance(text, str):
        return text
    return None
