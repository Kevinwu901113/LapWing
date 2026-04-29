"""Wiki write pipeline — gate → enqueue → compile → apply.

Phase 2 §2.6. The orchestration layer that ties the FastGate, the
CandidateStore, the WikiCompiler, and the WikiStore together. Two
public entry points:

- ``gate_and_enqueue(content, ...)`` — run the fast gate; on accept,
  persist the candidate to ``CandidateStore`` for later compilation.
- ``process_pending_candidates(...)`` — drain the pending pool, run
  structured extraction + compilation, and apply (or queue) the
  resulting patches.

Neither call fires in prod until ``MEMORY_WIKI_WRITE_ENABLED`` is set.
``process_pending_candidates`` short-circuits when write is disabled;
``gate_and_enqueue`` always runs because read-only enqueueing is safe
and feeds the same audit pipeline.

Hookup notes for follow-up integration:

- Call ``gate_and_enqueue`` from the trajectory-close handler or from
  the episodic extractor after each conversation slice. The gate
  itself is cheap (NIM lightweight slot).
- Schedule ``process_pending_candidates`` from the existing daily
  maintenance APScheduler job, or from a trajectory-close callback if
  you want lower latency.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from src.memory.candidate import MemoryCandidate
from src.memory.candidate_store import CandidateStore, PendingRecord
from src.memory.quality_gate import FastGate, MemoryGateDecision
from src.memory.wiki_compiler import WikiCompiler
from src.memory.wiki_store import (
    MemoryGuardBlocked,
    WikiStore,
    WikiWriteDisabled,
)

logger = logging.getLogger("lapwing.memory.wiki_pipeline")


WriteEnabledProvider = Callable[[], bool]


async def gate_and_enqueue(
    *,
    fast_gate: FastGate,
    candidate_store: CandidateStore,
    source_id: str,
    content: str,
    speaker: str = "",
    context_summary: str = "",
    source_ids: list[str] | None = None,
) -> tuple[MemoryGateDecision, str | None]:
    """Run the fast gate; enqueue if accepted. Returns (decision, candidate_id?).

    Always returns the gate decision so callers can log defer/reject for
    metrics. ``candidate_id`` is only set on accept.
    """
    decision = await fast_gate.evaluate(
        content=content,
        speaker=speaker,
        context_summary=context_summary,
        source_id=source_id,
    )
    if decision.decision != "accept":
        return decision, None

    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    cid = await candidate_store.enqueue(
        decision,
        source_ids=source_ids or [source_id],
        source_hash=source_hash,
    )
    return decision, cid


async def process_pending_candidates(
    *,
    candidate_store: CandidateStore,
    wiki_compiler: WikiCompiler,
    wiki_store: WikiStore,
    write_enabled_provider: WriteEnabledProvider,
    limit: int = 50,
) -> dict[str, int]:
    """Drain the pending pool. Returns counters for observability.

    Steps (matches Phase 2 blueprint):

        1. check write_enabled
        2. fetch pending records (oldest first)
        3. mark them ``compiling`` to claim the batch
        4. structure each record (extract_candidate or reuse cached JSON)
        5. compile candidates → patches
        6. apply low/medium-risk patches; queue high-risk for review
        7. mark compiled / failed per candidate
    """
    counters = {
        "pending": 0, "compiled": 0, "applied": 0, "queued": 0,
        "failed": 0, "skipped": 0, "guard_blocked": 0,
    }
    if not write_enabled_provider():
        logger.info("[wiki_pipeline] write_enabled=False; skipping compile")
        return counters

    pending = await candidate_store.get_pending(limit=limit)
    counters["pending"] = len(pending)
    if not pending:
        return counters

    await candidate_store.mark_compiling([p.id for p in pending])

    structured: list[tuple[PendingRecord, MemoryCandidate]] = []
    for record in pending:
        try:
            candidate = await _structure_one(record, wiki_compiler, candidate_store)
            structured.append((record, candidate))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[wiki_pipeline] structuring failed for %s: %s", record.id, exc,
            )
            await candidate_store.mark_failed(record.id, f"extract_error: {exc}")
            counters["failed"] += 1

    if not structured:
        return counters

    candidates = [c for _, c in structured]
    try:
        patches = await wiki_compiler.compile(candidates)
    except Exception as exc:  # noqa: BLE001
        logger.error("[wiki_pipeline] compile failed: %s", exc)
        for record, _ in structured:
            await candidate_store.mark_failed(record.id, f"compile_error: {exc}")
            counters["failed"] += 1
        return counters

    by_candidate: dict[str, list[str]] = {}
    for patch in patches:
        try:
            applied = await wiki_store.apply_patch(patch)
            if applied:
                counters["applied"] += 1
                by_candidate.setdefault(patch.candidate_id, []).append(
                    patch.target_page_id
                )
            else:
                counters["queued"] += 1
        except MemoryGuardBlocked:
            counters["guard_blocked"] += 1
            counters["queued"] += 1
        except WikiWriteDisabled:
            # Race: write got disabled between the gate check and the
            # apply. Re-queue this patch for human review.
            await wiki_store.record_pending_patch(
                patch, reason="write_disabled_race",
            )
            counters["queued"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[wiki_pipeline] apply_patch failed for %s: %s",
                patch.target_page_id, exc,
            )
            await wiki_store.record_pending_patch(
                patch, reason=f"apply_error: {exc}",
            )
            counters["queued"] += 1

    # Per-candidate accounting
    for record, candidate in structured:
        page_ids = by_candidate.get(candidate.id) or []
        if not page_ids and not _candidate_has_target(candidate):
            await candidate_store.mark_compiled(record.id, [])
            counters["skipped"] += 1
            continue
        await candidate_store.mark_compiled(record.id, page_ids)
        counters["compiled"] += 1

    return counters


# ── internals ──────────────────────────────────────────────────────


async def _structure_one(
    record: PendingRecord,
    compiler: WikiCompiler,
    store: CandidateStore,
) -> MemoryCandidate:
    if record.candidate_json:
        return MemoryCandidate.model_validate_json(record.candidate_json)
    candidate = await compiler.extract_candidate(record)
    await store.fill_candidate(record.id, candidate)
    return candidate


def _candidate_has_target(candidate: MemoryCandidate) -> bool:
    """Mirror WikiCompiler's MVP-scope rule for accounting purposes."""
    if candidate.subject in ("entity.kevin", "entity.lapwing"):
        return True
    return candidate.type in ("decision", "open_question")
