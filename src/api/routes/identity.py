"""身份文件 API 端点：soul.md / constitution.md / voice.md 的读写与版本管理。"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.identity")

router = APIRouter(prefix="/api/v2/identity", tags=["identity"])

_soul_manager = None
_identity_dir: Path | None = None
_voice_path: Path | None = None


def init(soul_manager, *, identity_dir: Path, voice_path: Path) -> None:
    global _soul_manager, _identity_dir, _voice_path
    _soul_manager = soul_manager
    _identity_dir = identity_dir
    _voice_path = voice_path


_ALLOWED_FILES = {"soul.md", "constitution.md", "voice.md"}


def _resolve_path(filename: str) -> Path:
    if filename not in _ALLOWED_FILES:
        raise HTTPException(status_code=404, detail=f"未知文件: {filename}")
    if filename == "voice.md":
        return _voice_path
    return _identity_dir / filename


class FileContent(BaseModel):
    content: str


@router.get("/{filename}")
async def read_identity_file(filename: str):
    """读取身份文件。"""
    path = _resolve_path(filename)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    return {"filename": filename, "content": content}


@router.put("/{filename}")
async def write_identity_file(filename: str, body: FileContent):
    """Kevin 编辑身份文件。"""
    if filename == "soul.md" and _soul_manager is not None:
        result = _soul_manager.edit(
            new_content=body.content,
            actor="kevin",
            trigger="desktop_api",
        )
        return result

    path = _resolve_path(filename)
    path.write_text(body.content, encoding="utf-8")
    return {"success": True, "reason": "已保存"}


@router.get("/soul/history")
async def soul_history():
    """获取 soul.md 快照历史。"""
    if _soul_manager is None:
        raise HTTPException(status_code=503, detail="SoulManager 不可用")
    snapshots = _soul_manager.list_snapshots()
    return {"snapshots": snapshots}


@router.get("/soul/diff/{snapshot_id}")
async def soul_diff(snapshot_id: str):
    """获取某个快照与当前版本的 diff。"""
    if _soul_manager is None:
        raise HTTPException(status_code=503, detail="SoulManager 不可用")
    diff = _soul_manager.get_diff(snapshot_id)
    return {"snapshot_id": snapshot_id, "diff": diff}


@router.post("/soul/rollback/{snapshot_id}")
async def soul_rollback(snapshot_id: str):
    """回滚到指定快照。"""
    if _soul_manager is None:
        raise HTTPException(status_code=503, detail="SoulManager 不可用")
    result = _soul_manager.rollback(snapshot_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["reason"])
    return result
