"""Phase 6D: Agent candidate operator tools.

Feature-gated behind agents.candidate_tools_enabled.
All tools require the agent_candidate_operator capability tag.
Read-only tools: list_agent_candidates, view_agent_candidate.
Write tools: add_agent_candidate_evidence, approve_agent_candidate,
             reject_agent_candidate, archive_agent_candidate.

No execution, no auto-promotion, no active agent creation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

if TYPE_CHECKING:
    from src.agents.candidate_store import AgentCandidateStore
    from src.agents.policy import AgentPolicy

logger = logging.getLogger("lapwing.tools.agent_candidate_tools")

APPROVAL_STATES = {"pending", "approved", "rejected", "archived"}
RISK_LEVELS = {"low", "medium", "high"}
VALID_EVIDENCE_TYPES = {
    "task_success", "task_failure", "manual_review",
    "policy_lint", "dry_run", "regression_test",
}


# ── Schemas ──────────────────────────────────────────────────────────────

LIST_AGENT_CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "approval_state": {
            "type": "string",
            "enum": ["pending", "approved", "rejected", "archived"],
            "description": "Filter by approval state",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Filter by risk level",
        },
        "include_archived": {
            "type": "boolean",
            "description": "Include archived candidates (default false)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Max results (default 20, max 100)",
        },
    },
}

VIEW_AGENT_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Candidate ID to view",
        },
        "include_evidence": {
            "type": "boolean",
            "description": "Include evidence summaries (default true)",
        },
        "include_spec": {
            "type": "boolean",
            "description": "Include proposed_spec metadata (default true)",
        },
    },
    "required": ["candidate_id"],
}

ADD_AGENT_CANDIDATE_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Candidate ID to add evidence to",
        },
        "evidence_type": {
            "type": "string",
            "enum": [
                "task_success", "task_failure", "manual_review",
                "policy_lint", "dry_run", "regression_test",
            ],
            "description": "Type of evidence",
        },
        "summary": {
            "type": "string",
            "description": "Human-readable summary of the evidence",
        },
        "passed": {
            "type": "boolean",
            "description": "Whether the evaluation passed",
        },
        "score": {
            "type": "number",
            "description": "Optional score 0.0-1.0",
        },
        "trace_id": {
            "type": "string",
            "description": "Optional trace ID linking back to the task",
        },
        "details": {
            "type": "object",
            "description": "Optional additional details",
        },
    },
    "required": ["candidate_id", "evidence_type", "summary", "passed"],
}

APPROVE_AGENT_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Candidate ID to approve",
        },
        "reviewer": {
            "type": "string",
            "description": "Optional reviewer identifier",
        },
        "reason": {
            "type": "string",
            "description": "Optional reason for approval",
        },
    },
    "required": ["candidate_id"],
}

REJECT_AGENT_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Candidate ID to reject",
        },
        "reviewer": {
            "type": "string",
            "description": "Optional reviewer identifier",
        },
        "reason": {
            "type": "string",
            "description": "Optional reason for rejection",
        },
    },
    "required": ["candidate_id"],
}

ARCHIVE_AGENT_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Candidate ID to archive",
        },
        "reason": {
            "type": "string",
            "description": "Optional reason for archiving",
        },
    },
    "required": ["candidate_id"],
}


# ── Compact summary helper ────────────────────────────────────────────────

def _candidate_summary(candidate) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "description": candidate.description,
        "approval_state": candidate.approval_state,
        "risk_level": candidate.risk_level,
        "requested_runtime_profile": candidate.requested_runtime_profile,
        "requested_tools": candidate.requested_tools,
        "bound_capabilities": candidate.bound_capabilities,
        "evidence_count": len(candidate.eval_evidence),
        "created_at": candidate.created_at,
        "source_trace_id": candidate.source_trace_id,
    }


def _evidence_summary(ev) -> dict:
    return {
        "evidence_id": ev.evidence_id,
        "evidence_type": ev.evidence_type,
        "summary": ev.summary,
        "passed": ev.passed,
        "score": ev.score,
        "trace_id": ev.trace_id,
        "created_at": ev.created_at,
    }


# ── Executors ─────────────────────────────────────────────────────────────

def _make_list_candidates_executor(store: "AgentCandidateStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            approval_state = args.get("approval_state")
            risk_level = args.get("risk_level")
            include_archived = bool(args.get("include_archived", False))
            limit = min(int(args.get("limit", 20)), 100)

            if approval_state is not None and approval_state not in APPROVAL_STATES:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"invalid approval_state: {approval_state!r}"},
                    reason=f"Invalid approval_state: {approval_state}",
                )
            if risk_level is not None and risk_level not in RISK_LEVELS:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"invalid risk_level: {risk_level!r}"},
                    reason=f"Invalid risk_level: {risk_level}",
                )

            candidates = store.list_candidates(
                approval_state=approval_state,
                risk_level=risk_level,
            )

            # Filter archived unless explicitly included
            if not include_archived:
                candidates = [
                    c for c in candidates
                    if c.metadata.get("archived") is not True
                ]

            # Deterministic ordering by created_at descending
            candidates.sort(key=lambda c: c.created_at, reverse=True)

            # Apply limit
            candidates = candidates[:limit]

            return ToolExecutionResult(
                success=True,
                payload={
                    "candidates": [_candidate_summary(c) for c in candidates],
                    "count": len(candidates),
                },
            )
        except Exception as e:
            logger.debug("list_agent_candidates failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"list_agent_candidates failed: {e}",
            )

    return executor


def _make_view_candidate_executor(store: "AgentCandidateStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "candidate_id is required"},
                    reason="view_agent_candidate requires a candidate_id",
                )

            include_evidence = bool(args.get("include_evidence", True))
            include_spec = bool(args.get("include_spec", True))

            from src.agents.candidate_store import CandidateStoreError

            try:
                candidate = store.get_candidate(candidate_id)
            except CandidateStoreError:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "candidate_id": candidate_id},
                    reason=f"Candidate {candidate_id!r} not found",
                )

            result: dict = {
                "candidate_id": candidate.candidate_id,
                "name": candidate.name,
                "description": candidate.description,
                "approval_state": candidate.approval_state,
                "risk_level": candidate.risk_level,
                "requested_runtime_profile": candidate.requested_runtime_profile,
                "requested_tools": candidate.requested_tools,
                "bound_capabilities": candidate.bound_capabilities,
                "created_at": candidate.created_at,
                "created_by": candidate.created_by,
                "source_trace_id": candidate.source_trace_id,
                "source_task_summary": candidate.source_task_summary,
                "reason": candidate.reason,
                "version": candidate.version,
                "metadata": candidate.metadata,
            }

            if include_spec:
                spec = candidate.proposed_spec
                result["proposed_spec"] = {
                    "name": spec.name,
                    "description": spec.description,
                    "system_prompt_hash": spec.spec_hash(),
                    "model_slot": spec.model_slot,
                    "runtime_profile": spec.runtime_profile,
                    "risk_level": spec.risk_level,
                    "approval_state": spec.approval_state,
                    "capability_binding_mode": spec.capability_binding_mode,
                    "bound_capabilities": spec.bound_capabilities,
                    "allowed_delegation_depth": spec.allowed_delegation_depth,
                }

            if include_evidence:
                result["eval_evidence"] = [
                    _evidence_summary(ev) for ev in candidate.eval_evidence
                ]
                result["evidence_count"] = len(candidate.eval_evidence)

            result["policy_findings"] = [
                {
                    "severity": f.severity,
                    "code": f.code,
                    "message": f.message,
                }
                for f in candidate.policy_findings
            ]

            return ToolExecutionResult(success=True, payload=result)
        except Exception as e:
            logger.debug("view_agent_candidate failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"view_agent_candidate failed: {e}",
            )

    return executor


def _make_add_evidence_executor(store: "AgentCandidateStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "candidate_id is required"},
                    reason="add_agent_candidate_evidence requires a candidate_id",
                )

            evidence_type = str(args.get("evidence_type", "")).strip()
            if evidence_type not in VALID_EVIDENCE_TYPES:
                return ToolExecutionResult(
                    success=False,
                    payload={
                        "error": f"invalid evidence_type: {evidence_type!r}",
                        "allowed": sorted(VALID_EVIDENCE_TYPES),
                    },
                    reason=f"Invalid evidence_type: {evidence_type}",
                )

            summary = str(args.get("summary", "")).strip()
            passed = bool(args.get("passed", True))
            score = args.get("score")
            trace_id = str(args.get("trace_id", "")).strip() or None
            details = args.get("details")

            if score is not None:
                try:
                    score = float(score)
                    if not (0.0 <= score <= 1.0):
                        return ToolExecutionResult(
                            success=False,
                            payload={"error": f"score {score} out of [0.0, 1.0]"},
                            reason=f"Score out of range: {score}",
                        )
                except (TypeError, ValueError):
                    return ToolExecutionResult(
                        success=False,
                        payload={"error": f"score must be a number, got {score!r}"},
                        reason="Invalid score type",
                    )

            if details is not None and not isinstance(details, dict):
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "details must be a dict"},
                    reason="Invalid details type",
                )

            # Sanitize summary for secrets
            from src.agents.candidate import AgentEvalEvidence, redact_secrets_in_summary

            sanitized_summary = redact_secrets_in_summary(summary) or summary

            evidence = AgentEvalEvidence(
                evidence_type=evidence_type,
                summary=sanitized_summary,
                passed=passed,
                score=score,
                trace_id=trace_id,
                details=details if isinstance(details, dict) else {},
            )

            from src.agents.candidate_store import CandidateStoreError

            try:
                updated = store.add_evidence(candidate_id, evidence)
            except CandidateStoreError as e:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": str(e), "candidate_id": candidate_id},
                    reason=str(e),
                )

            return ToolExecutionResult(
                success=True,
                payload={
                    "candidate_id": candidate_id,
                    "evidence_id": evidence.evidence_id,
                    "evidence_type": evidence.evidence_type,
                    "approval_state": updated.approval_state,
                    "evidence_count": len(updated.eval_evidence),
                },
            )
        except Exception as e:
            logger.debug("add_agent_candidate_evidence failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"add_agent_candidate_evidence failed: {e}",
            )

    return executor


def _make_approve_candidate_executor(
    store: "AgentCandidateStore",
    policy: "AgentPolicy | None" = None,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "candidate_id is required"},
                    reason="approve_agent_candidate requires a candidate_id",
                )

            reviewer = str(args.get("reviewer", "")).strip() or None
            reason = str(args.get("reason", "")).strip() or None

            from src.agents.candidate_store import CandidateStoreError

            try:
                candidate = store.get_candidate(candidate_id)
            except CandidateStoreError:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "candidate_id": candidate_id},
                    reason=f"Candidate {candidate_id!r} not found",
                )

            # Refuse archived candidates
            if candidate.metadata.get("archived") is True:
                return ToolExecutionResult(
                    success=False,
                    payload={
                        "error": "candidate_archived",
                        "candidate_id": candidate_id,
                    },
                    reason="Cannot approve an archived candidate",
                )

            # Run policy validation before approval
            if policy is not None:
                lint_result = policy.validate_agent_candidate(candidate)
                if not lint_result.allowed:
                    return ToolExecutionResult(
                        success=False,
                        payload={
                            "error": "policy_denied",
                            "candidate_id": candidate_id,
                            "denials": lint_result.denials,
                            "warnings": lint_result.warnings,
                        },
                        reason="Candidate policy validation denied approval",
                    )

            updated = store.update_approval(
                candidate_id,
                "approved",
                reviewer=reviewer,
                reason=reason,
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "candidate_id": candidate_id,
                    "approval_state": updated.approval_state,
                    "reviewer": reviewer,
                },
            )
        except Exception as e:
            logger.debug("approve_agent_candidate failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"approve_agent_candidate failed: {e}",
            )

    return executor


def _make_reject_candidate_executor(store: "AgentCandidateStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "candidate_id is required"},
                    reason="reject_agent_candidate requires a candidate_id",
                )

            reviewer = str(args.get("reviewer", "")).strip() or None
            reason = str(args.get("reason", "")).strip() or None

            from src.agents.candidate_store import CandidateStoreError

            try:
                candidate = store.get_candidate(candidate_id)
            except CandidateStoreError:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "candidate_id": candidate_id},
                    reason=f"Candidate {candidate_id!r} not found",
                )

            updated = store.update_approval(
                candidate_id,
                "rejected",
                reviewer=reviewer,
                reason=reason,
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "candidate_id": candidate_id,
                    "approval_state": updated.approval_state,
                    "reviewer": reviewer,
                },
            )
        except Exception as e:
            logger.debug("reject_agent_candidate failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"reject_agent_candidate failed: {e}",
            )

    return executor


def _make_archive_candidate_executor(store: "AgentCandidateStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            candidate_id = str(args.get("candidate_id", "")).strip()
            if not candidate_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "candidate_id is required"},
                    reason="archive_agent_candidate requires a candidate_id",
                )

            reason = str(args.get("reason", "")).strip() or None

            from src.agents.candidate_store import CandidateStoreError

            try:
                store.get_candidate(candidate_id)
            except CandidateStoreError:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "candidate_id": candidate_id},
                    reason=f"Candidate {candidate_id!r} not found",
                )

            updated = store.archive_candidate(candidate_id, reason=reason)

            return ToolExecutionResult(
                success=True,
                payload={
                    "candidate_id": candidate_id,
                    "archived": True,
                    "evidence_count": len(updated.eval_evidence),
                },
            )
        except Exception as e:
            logger.debug("archive_agent_candidate failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "candidate_store_unavailable", "detail": str(e)},
                reason=f"archive_agent_candidate failed: {e}",
            )

    return executor


# ── Registration ──────────────────────────────────────────────────────────

def register_agent_candidate_tools(
    tool_registry,
    store: "AgentCandidateStore",
    policy: "AgentPolicy | None" = None,
) -> None:
    """Register Phase 6D agent candidate operator tools.

    All 6 tools use the agent_candidate_operator capability tag.
    They require an explicit AGENT_CANDIDATE_OPERATOR_PROFILE —
    not granted to standard/default/chat/local_execution/browser/identity.

    Feature-gated: caller must check agents.candidate_tools_enabled first.
    """
    if store is None:
        logger.warning("register_agent_candidate_tools called with store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="list_agent_candidates",
        description=(
            "List agent candidates with optional filters. "
            "Returns compact summaries (no full prompt bodies). "
            "Archived candidates excluded by default. "
            "Read-only, no execution."
        ),
        json_schema=LIST_AGENT_CANDIDATES_SCHEMA,
        executor=_make_list_candidates_executor(store),
        capability="agent_candidate_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_agent_candidate",
        description=(
            "View full details of an agent candidate. "
            "Optionally includes evidence summaries and proposed_spec metadata. "
            "Read-only, no execution, no registry mutation."
        ),
        json_schema=VIEW_AGENT_CANDIDATE_SCHEMA,
        executor=_make_view_candidate_executor(store),
        capability="agent_candidate_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="add_agent_candidate_evidence",
        description=(
            "Append an evidence record to an agent candidate. "
            "Does not change approval_state. "
            "Does not create an active agent. "
            "Sanitizes summary for secrets before storage."
        ),
        json_schema=ADD_AGENT_CANDIDATE_EVIDENCE_SCHEMA,
        executor=_make_add_evidence_executor(store),
        capability="agent_candidate_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="approve_agent_candidate",
        description=(
            "Approve an agent candidate. Runs policy validation before approval. "
            "Refuses archived candidates. "
            "Does not create an active agent, save a persistent agent, or grant permissions."
        ),
        json_schema=APPROVE_AGENT_CANDIDATE_SCHEMA,
        executor=_make_approve_candidate_executor(store, policy),
        capability="agent_candidate_operator",
        risk_level="medium",
    ))

    tool_registry.register(ToolSpec(
        name="reject_agent_candidate",
        description=(
            "Reject an agent candidate. Changes approval_state only. "
            "Does not delete files, affect active agents, or mutate proposed_spec."
        ),
        json_schema=REJECT_AGENT_CANDIDATE_SCHEMA,
        executor=_make_reject_candidate_executor(store),
        capability="agent_candidate_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="archive_agent_candidate",
        description=(
            "Archive an agent candidate. Archived candidates are excluded from "
            "default listing and blocked from the save gate. "
            "Does not delete evidence or affect active agents."
        ),
        json_schema=ARCHIVE_AGENT_CANDIDATE_SCHEMA,
        executor=_make_archive_candidate_executor(store),
        capability="agent_candidate_operator",
        risk_level="low",
    ))

    logger.info("Phase 6D agent candidate operator tools registered (6 tools)")
