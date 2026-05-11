"""Desktop API for owner-attention interrupts.

Endpoints (blueprint §8.6):
  GET  /api/v2/interrupts/pending          — list pending interrupts for owner
  GET  /api/v2/interrupts/{id}             — interrupt detail
  POST /api/v2/interrupts/{id}/approve     — owner approves; resumes continuation
  POST /api/v2/interrupts/{id}/deny        — owner denies; cancels continuation

CRITICAL (blueprint §4.3 / §8.6, GPT final-pass fix):
  kernel.resume() returns a small status dict, NOT an Observation. The
  endpoint releases the continuation and returns immediately; the final
  Observation flows through the original agent worker's pipeline call,
  NOT awaited here. Tests must verify approve responds in <1s even when
  the worker sleeps for 5s.

See docs/architecture/lapwing_v1_blueprint.md §8.6, §15.2 I-6.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.interrupts")

router = APIRouter(prefix="/api/v2/interrupts", tags=["interrupts-v2"])

# Module-level dependency. Initialized by src/api/server.py via init().
_interrupt_store = None
_kernel = None


def init(*, interrupt_store, kernel=None) -> None:
    """Wire dependencies. Called by container at startup.

    interrupt_store: InterruptStore — the persistence facade
    kernel: optional Kernel — if provided, /approve uses kernel.resume() to
            release continuations; if absent (admin-only mode) /approve still
            marks the row resolved but does not wake any awaiter.
    """
    global _interrupt_store, _kernel
    _interrupt_store = interrupt_store
    _kernel = kernel


class InterruptListItem(BaseModel):
    id: str
    kind: str
    status: str
    resource: str
    summary: str
    created_at: str
    expires_at: str | None
    non_resumable: bool
    actor_required: str


class InterruptDetail(InterruptListItem):
    resource_ref: str | None
    continuation_ref: str | None
    non_resumable_reason: str | None
    payload_redacted: dict[str, Any]
    updated_at: str
    resolved_payload: dict[str, Any] | None


class ResolvePayload(BaseModel):
    payload: dict[str, Any] = {}


class DenyPayload(BaseModel):
    reason: str = "denied"


class ResumeResponse(BaseModel):
    status: str
    interrupt_id: str
    continuation_ref: str | None = None
    reason: str | None = None


@router.get("/pending", response_model=list[InterruptListItem])
async def list_pending():
    """List pending interrupts that require owner attention."""
    if _interrupt_store is None:
        raise HTTPException(503, "interrupt_store not initialized")
    pending = _interrupt_store.list_pending(actor="owner")
    return [
        InterruptListItem(
            id=i.id,
            kind=i.kind,
            status=i.status,
            resource=i.resource,
            summary=i.summary,
            created_at=i.created_at.isoformat(),
            expires_at=i.expires_at.isoformat() if i.expires_at else None,
            non_resumable=i.non_resumable,
            actor_required=i.actor_required,
        )
        for i in pending
    ]


@router.get("/{interrupt_id}", response_model=InterruptDetail)
async def detail(interrupt_id: str):
    if _interrupt_store is None:
        raise HTTPException(503, "interrupt_store not initialized")
    interrupt = _interrupt_store.get(interrupt_id)
    if interrupt is None:
        raise HTTPException(404, "interrupt not found")
    return InterruptDetail(
        id=interrupt.id,
        kind=interrupt.kind,
        status=interrupt.status,
        resource=interrupt.resource,
        summary=interrupt.summary,
        created_at=interrupt.created_at.isoformat(),
        expires_at=interrupt.expires_at.isoformat() if interrupt.expires_at else None,
        non_resumable=interrupt.non_resumable,
        actor_required=interrupt.actor_required,
        resource_ref=interrupt.resource_ref,
        continuation_ref=interrupt.continuation_ref,
        non_resumable_reason=interrupt.non_resumable_reason,
        payload_redacted=interrupt.payload_redacted,
        updated_at=interrupt.updated_at.isoformat(),
        resolved_payload=interrupt.resolved_payload,
    )


@router.post("/{interrupt_id}/approve", response_model=ResumeResponse)
async def approve(interrupt_id: str, body: ResolvePayload | None = None) -> ResumeResponse:
    """Owner approval entry point.

    CRITICAL: kernel.resume returns a status dict, NOT an Observation. We
    release the continuation and return immediately; the original agent
    worker produces the final Observation in its own coroutine through the
    normal pipeline. This endpoint MUST NOT await that.

    Returns:
      200 {status: 'resumed', interrupt_id, continuation_ref}    — success
      200 {status: 'error',   interrupt_id, reason: '...'}       — lost continuation
                                                                    / non-resumable
      404 — interrupt not found
      409 — interrupt is not pending (already resolved/denied/expired/cancelled)
      503 — kernel not wired
    """
    if _interrupt_store is None:
        raise HTTPException(503, "interrupt_store not initialized")
    if _kernel is None:
        raise HTTPException(503, "kernel not initialized — cannot resume")

    interrupt = _interrupt_store.get(interrupt_id)
    if interrupt is None:
        raise HTTPException(404, "interrupt not found")
    if interrupt.status != "pending":
        raise HTTPException(409, f"interrupt is {interrupt.status}, not pending")

    payload = (body.payload if body else {}) or {}

    # kernel.resume is fast: validate + continuation existence check + mark
    # resolved-or-cancelled + release future. We may safely await it.
    result = await _kernel.resume(interrupt_id, payload)
    return ResumeResponse(**result)


@router.post("/{interrupt_id}/deny", response_model=ResumeResponse)
async def deny(interrupt_id: str, body: DenyPayload | None = None) -> ResumeResponse:
    """Owner denies the interrupt. InterruptStore transitions to 'denied',
    and the awaiting continuation receives InterruptCancelled so the worker
    can wind down cleanly (blueprint §8.4 cleanup lifecycle)."""
    if _interrupt_store is None:
        raise HTTPException(503, "interrupt_store not initialized")
    interrupt = _interrupt_store.get(interrupt_id)
    if interrupt is None:
        raise HTTPException(404, "interrupt not found")
    if interrupt.status != "pending":
        raise HTTPException(409, f"interrupt is {interrupt.status}, not pending")

    reason = (body.reason if body else "denied") or "denied"
    _interrupt_store.deny(interrupt_id, reason=reason)

    # Cancel the awaiting continuation so the worker raises InterruptCancelled
    from src.lapwing_kernel.pipeline.continuation_registry import (
        ContinuationRegistry,
    )

    if interrupt.continuation_ref:
        ContinuationRegistry.instance().cancel(
            interrupt.continuation_ref, reason="denied"
        )

    return ResumeResponse(
        status="denied",
        interrupt_id=interrupt_id,
        continuation_ref=interrupt.continuation_ref,
    )
