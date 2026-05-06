"""src/agents/candidate_store.py — Filesystem storage for AgentCandidates.

Candidates live in data/agent_candidates/, separate from the active AgentCatalog
(SQLite lapwing.db). They cannot run, cannot be looked up by AgentRegistry, and
do not affect ToolDispatcher.

Layout:
    <base_dir>/
      <candidate_id>/
        candidate.json
        evidence/
          <evidence_id>.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.agents.candidate import (
    AgentCandidate,
    AgentEvalEvidence,
    validate_candidate_id,
    validate_evidence_id,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("lapwing.agents.candidate_store")


class CandidateStoreError(RuntimeError):
    """Raised when a candidate store operation fails."""


class AgentCandidateStore:
    """Filesystem-backed store for AgentCandidates.

    All methods are synchronous (no async I/O needed for local JSON files).
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    # ── helpers ──

    def _candidate_dir(self, candidate_id: str) -> Path:
        validate_candidate_id(candidate_id)
        return self._base_dir / candidate_id

    def _candidate_file(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "candidate.json"

    def _evidence_dir(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "evidence"

    def _ensure_dirs(self, candidate_id: str) -> None:
        self._candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        self._evidence_dir(candidate_id).mkdir(parents=True, exist_ok=True)

    # ── CRUD ──

    def create_candidate(self, candidate: AgentCandidate) -> AgentCandidate:
        """Persist a new candidate. Raises CandidateStoreError if candidate_id
        already exists."""
        cid = candidate.candidate_id
        candidate_file = self._candidate_file(cid)
        if candidate_file.exists():
            raise CandidateStoreError(
                f"candidate {cid!r} already exists; use a different candidate_id"
            )
        self._ensure_dirs(cid)
        self._write_candidate(candidate)
        logger.info("candidate %r created", cid)
        return candidate

    def get_candidate(self, candidate_id: str) -> AgentCandidate:
        """Read a candidate from disk. Raises CandidateStoreError if not found
        or corrupt."""
        candidate_file = self._candidate_file(candidate_id)
        if not candidate_file.exists():
            raise CandidateStoreError(f"candidate {candidate_id!r} not found")
        return self._read_candidate(candidate_id)

    def get_candidate_or_none(self, candidate_id: str) -> AgentCandidate | None:
        """Read a candidate from disk, returning None if not found."""
        try:
            return self.get_candidate(candidate_id)
        except CandidateStoreError:
            return None

    def list_candidates(
        self,
        *,
        approval_state: str | None = None,
        risk_level: str | None = None,
    ) -> list[AgentCandidate]:
        """List all candidates, optionally filtered."""
        if not self._base_dir.exists():
            return []
        results: list[AgentCandidate] = []
        for entry in sorted(self._base_dir.iterdir()):
            if not entry.is_dir():
                continue
            cid = entry.name
            try:
                validate_candidate_id(cid)
            except ValueError:
                continue
            candidate_file = entry / "candidate.json"
            if not candidate_file.exists():
                continue
            try:
                cand = self._read_candidate(cid)
            except CandidateStoreError:
                logger.warning("skipping corrupt candidate %r", cid)
                continue
            if approval_state is not None and cand.approval_state != approval_state:
                continue
            if risk_level is not None and cand.risk_level != risk_level:
                continue
            results.append(cand)
        return results

    def update_candidate(self, candidate: AgentCandidate) -> AgentCandidate:
        """Overwrite an existing candidate. Raises CandidateStoreError if the
        candidate does not exist yet."""
        cid = candidate.candidate_id
        if not self._candidate_file(cid).exists():
            raise CandidateStoreError(
                f"candidate {cid!r} does not exist; use create_candidate first"
            )
        self._write_candidate(candidate)
        return candidate

    # ── evidence ──

    def add_evidence(
        self, candidate_id: str, evidence: AgentEvalEvidence
    ) -> AgentCandidate:
        """Append an evidence record to a candidate and persist it."""
        cand = self.get_candidate(candidate_id)
        cand.eval_evidence.append(evidence)
        # Also write the standalone evidence file
        ev_dir = self._evidence_dir(candidate_id)
        ev_dir.mkdir(parents=True, exist_ok=True)
        validate_evidence_id(evidence.evidence_id)
        ev_path = ev_dir / f"{evidence.evidence_id}.json"
        ev_path.write_text(
            json.dumps(evidence.to_dict(), default=str, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_candidate(cand)
        logger.info(
            "evidence %r added to candidate %r", evidence.evidence_id, candidate_id
        )
        return cand

    # ── approval ──

    def update_approval(
        self,
        candidate_id: str,
        approval_state: str,
        reviewer: str | None = None,
        reason: str | None = None,
    ) -> AgentCandidate:
        """Update a candidate's approval state."""
        from src.agents.spec import VALID_APPROVAL_STATES
        if approval_state not in VALID_APPROVAL_STATES:
            raise CandidateStoreError(
                f"invalid approval_state {approval_state!r}, "
                f"expected one of {sorted(VALID_APPROVAL_STATES)}"
            )
        cand = self.get_candidate(candidate_id)
        cand.approval_state = approval_state
        if reviewer:
            cand.metadata["reviewer"] = reviewer
        if reason:
            cand.metadata["approval_reason"] = reason
        self._write_candidate(cand)
        logger.info(
            "candidate %r approval updated to %r", candidate_id, approval_state
        )
        return cand

    # ── archive ──

    def archive_candidate(
        self, candidate_id: str, reason: str | None = None
    ) -> AgentCandidate:
        """Mark a candidate as archived. Does NOT delete files."""
        cand = self.get_candidate(candidate_id)
        cand.metadata["archived"] = True
        from src.core.time_utils import now as _now
        cand.metadata["archived_at"] = _now().isoformat()
        if reason:
            cand.metadata["archive_reason"] = reason
        self._write_candidate(cand)
        logger.info("candidate %r archived", candidate_id)
        return cand

    # ── internal read / write ──

    def _read_candidate(self, candidate_id: str) -> AgentCandidate:
        candidate_file = self._candidate_file(candidate_id)
        try:
            raw = candidate_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CandidateStoreError(
                f"cannot read candidate {candidate_id!r}: {exc}"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CandidateStoreError(
                f"candidate {candidate_id!r} has corrupt JSON: {exc}"
            ) from exc
        try:
            return AgentCandidate.from_dict(data)
        except Exception as exc:
            raise CandidateStoreError(
                f"candidate {candidate_id!r} failed deserialization: {exc}"
            ) from exc

    def _write_candidate(self, candidate: AgentCandidate) -> None:
        cid = candidate.candidate_id
        candidate_file = self._candidate_file(cid)
        candidate_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = candidate_file.with_suffix(".tmp")
        tmp.write_text(candidate.to_json(), encoding="utf-8")
        tmp.replace(candidate_file)
