"""Gated capability lifecycle transitions.

Orchestrates CapabilityPolicy, CapabilityEvaluator, EvalRecords,
PromotionPlanner, CapabilityStore, and Versioning to apply controlled
maturity/status transitions that mutate capability state.

Phase 3B: first controlled mutation path. Not wired into promote_skill,
not exposed as tools, no script execution, no runtime auto-behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.capabilities.eval_records import get_latest_eval_record, write_eval_record
from src.capabilities.versioning import create_version_snapshot

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.evaluator import CapabilityEvaluator, EvalRecord
    from src.capabilities.policy import CapabilityPolicy, PolicyDecision
    from src.capabilities.promotion import PromotionPlan, PromotionPlanner
    from src.capabilities.schema import CapabilityManifest
    from src.capabilities.store import CapabilityStore

logger = logging.getLogger(__name__)

# Maturity transitions that require a fresh evaluator run.
_EVAL_REQUIRED_TRANSITIONS: set[tuple[str, str]] = {
    ("draft", "testing"),
    ("testing", "stable"),
    ("repairing", "testing"),
}


# ── Transition result ────────────────────────────────────────────────────


@dataclass
class TransitionResult:
    capability_id: str
    scope: str
    from_maturity: str
    to_maturity: str
    from_status: str
    to_status: str
    applied: bool
    eval_record_id: str | None = None
    version_snapshot_id: str | None = None
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)
    blocking_findings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    content_hash_before: str = ""
    content_hash_after: str = ""


# ── Lifecycle manager ────────────────────────────────────────────────────


class CapabilityLifecycleManager:
    """Apply gated capability lifecycle transitions.

    Every applied transition:
      - Uses PromotionPlanner to check viability.
      - Passes through CapabilityPolicy.
      - Runs CapabilityEvaluator when quality is relevant.
      - Writes a version snapshot before mutation.
      - Refreshes the CapabilityIndex after mutation.
      - Records MutationLog events when a log is provided.

    Constructor args:
        store: CapabilityStore for CRUD + index + manifest sync.
        evaluator: CapabilityEvaluator for safety/quality lint.
        policy: CapabilityPolicy for allow/deny decisions.
        planner: PromotionPlanner for transition planning.
        mutation_log: optional MutationLog for audit events.
        available_tools: optional set/list of tool names for policy validation.
        trust_policy: optional CapabilityTrustPolicy for stable promotion gate (Phase 8C-1).
        trust_gate_enabled: feature flag for stable promotion trust gate (default False).
    """

    def __init__(
        self,
        store: "CapabilityStore",
        evaluator: "CapabilityEvaluator",
        policy: "CapabilityPolicy",
        planner: "PromotionPlanner",
        *,
        mutation_log: Any | None = None,
        available_tools: list[str] | set[str] | None = None,
        trust_policy: Any | None = None,
        trust_gate_enabled: bool = False,
    ) -> None:
        self._store = store
        self._evaluator = evaluator
        self._policy = policy
        self._planner = planner
        self._mutation_log = mutation_log
        self._available_tools: list[str] = (
            list(available_tools) if available_tools else []
        )
        self._trust_policy = trust_policy
        self._trust_gate_enabled = trust_gate_enabled

    # ── Public API ───────────────────────────────────────────────────

    def evaluate(
        self,
        capability_id: str,
        scope: str | None = None,
        *,
        write_record: bool = True,
    ) -> "EvalRecord":
        """Run the evaluator on a capability and optionally persist the record."""
        from src.capabilities.schema import CapabilityScope

        scope_enum = CapabilityScope(scope) if scope else None
        doc = self._store.get(capability_id, scope_enum)
        record = self._evaluator.evaluate(
            doc, available_tools=self._available_tools or None
        )
        if write_record:
            write_eval_record(record, doc, mutation_log=self._mutation_log)
        return record

    def plan_transition(
        self,
        capability_id: str,
        to_maturity_or_status: str,
        *,
        scope: str | None = None,
        approval: Any | None = None,
        failure_evidence: dict[str, Any] | None = None,
    ) -> "PromotionPlan":
        """Plan a transition without executing it.

        Returns a PromotionPlan describing whether the transition would be
        allowed and what evidence/approval is required.
        """
        from src.capabilities.schema import CapabilityScope

        scope_enum = CapabilityScope(scope) if scope else None
        doc = self._store.get(capability_id, scope_enum)
        target = to_maturity_or_status

        if target in ("disabled", "archived"):
            return self._plan_status_transition(doc, target)

        # Block promotion of disabled / archived / quarantined capabilities.
        from src.capabilities.promotion import PromotionPlan as PP

        from_status = doc.manifest.status.value
        from_mat = doc.manifest.maturity.value
        if from_status == "disabled":
            return PP(
                capability_id=capability_id,
                scope=doc.manifest.scope.value,
                from_maturity=from_mat,
                to_maturity=target,
                allowed=False,
                explanation="Disabled capability cannot be promoted",
            )
        if from_status == "archived":
            return PP(
                capability_id=capability_id,
                scope=doc.manifest.scope.value,
                from_maturity=from_mat,
                to_maturity=target,
                allowed=False,
                explanation="Archived capability cannot transition",
            )

        eval_record = get_latest_eval_record(doc)
        return self._planner.plan_transition(
            doc.manifest,
            target,
            eval_record=eval_record,
            approval=approval,
            failure_evidence=failure_evidence,
        )

    def apply_transition(
        self,
        capability_id: str,
        to_maturity_or_status: str,
        *,
        scope: str | None = None,
        approval: Any | None = None,
        failure_evidence: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> TransitionResult:
        """Apply a maturity or status transition, mutating capability state.

        Returns TransitionResult with applied=True on success, or
        applied=False with reasons when blocked.  Does not modify files
        when validation fails.
        """
        from src.capabilities.schema import CapabilityScope, CapabilityMaturity, CapabilityStatus

        scope_enum = CapabilityScope(scope) if scope else None
        doc = self._store.get(capability_id, scope_enum)
        resolved_scope = doc.manifest.scope.value

        target = to_maturity_or_status
        content_hash_before = doc.content_hash

        # ── status transitions (disable / archive) ──────────────────
        if target in ("disabled", "archived"):
            return self._apply_status_transition(
                doc, target, reason=reason, content_hash_before=content_hash_before,
            )

        # ── maturity transitions ────────────────────────────────────
        from_mat = doc.manifest.maturity.value
        from_status = doc.manifest.status.value
        to_mat = target

        # Block promotion of disabled / archived / quarantined capabilities.
        if from_status == "disabled":
            return TransitionResult(
                capability_id=capability_id,
                scope=resolved_scope,
                from_maturity=from_mat,
                to_maturity=to_mat,
                from_status=from_status,
                to_status=from_status,
                applied=False,
                message="Disabled capability cannot be promoted",
                content_hash_before=content_hash_before,
            )
        if from_status == "archived":
            return TransitionResult(
                capability_id=capability_id,
                scope=resolved_scope,
                from_maturity=from_mat,
                to_maturity=to_mat,
                from_status=from_status,
                to_status=from_status,
                applied=False,
                message="Archived capability cannot transition",
                content_hash_before=content_hash_before,
            )

        # Build a base result for blocked / error paths.
        def _blocked(
            message: str,
            *,
            plan: "PromotionPlan | None" = None,
            policy_decisions: list[dict[str, Any]] | None = None,
            blocking_findings: list[dict[str, Any]] | None = None,
            eval_record_id: str | None = None,
        ) -> TransitionResult:
            return TransitionResult(
                capability_id=capability_id,
                scope=resolved_scope,
                from_maturity=from_mat,
                to_maturity=to_mat,
                from_status=from_status,
                to_status=from_status,
                applied=False,
                eval_record_id=eval_record_id,
                policy_decisions=policy_decisions or [],
                blocking_findings=blocking_findings or [],
                message=message,
                content_hash_before=content_hash_before,
            )

        # 1. Run evaluator when quality is relevant.
        eval_record: "EvalRecord | None" = None
        if (from_mat, to_mat) in _EVAL_REQUIRED_TRANSITIONS:
            eval_record = self._evaluator.evaluate(
                doc, available_tools=self._available_tools or None
            )
            write_eval_record(eval_record, doc, mutation_log=self._mutation_log)

        # 2. Plan the transition.
        plan = self._planner.plan_transition(
            doc.manifest,
            to_mat,
            eval_record=eval_record,
            approval=approval,
            failure_evidence=failure_evidence,
        )

        if not plan.allowed:
            return _blocked(
                plan.explanation,
                plan=plan,
                blocking_findings=plan.blocking_findings,
                eval_record_id=plan.eval_record_id or (
                    eval_record.created_at if eval_record else None
                ),
            )

        # 3. Policy check.
        policy_decision = self._policy.validate_promote(
            doc.manifest,
            eval_record=eval_record,
            approval=approval,
        )
        policy_decisions = [
            {
                "code": policy_decision.code,
                "message": policy_decision.message,
                "severity": policy_decision.severity.value,
                "allowed": policy_decision.allowed,
            }
        ]

        if not policy_decision.allowed:
            return _blocked(
                f"Policy denied: {policy_decision.message}",
                policy_decisions=policy_decisions,
                eval_record_id=eval_record.created_at if eval_record else None,
            )

        # 3.5 Trust gate for testing -> stable (Phase 8C-1).
        if (
            self._trust_gate_enabled
            and self._trust_policy is not None
            and from_mat == "testing"
            and to_mat == "stable"
        ):
            from src.capabilities.provenance import read_provenance

            trust_provenance = read_provenance(doc.directory)

            trust_risk_level: str = "low"
            if doc.manifest.risk_level:
                trust_risk_level = doc.manifest.risk_level.value

            trust_decision = self._trust_policy.can_promote_to_stable(
                doc.manifest,
                provenance=trust_provenance,
                eval_record=eval_record,
                risk_level=trust_risk_level,
                approval=approval,
            )

            trust_decision_dict = {
                "code": trust_decision.code,
                "message": trust_decision.message,
                "severity": trust_decision.severity,
                "allowed": trust_decision.allowed,
                "details": trust_decision.details,
                "source": "CapabilityTrustPolicy",
            }
            policy_decisions.append(trust_decision_dict)

            if not trust_decision.allowed:
                return _blocked(
                    f"Trust gate denied stable promotion: {trust_decision.message}",
                    policy_decisions=policy_decisions,
                    eval_record_id=eval_record.created_at if eval_record else None,
                )

        # 4. Write version snapshot before mutation.
        trigger = f"promote_to_{to_mat}"
        snapshot = create_version_snapshot(doc, trigger, reason=reason or "")

        # 5. Update manifest maturity.
        now = datetime.now(timezone.utc)
        updated_manifest = doc.manifest.model_copy(update={
            "maturity": CapabilityMaturity(to_mat),
            "updated_at": now,
        })
        doc.manifest = updated_manifest

        # 6. Persist manifest and re-parse for a fresh content_hash.
        cap_dir = doc.directory
        self._store._sync_manifest_json(cap_dir, doc)
        doc = self._store._parser.parse(cap_dir)

        # 7. Refresh index.
        self._store._maybe_index(doc)

        # 8. Record mutation log.
        self._maybe_record("capability.transition_applied", {
            "capability_id": capability_id,
            "scope": resolved_scope,
            "from_maturity": from_mat,
            "to_maturity": to_mat,
            "from_status": from_status,
            "to_status": doc.manifest.status.value,
            "trigger": trigger,
            "reason": reason or "",
            "version_snapshot": snapshot.snapshot_dir,
            "eval_record_id": eval_record.created_at if eval_record else None,
        })

        return TransitionResult(
            capability_id=capability_id,
            scope=resolved_scope,
            from_maturity=from_mat,
            to_maturity=doc.manifest.maturity.value,
            from_status=from_status,
            to_status=doc.manifest.status.value,
            applied=True,
            eval_record_id=eval_record.created_at if eval_record else None,
            version_snapshot_id=snapshot.snapshot_dir,
            policy_decisions=policy_decisions,
            message=f"Transition {from_mat} -> {to_mat} applied",
            content_hash_before=content_hash_before,
            content_hash_after=doc.content_hash,
        )

    # ── Status transitions ───────────────────────────────────────────

    def _plan_status_transition(
        self, doc: "CapabilityDocument", target: str
    ) -> "PromotionPlan":
        """Build a PromotionPlan for a disable/archive transition."""
        from src.capabilities.promotion import PromotionPlan

        manifest = doc.manifest
        current_status = manifest.status.value
        current_maturity = manifest.maturity.value

        if current_status == target:
            return PromotionPlan(
                capability_id=manifest.id,
                scope=manifest.scope.value,
                from_maturity=current_maturity,
                to_maturity=current_maturity,
                allowed=False,
                explanation=f"Capability is already {target}",
            )

        if current_status == "archived":
            return PromotionPlan(
                capability_id=manifest.id,
                scope=manifest.scope.value,
                from_maturity=current_maturity,
                to_maturity=current_maturity,
                allowed=False,
                explanation="Archived capability cannot transition further",
            )

        return PromotionPlan(
            capability_id=manifest.id,
            scope=manifest.scope.value,
            from_maturity=current_maturity,
            to_maturity=current_maturity,
            allowed=True,
            explanation=f"Status transition to {target} is allowed",
        )

    def _apply_status_transition(
        self,
        doc: "CapabilityDocument",
        target: str,
        *,
        reason: str | None,
        content_hash_before: str,
    ) -> TransitionResult:
        """Execute a disable or archive transition."""
        from src.capabilities.schema import CapabilityStatus

        manifest = doc.manifest
        capability_id = manifest.id
        resolved_scope = manifest.scope.value
        from_mat = manifest.maturity.value
        from_status = manifest.status.value

        def _blocked(message: str) -> TransitionResult:
            return TransitionResult(
                capability_id=capability_id,
                scope=resolved_scope,
                from_maturity=from_mat,
                to_maturity=from_mat,
                from_status=from_status,
                to_status=from_status,
                applied=False,
                message=message,
                content_hash_before=content_hash_before,
            )

        # Plan check.
        plan = self._plan_status_transition(doc, target)
        if not plan.allowed:
            return _blocked(plan.explanation)

        # Policy check: validate_promote for status change gating.
        policy_decision = self._policy.validate_promote(doc.manifest)
        if not policy_decision.allowed:
            return _blocked(f"Policy denied: {policy_decision.message}")

        # Write version snapshot.
        trigger = "archived" if target == "archived" else "disabled"
        snapshot = create_version_snapshot(doc, trigger, reason=reason or "")

        # Delegate to store (handles manifest update, re-parse, index, mutation log).
        if target == "disabled":
            updated_doc = self._store.disable(capability_id, doc.manifest.scope)
        else:
            updated_doc = self._store.archive(capability_id, doc.manifest.scope)

        # Record lifecycle-level event (store already recorded its own event).
        self._maybe_record("capability.transition_applied", {
            "capability_id": capability_id,
            "scope": resolved_scope,
            "from_maturity": from_mat,
            "to_maturity": from_mat,
            "from_status": from_status,
            "to_status": target,
            "trigger": trigger,
            "reason": reason or "",
            "version_snapshot": snapshot.snapshot_dir,
        })

        return TransitionResult(
            capability_id=capability_id,
            scope=resolved_scope,
            from_maturity=from_mat,
            to_maturity=from_mat,
            from_status=from_status,
            to_status=target,
            applied=True,
            version_snapshot_id=snapshot.snapshot_dir,
            message=f"Status transition to {target} applied",
            content_hash_before=content_hash_before,
            content_hash_after=updated_doc.content_hash,
        )

    # ── Internal helpers ─────────────────────────────────────────────

    def _maybe_record(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record a mutation log event if a log is configured."""
        if self._mutation_log is None:
            return
        try:
            record = getattr(self._mutation_log, "record", None)
            if callable(record):
                record(event_type, payload)
        except Exception:
            logger.debug("mutation_log record failed", exc_info=True)
