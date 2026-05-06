"""Maintenance C: Repair Queue Operator Tools.

Operator-only tools for viewing and managing repair queue item status.
All tools require the capability_repair_operator tag — not granted to
standard/default/chat/local_execution or any other operator profile.

Hard constraints:
- No repair execution.
- No capability mutation.
- No index rebuild.
- No lifecycle transition.
- No proposal/candidate/trust-root mutation.
- No artifact deletion.
- No script execution.
- No network.
- No LLM judge.
- No run_capability.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)

# ── JSON Schemas ──────────────────────────────────────────────────────────

LIST_REPAIR_QUEUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["open", "acknowledged", "resolved", "dismissed"],
            "description": "Filter by item status",
        },
        "severity": {
            "type": "string",
            "enum": ["info", "warning", "error"],
            "description": "Filter by severity",
        },
        "capability_id": {
            "type": "string",
            "description": "Filter by capability ID",
        },
        "recommended_action": {
            "type": "string",
            "description": "Filter by recommended action",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Max items to return (default 50, max 200)",
        },
    },
}

VIEW_REPAIR_QUEUE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {
            "type": "string",
            "description": "Repair queue item ID (e.g. rq-xxxxxxxxxxxx)",
        },
    },
    "required": ["item_id"],
}

CREATE_REPAIR_QUEUE_FROM_HEALTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dedupe": {
            "type": "boolean",
            "description": "Skip findings that already have an open queue item (default true)",
        },
    },
}

ACKNOWLEDGE_REPAIR_QUEUE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {
            "type": "string",
            "description": "Repair queue item ID to acknowledge",
        },
        "actor": {
            "type": "string",
            "description": "Who is acknowledging this item (optional, stored in metadata)",
        },
        "reason": {
            "type": "string",
            "description": "Reason for acknowledgement (optional, stored in metadata)",
        },
    },
    "required": ["item_id"],
}

RESOLVE_REPAIR_QUEUE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {
            "type": "string",
            "description": "Repair queue item ID to resolve",
        },
        "actor": {
            "type": "string",
            "description": "Who is resolving this item (optional, stored in metadata)",
        },
        "reason": {
            "type": "string",
            "description": "Reason for resolution (optional, stored in metadata)",
        },
    },
    "required": ["item_id"],
}

DISMISS_REPAIR_QUEUE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {
            "type": "string",
            "description": "Repair queue item ID to dismiss",
        },
        "actor": {
            "type": "string",
            "description": "Who is dismissing this item (optional, stored in metadata)",
        },
        "reason": {
            "type": "string",
            "description": "Reason for dismissal (optional, stored in metadata)",
        },
    },
    "required": ["item_id"],
}


# ── Compact summary helper ────────────────────────────────────────────────

def _compact_summary(item) -> dict[str, Any]:
    """Return a compact summary dict for list display.
    Does NOT expand action_payload — it remains inert metadata.
    """
    return {
        "item_id": item.item_id,
        "created_at": item.created_at,
        "status": item.status,
        "severity": item.severity,
        "finding_code": item.finding_code,
        "capability_id": item.capability_id,
        "scope": item.scope,
        "title": item.title,
        "recommended_action": item.recommended_action,
        "assigned_to": item.assigned_to,
    }


# ── Executor factories ────────────────────────────────────────────────────

def _make_list_repair_queue_executor(repair_queue_store):
    """Read-only list with deterministic ordering and compact summaries."""

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            limit = int(args.get("limit", 50))
            limit = max(1, min(limit, 200))

            items = repair_queue_store.list_items(
                status=args.get("status"),
                severity=args.get("severity"),
                capability_id=args.get("capability_id"),
                action=args.get("recommended_action"),
            )

            # Apply limit (list_items already returns deterministically sorted)
            items = items[:limit]

            return ToolExecutionResult(
                success=True,
                payload={
                    "items": [_compact_summary(item) for item in items],
                    "count": len(items),
                    "total": len(items),
                },
            )
        except Exception as e:
            logger.debug("list_repair_queue_items failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "list_repair_queue_failed", "detail": str(e)},
                reason=f"list_repair_queue_items failed: {e}",
            )

    return executor


def _make_view_repair_queue_item_executor(repair_queue_store):
    """Read-only view of a single item including inert action_payload."""

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            item_id = str(args.get("item_id", "")).strip()
            if not item_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "item_id is required"},
                    reason="view_repair_queue_item requires item_id",
                )

            item = repair_queue_store.get_item(item_id)
            if item is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "item_id": item_id},
                    reason=f"Repair queue item not found: {item_id}",
                )

            return ToolExecutionResult(
                success=True,
                payload={"item": item.to_dict()},
            )
        except Exception as e:
            logger.debug("view_repair_queue_item failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "view_repair_queue_failed", "detail": str(e)},
                reason=f"view_repair_queue_item failed: {e}",
            )

    return executor


def _make_create_repair_queue_from_health_executor(repair_queue_store, *, capability_store=None):
    """Generate health report and create queue items for findings.
    Writes only repair_queue item files. Does not repair anything.
    """

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            if capability_store is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_store_unavailable"},
                    reason="Health report generation requires a capability store",
                )

            from src.capabilities.health import generate_capability_health_report

            args = request.arguments
            dedupe = bool(args.get("dedupe", True))

            report = generate_capability_health_report(capability_store)
            items = repair_queue_store.create_from_health_report(report, dedupe=dedupe)

            return ToolExecutionResult(
                success=True,
                payload={
                    "created": len(items),
                    "skipped": len(report.findings) - len(items),
                    "total_findings": len(report.findings),
                    "items": [_compact_summary(item) for item in items],
                    "recommendations": report.recommendations,
                },
            )
        except Exception as e:
            logger.debug("create_repair_queue_from_health failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "create_repair_queue_failed", "detail": str(e)},
                reason=f"create_repair_queue_from_health failed: {e}",
            )

    return executor


def _make_acknowledge_repair_queue_item_executor(repair_queue_store):
    """Status transition: open -> acknowledged. Writes only item JSON."""

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            item_id = str(args.get("item_id", "")).strip()
            if not item_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "item_id is required"},
                    reason="acknowledge_repair_queue_item requires item_id",
                )

            actor = args.get("actor")
            reason = args.get("reason")

            updated = repair_queue_store.update_status(
                item_id, "acknowledged", reason=reason, actor=actor
            )
            if updated is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "item_id": item_id},
                    reason=f"Repair queue item not found: {item_id}",
                )

            return ToolExecutionResult(
                success=True,
                payload={"item": _compact_summary(updated)},
            )
        except ValueError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("acknowledge_repair_queue_item failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "acknowledge_repair_queue_failed", "detail": str(e)},
                reason=f"acknowledge_repair_queue_item failed: {e}",
            )

    return executor


def _make_resolve_repair_queue_item_executor(repair_queue_store):
    """Status transition: open/acknowledged -> resolved. Writes only item JSON.
    Reason is optional — no repair verification is performed.
    """

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            item_id = str(args.get("item_id", "")).strip()
            if not item_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "item_id is required"},
                    reason="resolve_repair_queue_item requires item_id",
                )

            actor = args.get("actor")
            reason = args.get("reason")

            updated = repair_queue_store.update_status(
                item_id, "resolved", reason=reason, actor=actor
            )
            if updated is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "item_id": item_id},
                    reason=f"Repair queue item not found: {item_id}",
                )

            return ToolExecutionResult(
                success=True,
                payload={"item": _compact_summary(updated)},
            )
        except ValueError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("resolve_repair_queue_item failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "resolve_repair_queue_failed", "detail": str(e)},
                reason=f"resolve_repair_queue_item failed: {e}",
            )

    return executor


def _make_dismiss_repair_queue_item_executor(repair_queue_store):
    """Status transition: open -> dismissed. Writes only item JSON.
    Does not delete the item. Reason is optional.
    """

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            item_id = str(args.get("item_id", "")).strip()
            if not item_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "item_id is required"},
                    reason="dismiss_repair_queue_item requires item_id",
                )

            actor = args.get("actor")
            reason = args.get("reason")

            updated = repair_queue_store.update_status(
                item_id, "dismissed", reason=reason, actor=actor
            )
            if updated is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "item_id": item_id},
                    reason=f"Repair queue item not found: {item_id}",
                )

            return ToolExecutionResult(
                success=True,
                payload={"item": _compact_summary(updated)},
            )
        except ValueError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("dismiss_repair_queue_item failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "dismiss_repair_queue_failed", "detail": str(e)},
                reason=f"dismiss_repair_queue_item failed: {e}",
            )

    return executor


# ── Registration ──────────────────────────────────────────────────────────

def register_repair_queue_tools(
    tool_registry,
    repair_queue_store,
    *,
    capability_store=None,
) -> None:
    """Register Maintenance C repair queue operator tools.

    Six tools: list_repair_queue_items, view_repair_queue_item,
    create_repair_queue_from_health, acknowledge_repair_queue_item,
    resolve_repair_queue_item, dismiss_repair_queue_item.

    All tools use capability_repair_operator tag so they require an
    explicit operator profile — not granted to standard/default/chat.
    """
    if repair_queue_store is None:
        logger.warning("register_repair_queue_tools called with repair_queue_store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="list_repair_queue_items",
        description=(
            "List repair queue items with optional filters. "
            "Read-only. Returns compact summaries — action_payload is never expanded. "
            "Deterministic ordering."
        ),
        json_schema=LIST_REPAIR_QUEUE_SCHEMA,
        executor=_make_list_repair_queue_executor(repair_queue_store),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_repair_queue_item",
        description=(
            "View full details of a repair queue item including inert action_payload. "
            "Read-only. Returns clean not_found for missing items."
        ),
        json_schema=VIEW_REPAIR_QUEUE_ITEM_SCHEMA,
        executor=_make_view_repair_queue_item_executor(repair_queue_store),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="create_repair_queue_from_health",
        description=(
            "Generate a capability health report and create repair queue items for "
            "each finding. Deduplicates against existing open items by default. "
            "Writes only repair_queue item files. Does not repair anything."
        ),
        json_schema=CREATE_REPAIR_QUEUE_FROM_HEALTH_SCHEMA,
        executor=_make_create_repair_queue_from_health_executor(
            repair_queue_store, capability_store=capability_store,
        ),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="acknowledge_repair_queue_item",
        description=(
            "Acknowledge a repair queue item (status -> acknowledged). "
            "Writes only the item JSON file. Does not perform any repair."
        ),
        json_schema=ACKNOWLEDGE_REPAIR_QUEUE_ITEM_SCHEMA,
        executor=_make_acknowledge_repair_queue_item_executor(repair_queue_store),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="resolve_repair_queue_item",
        description=(
            "Resolve a repair queue item (status -> resolved). "
            "Writes only the item JSON file. Does not verify or perform any repair."
        ),
        json_schema=RESOLVE_REPAIR_QUEUE_ITEM_SCHEMA,
        executor=_make_resolve_repair_queue_item_executor(repair_queue_store),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="dismiss_repair_queue_item",
        description=(
            "Dismiss a repair queue item (status -> dismissed). "
            "Writes only the item JSON file. Does not delete or repair anything."
        ),
        json_schema=DISMISS_REPAIR_QUEUE_ITEM_SCHEMA,
        executor=_make_dismiss_repair_queue_item_executor(repair_queue_store),
        capability="capability_repair_operator",
        risk_level="low",
    ))

    logger.info(
        "Maintenance C repair queue operator tools registered "
        "(list/view/create-from-health/acknowledge/resolve/dismiss)"
    )
