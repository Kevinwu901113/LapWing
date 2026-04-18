"""身份文件 API 端点：soul.md / constitution.md / voice.md 的读写与版本管理。

三个文件通过各自的 manager 暴露同一套 history/diff/rollback 端点，
前端只需要写一套组件即可。
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.identity")

router = APIRouter(prefix="/api/v2/identity", tags=["identity"])

# base name (URL segment) → manager
_managers: dict[str, object] = {}


_BASE_TO_FILENAME = {
    "soul": "soul.md",
    "voice": "voice.md",
    "constitution": "constitution.md",
}
_FILENAME_TO_BASE = {v: k for k, v in _BASE_TO_FILENAME.items()}


def init(
    *,
    soul_manager=None,
    voice_manager=None,
    constitution_manager=None,
) -> None:
    _managers.clear()
    if soul_manager is not None:
        _managers["soul"] = soul_manager
    if voice_manager is not None:
        _managers["voice"] = voice_manager
    if constitution_manager is not None:
        _managers["constitution"] = constitution_manager


def _manager_for_filename(filename: str):
    base = _FILENAME_TO_BASE.get(filename)
    if base is None:
        raise HTTPException(status_code=404, detail=f"未知文件: {filename}")
    manager = _managers.get(base)
    if manager is None:
        raise HTTPException(status_code=503, detail=f"{base} 管理器未初始化")
    return manager


def _manager_for_base(base: str):
    if base not in _BASE_TO_FILENAME:
        raise HTTPException(status_code=404, detail=f"未知身份文件: {base}")
    manager = _managers.get(base)
    if manager is None:
        raise HTTPException(status_code=503, detail=f"{base} 管理器未初始化")
    return manager


class FileContent(BaseModel):
    content: str


@router.get("/{filename}")
async def read_identity_file(filename: str):
    """读取身份文件。文件不存在时返回空内容（让前端编辑器直接新建）。"""
    manager = _manager_for_filename(filename)
    return {"filename": filename, "content": manager.read()}


@router.put("/{filename}")
async def write_identity_file(filename: str, body: FileContent):
    """Kevin 编辑身份文件。三个文件统一走 manager.edit()。"""
    manager = _manager_for_filename(filename)
    result = manager.edit(
        new_content=body.content,
        actor="kevin",
        trigger="desktop_api",
    )
    return result


@router.get("/{base}/history")
async def identity_history(base: str):
    """获取快照历史。"""
    manager = _manager_for_base(base)
    snapshots = manager.list_snapshots()
    return {"snapshots": snapshots}


@router.get("/{base}/diff/{snapshot_id}")
async def identity_diff(base: str, snapshot_id: str):
    """某个快照与当前版本的 diff。"""
    manager = _manager_for_base(base)
    diff = manager.get_diff(snapshot_id)
    return {"snapshot_id": snapshot_id, "diff": diff}


@router.post("/{base}/rollback/{snapshot_id}")
async def identity_rollback(base: str, snapshot_id: str):
    """回滚到指定快照。"""
    manager = _manager_for_base(base)
    result = manager.rollback(snapshot_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["reason"])
    return result
