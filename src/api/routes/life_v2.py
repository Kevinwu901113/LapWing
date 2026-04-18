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


@router.get("/ping")
async def ping():
    """Smoke endpoint used by tests to verify routing."""
    return {"ok": True}
