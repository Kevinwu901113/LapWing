"""Maintenance B: repair queue data model and filesystem store.

Converts health report findings into inert repair queue items.
No automatic repair. No mutation of capabilities. No index rebuild.
No lifecycle transition. No proposal/candidate/trust-root mutation.
No command execution. No network access. No external process calls.
No AI model calls. Deterministic filesystem operations only.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.capabilities.health import CapabilityHealthReport

logger = logging.getLogger(__name__)

# ── Constants ──

_VALID_SOURCES: frozenset[str] = frozenset({
    "health_report", "manual", "import_audit", "lifecycle", "unknown",
})

_VALID_SEVERITIES: frozenset[str] = frozenset({
    "info", "warning", "error",
})

_VALID_STATUSES: frozenset[str] = frozenset({
    "open", "acknowledged", "resolved", "dismissed",
})

_VALID_ACTIONS: frozenset[str] = frozenset({
    "inspect", "reindex", "reeval", "repair_metadata",
    "add_provenance", "quarantine_review", "archive",
    "manual_review", "unknown",
})

# Mapping from health finding codes to recommended actions.
# These are labels only — they never execute anything.
_FINDING_CODE_TO_ACTION: dict[str, str] = {
    "missing_provenance_quarantined": "add_provenance",
    "missing_provenance_imported": "add_provenance",
    "missing_provenance_legacy": "add_provenance",
    "missing_provenance_archived": "add_provenance",
    "integrity_mismatch": "manual_review",
    "eval_missing": "reeval",
    "eval_stale": "reeval",
    "needs_eval": "reeval",
    "stale_eval": "reeval",
    "missing_boundary": "repair_metadata",
    "trust_root_revoked": "manual_review",
    "trust_root_expired": "manual_review",
    "trust_root_disabled": "manual_review",
    "trust_root_nearing_expiry": "manual_review",
    "quarantine_no_audit": "quarantine_review",
    "quarantine_audit_pending_review": "quarantine_review",
    "quarantine_review_pending_request": "quarantine_review",
    "quarantine_request_pending_plan": "quarantine_review",
    "quarantine_plan_pending_apply": "quarantine_review",
    "proposal_pending": "manual_review",
    "proposal_stale": "manual_review",
    "proposal_high_risk_pending": "manual_review",
    "proposal_corrupt": "repair_metadata",
    "candidate_pending": "manual_review",
    "candidate_high_risk_no_evidence": "manual_review",
    "candidate_high_risk_pending": "manual_review",
    "candidate_approved_not_saved": "manual_review",
    "candidate_rejected": "manual_review",
    "index_missing_row": "reindex",
    "index_stale_row": "reindex",
    "orphaned_corrupt_trust_root": "manual_review",
    "orphaned_quarantine_artifacts": "manual_review",
    "orphaned_empty_quarantine": "manual_review",
}

# Patterns that indicate executable content in action_payload values.
_EXECUTABLE_PATTERNS: tuple[str, ...] = (
    r"\brm\b",
    r"\bsh\b",
    r"\bbash\b",
    r"\bexec\s*\(",
    r"\beval\s*\(",
    r"\bsystem\s*\(",
    r"\bsubprocess\b",
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\b__import__\b",
    r"\bimportlib\b",
    r"\brunpy\b",
    r"\bpexpect\b",
    r"(?:^|[.;])\s*import\s+\w+",
    r"\brequests\b",
    r"\bhttpx\b",
    r"\burllib\b",
    r"\bopenai\b",
    r"\banthropic\b",
)

# Function names that would imply repair/execution capability.
_BANNED_FUNCTION_NAMES: frozenset[str] = frozenset({
    "repair_capability", "run_capability", "auto_repair_capability",
    "rebuild_index", "rebuild_index_from_health", "transition_capability",
    "promote_from_health", "execute_repair", "apply_repair",
    "run_repair", "auto_fix", "dispatch_action",
})

# Keys whose presence suggests a tool-call or executable structure.
_TOOL_CALL_KEYS: frozenset[str] = frozenset({
    "tool_name", "tool_call", "function_name", "function_call",
    "command", "script", "exec", "execute", "shell_cmd",
})

# URL schemes that imply remote action (not inert reference).
_ACTION_URL_SCHEMES: tuple[str, ...] = (
    "http://", "https://", "ftp://",
)

_REPAIR_QUEUE_DIR = "repair_queue"


# ── Data model ──


@dataclass
class RepairQueueItem:
    """A single repair queue item.

    Queue items are data-only records. The action_payload is inert
    metadata — it never triggers execution. Status changes only
    mutate the queue item file, never capabilities.
    """

    item_id: str
    created_at: str
    source: str  # health_report, manual, import_audit, lifecycle, unknown
    finding_code: str
    severity: str  # info, warning, error
    status: str  # open, acknowledged, resolved, dismissed
    title: str
    description: str
    recommended_action: str  # inspect, reindex, reeval, etc.
    action_payload: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    capability_id: str | None = None
    scope: str | None = None
    assigned_to: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None
    dismissed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in _VALID_SOURCES:
            raise ValueError(
                f"Invalid source {self.source!r}, expected one of {sorted(_VALID_SOURCES)}"
            )
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity {self.severity!r}, expected one of {sorted(_VALID_SEVERITIES)}"
            )
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}, expected one of {sorted(_VALID_STATUSES)}"
            )
        if self.recommended_action not in _VALID_ACTIONS:
            raise ValueError(
                f"Invalid recommended_action {self.recommended_action!r}, "
                f"expected one of {sorted(_VALID_ACTIONS)}"
            )
        if not self.item_id or not self.item_id.strip():
            raise ValueError("item_id must not be empty")
        if ".." in self.item_id or "/" in self.item_id or "\\" in self.item_id:
            raise ValueError(f"item_id contains path traversal: {self.item_id!r}")
        self._reject_executable_payload(self.action_payload)

    @staticmethod
    def _reject_executable_payload(payload: dict[str, Any]) -> None:
        """Reject action_payload values that look like shell commands or code.

        Scans string values and recursively scans nested dicts/lists.
        """
        RepairQueueItem._scan_payload_dict(payload, "action_payload")

    @staticmethod
    def _scan_payload_dict(d: dict[str, Any], path: str) -> None:
        for key, value in d.items():
            key_lower = key.lower().strip()
            cur_path = f"{path}.{key}"

            # Reject tool-call-like keys
            if key_lower in _TOOL_CALL_KEYS:
                raise ValueError(
                    f"{cur_path}: key {key!r} looks like a tool-call or command field"
                )

            # Reject banned function name values
            if isinstance(value, str) and value.lower().strip() in _BANNED_FUNCTION_NAMES:
                raise ValueError(
                    f"{cur_path}: value {value!r} is a banned function name"
                )

            if isinstance(value, str):
                lower = value.lower().strip()
                # Check executable patterns
                for pattern in _EXECUTABLE_PATTERNS:
                    if re.search(pattern, lower):
                        raise ValueError(
                            f"{cur_path}: contains executable-like content: {value!r}"
                        )
                # Check URL schemes
                for scheme in _ACTION_URL_SCHEMES:
                    if scheme in lower:
                        raise ValueError(
                            f"{cur_path}: contains URL scheme {scheme!r} in value {value!r}"
                        )
            elif isinstance(value, dict):
                RepairQueueItem._scan_payload_dict(value, cur_path)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        RepairQueueItem._scan_payload_dict(item, f"{cur_path}[{i}]")
                    elif isinstance(item, str):
                        lower = item.lower().strip()
                        for pattern in _EXECUTABLE_PATTERNS:
                            if re.search(pattern, lower):
                                raise ValueError(
                                    f"{cur_path}[{i}]: contains executable-like content: {item!r}"
                                )
                        for scheme in _ACTION_URL_SCHEMES:
                            if scheme in lower:
                                raise ValueError(
                                    f"{cur_path}[{i}]: contains URL scheme in value {item!r}"
                                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "created_at": self.created_at,
            "source": self.source,
            "finding_code": self.finding_code,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "recommended_action": self.recommended_action,
            "action_payload": self.action_payload,
            "evidence": self.evidence,
            "capability_id": self.capability_id,
            "scope": self.scope,
            "assigned_to": self.assigned_to,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "dismissed_at": self.dismissed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepairQueueItem":
        return cls(
            item_id=data["item_id"],
            created_at=data["created_at"],
            source=data.get("source", "unknown"),
            finding_code=data.get("finding_code", ""),
            severity=data.get("severity", "info"),
            status=data.get("status", "open"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            recommended_action=data.get("recommended_action", "unknown"),
            action_payload=data.get("action_payload", {}),
            evidence=data.get("evidence", {}),
            capability_id=data.get("capability_id"),
            scope=data.get("scope"),
            assigned_to=data.get("assigned_to"),
            updated_at=data.get("updated_at"),
            resolved_at=data.get("resolved_at"),
            dismissed_at=data.get("dismissed_at"),
            metadata=data.get("metadata", {}),
        )

    @property
    def dedup_key(self) -> tuple[str, str | None, str | None, str]:
        """Key for deduplication: (finding_code, capability_id, scope, recommended_action)."""
        return (self.finding_code, self.capability_id, self.scope, self.recommended_action)


# ── Store ──


class RepairQueueStore:
    """Filesystem-backed store for repair queue items.

    Storage layout:
        <data_dir>/repair_queue/<item_id>.json

    All operations are local filesystem reads/writes. No mutation
    of capabilities, index, lifecycle, proposals, candidates, or
    trust roots. No execution, no network, no LLM.
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._queue_dir = self.data_dir / _REPAIR_QUEUE_DIR

    def _ensure_queue_dir(self) -> None:
        self._queue_dir.mkdir(parents=True, exist_ok=True)

    def _item_path(self, item_id: str) -> Path:
        return self._queue_dir / f"{item_id}.json"

    # ── CRUD ──

    def create_item(self, item: RepairQueueItem) -> RepairQueueItem:
        """Persist a repair queue item to disk.

        Returns the item unchanged. The item_id must be unique.
        """
        self._ensure_queue_dir()
        item_path = self._item_path(item.item_id)
        if item_path.exists():
            raise FileExistsError(f"Repair queue item {item.item_id} already exists")
        data = item.to_dict()
        tmp_path = item_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_path.rename(item_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        return item

    def get_item(self, item_id: str) -> RepairQueueItem | None:
        """Read a repair queue item from disk, or None if not found."""
        item_path = self._item_path(item_id)
        if not item_path.is_file():
            return None
        try:
            data = json.loads(item_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("Repair queue item %s is not a JSON object", item_id)
                return None
            return RepairQueueItem.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read repair queue item %s: %s", item_id, e)
            return None

    def list_items(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        capability_id: str | None = None,
        action: str | None = None,
    ) -> list[RepairQueueItem]:
        """List repair queue items, optionally filtered.

        All filter parameters are optional. When multiple are provided,
        items must match all of them (AND semantics).
        """
        if not self._queue_dir.is_dir():
            return []
        results: list[RepairQueueItem] = []
        for file_path in sorted(self._queue_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                item = RepairQueueItem.from_dict(data)
            except (json.JSONDecodeError, OSError, ValueError):
                logger.debug("Skipping unreadable queue item: %s", file_path.name, exc_info=True)
                continue
            if status is not None and item.status != status:
                continue
            if severity is not None and item.severity != severity:
                continue
            if capability_id is not None and item.capability_id != capability_id:
                continue
            if action is not None and item.recommended_action != action:
                continue
            results.append(item)
        return results

    def update_status(
        self,
        item_id: str,
        new_status: str,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> RepairQueueItem | None:
        """Update the status of a repair queue item.

        Returns the updated item, or None if the item doesn't exist.
        Only the queue item file is modified. No capability, index,
        lifecycle, proposal, candidate, or trust root is touched.
        """
        if new_status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {new_status!r}, expected one of {sorted(_VALID_STATUSES)}"
            )
        item = self.get_item(item_id)
        if item is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        item.status = new_status
        item.updated_at = now
        if new_status == "resolved":
            item.resolved_at = now
        elif new_status == "dismissed":
            item.dismissed_at = now

        if reason:
            item.metadata["status_change_reason"] = reason
        if actor:
            item.metadata["status_change_actor"] = actor

        item_path = self._item_path(item_id)
        data = item.to_dict()
        tmp_path = item_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_path.rename(item_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        return item

    # ── Health report conversion ──

    def create_from_health_report(
        self,
        report: "CapabilityHealthReport",
        *,
        dedupe: bool = True,
    ) -> list[RepairQueueItem]:
        """Convert health report findings into repair queue items.

        Each finding becomes one queue item. The recommended_action is
        determined by mapping the finding code.

        If dedupe=True (default), skips findings that already have an
        open queue item with the same dedup key.

        Returns the list of newly created items.
        """
        created: list[RepairQueueItem] = []
        now = datetime.now(timezone.utc).isoformat()

        for finding in report.findings:
            action = _FINDING_CODE_TO_ACTION.get(finding.code, "manual_review")
            title = _build_item_title(finding)
            description = _build_item_description(finding)

            item = RepairQueueItem(
                item_id=f"rq-{uuid.uuid4().hex[:12]}",
                created_at=now,
                source="health_report",
                finding_code=finding.code,
                severity=finding.severity,
                status="open",
                title=title,
                description=description,
                recommended_action=action,
                action_payload=_build_action_payload(finding),
                evidence={"finding_details": finding.details},
                capability_id=finding.capability_id,
                scope=finding.scope,
            )

            if dedupe and self._has_open_duplicate(item):
                continue

            try:
                self.create_item(item)
                created.append(item)
            except FileExistsError:
                logger.debug("Repair queue item %s already exists (race), skipping", item.item_id)
                continue

        return created

    def _has_open_duplicate(self, item: RepairQueueItem) -> bool:
        """Check if an open item with the same dedup key already exists."""
        existing = self.list_items(
            status="open",
            capability_id=item.capability_id,
            action=item.recommended_action,
        )
        for existing_item in existing:
            if existing_item.finding_code == item.finding_code:
                return True
        return False


# ── Helpers ──


def _build_item_title(finding: Any) -> str:
    """Build a concise title from a health finding."""
    code = getattr(finding, "code", "")
    cap_id = getattr(finding, "capability_id", None)
    if cap_id:
        return f"[{code}] {cap_id}"
    return f"[{code}]"


def _build_item_description(finding: Any) -> str:
    """Build a description from a health finding's message."""
    return getattr(finding, "message", "")


def _build_action_payload(finding: Any) -> dict[str, Any]:
    """Build an inert action_payload from a health finding.

    The payload is pure metadata — it never triggers execution.
    """
    code = getattr(finding, "code", "")
    details = getattr(finding, "details", {}) or {}
    payload: dict[str, Any] = {"source_finding_code": code}

    if "issue" in details:
        payload["issue"] = details["issue"]

    # Carry forward relevant context
    for key in ("stage", "status", "maturity", "proposal_id", "candidate_id",
                 "trust_root_id", "file", "directory"):
        if key in details:
            payload[key] = details[key]

    return payload
