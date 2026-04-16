"""权限管理 REST API — Phase 5。

查看和修改用户权限。基于 AuthorityGate 的动态扩展：
运行时权限覆盖存储在 JSON 文件中，叠加在 env var 静态配置之上。
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.core.authority_gate import AuthLevel, OPERATION_AUTH, DEFAULT_AUTH, identify

logger = logging.getLogger("lapwing.api.routes.permissions_v2")

router = APIRouter(prefix="/api/v2/permissions", tags=["permissions-v2"])

_overrides_path: Path | None = None
_overrides: dict = {}  # user_id → {"level": int, "name": str, "note": str}


def init(data_dir: Path) -> None:
    """初始化权限覆盖存储。"""
    global _overrides_path, _overrides
    _overrides_path = data_dir / "config" / "permission_overrides.json"
    _overrides_path.parent.mkdir(parents=True, exist_ok=True)
    if _overrides_path.exists():
        try:
            _overrides = json.loads(_overrides_path.read_text(encoding="utf-8"))
        except Exception:
            _overrides = {}


def _save() -> None:
    if _overrides_path is not None:
        _overrides_path.write_text(
            json.dumps(_overrides, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class UserPermissionBody(BaseModel):
    level: int
    name: str = ""
    note: str = ""


@router.get("")
async def list_permissions():
    """用户权限列表（env var 配置 + 运行时覆盖）。"""
    from config.settings import OWNER_IDS, TRUSTED_IDS

    users = {}

    # 静态配置
    for uid in OWNER_IDS:
        users[uid] = {"level": AuthLevel.OWNER, "name": "", "source": "env"}
    for uid in TRUSTED_IDS:
        users[uid] = {"level": AuthLevel.TRUSTED, "name": "", "source": "env"}

    # 运行时覆盖
    for uid, info in _overrides.items():
        users[uid] = {
            "level": info.get("level", 0),
            "name": info.get("name", ""),
            "note": info.get("note", ""),
            "source": "override",
        }

    return {
        "users": users,
        "defaults": {
            "desktop": "OWNER (via DESKTOP_DEFAULT_OWNER)",
            "qq_unknown": "GUEST",
            "unrecognized": "IGNORE",
        },
        "operation_auth": {k: v.name for k, v in OPERATION_AUTH.items()},
        "default_auth": DEFAULT_AUTH.name,
    }


@router.put("/{user_id}")
async def set_user_permission(user_id: str, body: UserPermissionBody):
    """修改用户权限（运行时覆盖）。"""
    if body.level not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="Invalid level (must be 0-3)")

    _overrides[user_id] = {
        "level": body.level,
        "name": body.name,
        "note": body.note,
    }
    _save()
    return {"success": True, "user_id": user_id, "level": body.level}


@router.delete("/{user_id}")
async def remove_user(user_id: str):
    """删除用户覆盖（恢复默认权限）。"""
    if user_id not in _overrides:
        raise HTTPException(status_code=404, detail="User override not found")

    del _overrides[user_id]
    _save()
    return {"success": True}


@router.get("/defaults")
async def get_defaults():
    """默认权限配置。"""
    from config.settings import DESKTOP_DEFAULT_OWNER

    return {
        "desktop_default_owner": DESKTOP_DEFAULT_OWNER,
        "default_auth": DEFAULT_AUTH.name,
        "default_auth_level": int(DEFAULT_AUTH),
        "operation_auth": {k: {"level": int(v), "name": v.name} for k, v in OPERATION_AUTH.items()},
    }
