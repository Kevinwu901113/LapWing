"""MemoryCandidate + CompiledMemoryPatch — Phase 2 wiki write models.

Phase 2 §2.1 of the wiki blueprint. Candidates are the structured form
of an accepted gate signal: subject/predicate/object plus salience,
confidence, stability, privacy, and supporting evidence. The compiler
turns batches of candidates into ``CompiledMemoryPatch`` records that
the WikiStore knows how to apply atomically.

Two-step pipeline:

    raw message → FastGate decision (accept/defer/reject)
                ↓ (accept)
    CandidateStore pending row (gate-only fields filled in)
                ↓ wiki_compiler.extract_candidate()
    MemoryCandidate (full 14 fields, written back via fill_candidate)
                ↓ wiki_compiler.compile()
    CompiledMemoryPatch[] → wiki_store.apply_patch()
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


# ── Relations ───────────────────────────────────────────────────────

VALID_RELATION_TYPES = (
    "owned_by",
    "created_by",
    "creator_of",
    "part_of",
    "depends_on",
    "related_to",
    "supersedes",
    "contradicts",
)


class Relation(BaseModel):
    """A typed link between two wiki pages."""
    type: str
    target: str  # canonical entity id, e.g. "entity.kevin"


# ── Candidate ───────────────────────────────────────────────────────

CandidateType = Literal[
    "preference",
    "identity",
    "project_fact",
    "decision",
    "relationship",
    "commitment",
    "skill",
    "open_question",
]

StabilityLiteral = Literal["transient", "session", "long_lived", "permanent"]
PrivacyLiteral = Literal["public", "personal", "sensitive", "secret"]


def _new_candidate_id() -> str:
    return f"candidate:{uuid.uuid4().hex[:8]}"


class MemoryCandidate(BaseModel):
    """A structured fact extracted from accepted gate output.

    All fields are populated by the wiki_compiler during the
    structured-extraction step (``extract_candidate``). The fast gate
    only knows enough to score and route; turning that signal into a
    subject/predicate/object triple is the compiler's job.
    """
    id: str = Field(default_factory=_new_candidate_id)
    source_ids: list[str]
    subject: str  # canonical entity id
    predicate: str
    object: str
    type: CandidateType
    salience: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    stability: StabilityLiteral
    privacy_level: PrivacyLiteral
    contradiction_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_quote: str = ""
    expires_at: str | None = None
    relations: list[Relation] = Field(default_factory=list)


# ── Compiled patch ──────────────────────────────────────────────────

PatchOperation = Literal[
    "create",
    "update_section",
    "add_fact",
    "supersede_fact",
    "add_relation",
]
PatchRisk = Literal["low", "medium", "high"]


class CompiledMemoryPatch(BaseModel):
    """A ready-to-apply diff against one wiki page.

    ``before_hash`` enables optimistic-lock semantics: WikiStore.apply_patch
    rejects the patch if the on-disk page hash has drifted since the
    patch was generated.
    """
    target_page_id: str            # e.g. "entity.kevin"
    target_path: str               # e.g. "data/memory/wiki/entities/kevin.md"
    operation: PatchOperation
    section: str | None = None
    content: str
    reason: str
    source_ids: list[str]
    before_hash: str | None = None
    after_hash: str | None = None
    risk: PatchRisk = "low"
    candidate_id: str
