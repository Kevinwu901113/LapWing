"""StateView — immutable snapshot consumed by StateSerializer.

Blueprint v2.0 Step 3 §1. Replaces the implicit per-call collection of
context done by PromptBuilder + brain helpers. StateViewBuilder gathers
every piece of state needed to render a prompt into this frozen
container, then StateSerializer turns it into bytes. The split makes the
serializer a pure function — no I/O, no state lookups — so its output
is a deterministic function of its input and easy to unit-test.

The type is deliberately narrow. Every field has a concrete type; there
is no ``dict[str, Any]`` escape hatch. New context sources added in later
steps (Commitment Reviewer, multi-channel attention, etc.) must extend
this schema explicitly, so drift between "what the prompt sees" and
"what the rest of the system believes" remains visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ── Identity ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class IdentityDocs:
    """Stable, slow-changing identity markdown.

    ``soul`` = ``data/identity/soul.md`` (who Lapwing is).
    ``constitution`` = ``data/identity/constitution.md`` (the rules she
    cannot violate). Both are injected verbatim. Empty string means the
    file was missing at build time — the serializer treats that as a no-
    layer rather than a failure, matching pre-Step-3 behaviour.
    """

    soul: str
    constitution: str


# ── Attention ────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AttentionContext:
    """Who Lapwing is talking to and where her focus is.

    ``channel`` is the transport tag (``qq`` / ``qq_group`` / ``desktop``
    / ``""`` for inner loop). ``actor_id``/``actor_name`` identify the
    current speaker when the channel distinguishes multiple humans (group
    chat). ``auth_level`` follows the AuthorityGate convention: 3=OWNER,
    2=TRUSTED, 1=GUEST, 0=IGNORE. ``group_id`` is populated only for
    ``qq_group``.

    ``current_conversation`` + ``mode`` come straight from AttentionManager
    (``conversing`` / ``acting`` / ``idle``). ``now`` is the wall clock
    captured at build time — the serializer renders the time anchor from
    it rather than calling ``datetime.now`` itself.

    ``offline_hours`` signals "restart after long gap"; ``None`` means the
    gap is under the threshold or unknown. When set, the serializer emits
    the offline-reminder layer that warns the model its memories may have
    gone stale.
    """

    channel: str
    actor_id: str | None
    actor_name: str | None
    auth_level: int
    group_id: str | None
    current_conversation: str | None
    mode: str
    now: datetime
    offline_hours: float | None


# ── Trajectory ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class TrajectoryTurn:
    """One legacy-shape turn the serializer will emit as a message.

    ``role`` ∈ {``user``, ``assistant``, ``system``}. ``system`` is used
    for inner-thought rows when they are surfaced to the conversational
    path (same rendering convention as ``trajectory_compat`` had).
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class TrajectoryWindow:
    """Oldest→newest slice of trajectory chosen for this render.

    The builder applies the window policy (MAX_HISTORY_TURNS×2 for chat,
    a wider inner-loop window, etc.); the serializer only concatenates.
    """

    turns: tuple[TrajectoryTurn, ...]


# ── Memory ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class MemorySnippet:
    """A single retrieval hit worth quoting in the prompt.

    ``note_id`` is the retrieval source (note path / vector id); the
    serializer uses it only as an audit breadcrumb. ``score`` is the
    similarity ranking; callers pre-sort by it, the serializer keeps the
    incoming order.
    """

    note_id: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class MemorySnippets:
    """Already-ranked shortlist. Empty = no memory layer."""

    snippets: tuple[MemorySnippet, ...]


# ── Commitments ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CommitmentView:
    """Live obligation Lapwing holds at this moment.

    ``kind`` tags the source: ``promise`` for CommitmentStore rows,
    ``reminder`` for scheduled DurableScheduler reminders, ``task`` for
    active TaskStore entries. Keeping the tag explicit means the
    serializer can render each class with its own wording without the
    schema growing a new top-level StateView field for every subsystem
    (see Step-3 identity-boundary memo, §"Commitments umbrella").

    ``due_at`` is populated for reminders and due-dated tasks; ``None``
    means open-ended.
    """

    id: str
    description: str
    status: str
    kind: str
    due_at: str | None


# ── Top-level view ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class StateView:
    """Everything StateSerializer needs, frozen at one instant.

    The field order matches the prompt-layer render order so a reader of
    this type sees the system prompt's structure directly.
    """

    identity_docs: IdentityDocs
    attention_context: AttentionContext
    trajectory_window: TrajectoryWindow
    memory_snippets: MemorySnippets
    commitments_active: tuple[CommitmentView, ...]


# ── Serializer output ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SerializedPrompt:
    """Rendered output. ``system_prompt`` is the full system-role text;
    ``messages`` is the oldest→newest list of role/content dicts ready to
    hand to LLMRouter. ``list[dict]`` preserves the LLM-SDK contract at
    the boundary; callers must treat the list as read-only.
    """

    system_prompt: str
    messages: list[dict]
