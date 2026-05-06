"""Eval record persistence — write, read, list, and retrieve evaluation records.

Stores EvalRecord JSON files in ``<capability_dir>/evals/``.
Never mutates manifest maturity/status and never triggers promotion.

Phase 3A: persistence foundation only — not wired into promotion or runtime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.capabilities.evaluator import (
    EVALUATOR_VERSION,
    EVAL_SCHEMA_VERSION,
    EvalFinding,
    EvalRecord,
    FindingSeverity,
)
from src.eval.axes import AxisResult, AxisStatus, EvalAxis

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument

logger = logging.getLogger(__name__)

EVALS_DIR = "evals"


def _eval_filename(created_at: str) -> str:
    """Generate a deterministic filename from the eval timestamp."""
    safe = created_at.replace(":", "-").replace("+", "-").replace(".", "-")
    return f"eval_{safe}.json"


def _eval_record_to_dict(record: EvalRecord) -> dict[str, Any]:
    findings = []
    for f in record.findings:
        findings.append({
            "severity": f.severity.value,
            "code": f.code,
            "message": f.message,
            "location": f.location,
            "details": f.details,
        })
    return {
        "capability_id": record.capability_id,
        "scope": record.scope,
        "content_hash": record.content_hash,
        "evaluator_version": record.evaluator_version,
        "schema_version": record.schema_version,
        "created_at": record.created_at,
        "passed": record.passed,
        "score": record.score,
        "findings": findings,
        "axes": {
            axis: {
                "axis": result.axis.value if hasattr(result.axis, "value") else str(result.axis),
                "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                "score": result.score,
                "findings": list(result.findings),
            }
            for axis, result in record.axes.items()
        },
        "required_approval": record.required_approval,
        "recommended_maturity": record.recommended_maturity,
    }


def _dict_to_eval_record(data: dict[str, Any]) -> EvalRecord:
    findings = []
    for f in data.get("findings", []):
        findings.append(EvalFinding(
            severity=FindingSeverity(f.get("severity", "info")),
            code=f.get("code", ""),
            message=f.get("message", ""),
            location=f.get("location"),
            details=f.get("details", {}),
        ))
    axes_data = data.get("axes")
    axes: dict[str, AxisResult] = {}
    if isinstance(axes_data, dict):
        for axis_name, raw in axes_data.items():
            if not isinstance(raw, dict):
                continue
            axis_value = raw.get("axis", axis_name)
            status_value = raw.get("status", "unknown")
            try:
                axis = EvalAxis(axis_value)
                status = AxisStatus(status_value)
            except ValueError:
                continue
            raw_findings = raw.get("findings", ())
            axes[axis.value] = AxisResult(
                axis=axis,
                status=status,
                score=raw.get("score"),
                findings=tuple(str(item) for item in raw_findings if item is not None),
            )

    return EvalRecord(
        capability_id=data["capability_id"],
        scope=data["scope"],
        content_hash=data.get("content_hash", ""),
        evaluator_version=data.get("evaluator_version", "3a.1"),
        schema_version=data.get("schema_version", "eval_record.v1"),
        created_at=data.get("created_at", ""),
        passed=data.get("passed", True),
        score=data.get("score", 1.0),
        findings=findings,
        required_approval=data.get("required_approval", False),
        recommended_maturity=data.get("recommended_maturity"),
        axes=axes,
    )


def write_eval_record(
    record: EvalRecord,
    doc: "CapabilityDocument",
    *,
    mutation_log: Any | None = None,
) -> Path:
    """Persist an eval record to ``<capability_dir>/evals/``.

    Returns the path to the written file.  Does not modify the manifest,
    maturity, or status.  If ``mutation_log`` is provided, the write is
    recorded via ``mutation_log.record()``.
    """
    evals_dir = doc.directory / EVALS_DIR
    evals_dir.mkdir(parents=True, exist_ok=True)

    filename = _eval_filename(record.created_at)
    filepath = evals_dir / filename

    data = _eval_record_to_dict(record)
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    if mutation_log is not None:
        try:
            record_fn = getattr(mutation_log, "record", None)
            if callable(record_fn):
                record_fn("eval.record_written", {
                    "capability_id": record.capability_id,
                    "scope": record.scope,
                    "eval_file": str(filepath),
                })
        except Exception:
            logger.debug("mutation_log record failed for eval write", exc_info=True)

    return filepath


def read_eval_record(
    doc: "CapabilityDocument",
    created_at: str,
) -> EvalRecord | None:
    """Read a specific eval record by its created_at timestamp."""
    evals_dir = doc.directory / EVALS_DIR
    filename = _eval_filename(created_at)
    filepath = evals_dir / filename

    if not filepath.exists():
        return None

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return _dict_to_eval_record(data)
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.debug("Failed to read eval record %s: %s", filepath, exc)
        return None


def list_eval_records(doc: "CapabilityDocument") -> list[EvalRecord]:
    """List all eval records for a capability, sorted by created_at descending."""
    evals_dir = doc.directory / EVALS_DIR
    if not evals_dir.is_dir():
        return []

    records: list[EvalRecord] = []
    for entry in sorted(evals_dir.iterdir(), reverse=True):
        if not entry.is_file() or not entry.name.startswith("eval_"):
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            records.append(_dict_to_eval_record(data))
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.debug("Skipping invalid eval record %s: %s", entry, exc)

    records.sort(key=lambda r: r.created_at, reverse=True)
    return records


def get_latest_eval_record(doc: "CapabilityDocument") -> EvalRecord | None:
    """Return the most recent eval record, or None."""
    records = list_eval_records(doc)
    return records[0] if records else None


def get_latest_valid_eval_record(
    doc: "CapabilityDocument",
    *,
    evaluator_version: str = EVALUATOR_VERSION,
    schema_version: str = EVAL_SCHEMA_VERSION,
) -> EvalRecord | None:
    """Return the newest record valid for the current capability content.

    Valid means the record matches the current content hash, current evaluator
    version, and current schema version. Older evaluator records are not used
    as fallback because they did not run current checks.
    """
    for record in list_eval_records(doc):
        if record.content_hash != doc.content_hash:
            continue
        if record.evaluator_version != evaluator_version:
            continue
        if record.schema_version != schema_version:
            continue
        return record
    return None
