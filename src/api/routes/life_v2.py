"""/api/v2/life/* — Desktop v2 "她的生活" 意识流时间轴 (read-only)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("lapwing.api.routes.life_v2")

router = APIRouter(prefix="/api/v2/life", tags=["life-v2"])

_trajectory_store = None
_soul_manager = None
_durable_scheduler = None
_llm_router = None
_summaries_dir: Path | None = None
# Tests may monkey-patch this to redirect the summaries directory.
_summaries_dir_override: Path | None = None


def init(
    trajectory_store=None,
    soul_manager=None,
    durable_scheduler=None,
    llm_router=None,
    summaries_dir: Path | None = None,
) -> None:
    global _trajectory_store, _soul_manager, _durable_scheduler, _llm_router, _summaries_dir
    _trajectory_store = trajectory_store
    _soul_manager = soul_manager
    _durable_scheduler = durable_scheduler
    _llm_router = llm_router
    _summaries_dir = summaries_dir


def _resolved_summaries_dir() -> Path | None:
    return _summaries_dir_override or _summaries_dir


from typing import Any

from fastapi import HTTPException, Query

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType


_TRAJECTORY_KINDS: set[str] = {t.value for t in TrajectoryEntryType}


def _parse_entry_types(raw: str | None) -> list[TrajectoryEntryType] | None:
    if not raw:
        return None
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in _TRAJECTORY_KINDS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown entry_type(s): {unknown}",
        )
    return [TrajectoryEntryType(n) for n in names]


def _serialize_trajectory(entry: TrajectoryEntry) -> dict[str, Any]:
    text = ""
    if isinstance(entry.content, dict):
        text = (
            entry.content.get("text")
            or entry.content.get("message")
            or entry.content.get("summary")
            or ""
        )
    return {
        "kind": entry.entry_type,
        "timestamp": entry.timestamp,
        "id": f"traj_{entry.id}",
        "content": text,
        "metadata": {
            "source_chat_id": entry.source_chat_id,
            "actor": entry.actor,
            "related_iteration_id": entry.related_iteration_id,
        },
    }


@router.get("/trajectory")
async def get_trajectory(
    limit: int = Query(100, ge=1, le=500),
    before_ts: float | None = Query(None),
    entry_types: str | None = Query(None),
    source_chat_id: str | None = Query(None),
):
    """Paginated, filtered trajectory read. Newest-first. Read-only debug view."""
    if _trajectory_store is None:
        return {"items": [], "next_before_ts": None}

    parsed_types = _parse_entry_types(entry_types)

    rows = await _trajectory_store.list_for_timeline(
        before_ts=before_ts,
        limit=limit,
        entry_types=parsed_types,
        source_chat_id=source_chat_id,
    )
    items = [_serialize_trajectory(r) for r in rows]
    next_cursor = items[-1]["timestamp"] if len(items) == limit else None
    return {"items": items, "next_before_ts": next_cursor}


@router.get("/ping")
async def ping():
    """Smoke endpoint used by tests to verify routing."""
    return {"ok": True}
