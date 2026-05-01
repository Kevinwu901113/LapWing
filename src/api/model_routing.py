"""模型路由配置的 API 端点。

给桌面前端 Settings 页面调用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/model-routing", tags=["model-routing"])

# ModelConfigManager 和 LLMRouter 实例由 server.py 注入
_config_manager = None
_llm_router = None


def init(config_manager, llm_router=None) -> None:
    global _config_manager, _llm_router
    _config_manager = config_manager
    _llm_router = llm_router


# ── Request Models ──

class ProviderCreate(BaseModel):
    id: str
    name: str
    base_url: str
    api_key: str
    api_type: str = "openai"
    auth_type: str = "api_key"
    auth_style: str = "x_api_key"
    api_key_env: str | None = None
    protocol: str | None = None
    models: list[dict[str, Any]] = []


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_type: str | None = None
    auth_type: str | None = None
    auth_style: str | None = None
    api_key_env: str | None = None
    protocol: str | None = None
    models: list[dict[str, Any]] | None = None


class SlotAssign(BaseModel):
    provider_id: str
    model_id: str
    fallback_model_ids: list[str] | None = None


# ── 端点 ──

@router.get("/config")
async def get_config():
    """获取完整配置（api_key 脱敏）。"""
    return _config_manager.get_config()


@router.post("/providers")
async def add_provider(body: ProviderCreate):
    """添加 provider。"""
    try:
        result = _config_manager.add_provider(
            provider_id=body.id,
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key,
            api_type=body.api_type,
            models=body.models,
            auth_type=body.auth_type,
            auth_style=body.auth_style,
            api_key_env=body.api_key_env,
            protocol=body.protocol,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate):
    """更新 provider。"""
    updates = body.model_dump(exclude_unset=True)
    try:
        return _config_manager.update_provider(provider_id, **updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/providers/{provider_id}")
async def remove_provider(provider_id: str):
    """删除 provider。"""
    try:
        return _config_manager.remove_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/slots/{slot_id}")
async def assign_slot(slot_id: str, body: SlotAssign):
    """给 slot 分配模型。"""
    try:
        return _config_manager.assign_slot(
            slot_id=slot_id,
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_model_ids=body.fallback_model_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reload")
async def reload_routing():
    """热重载模型路由。前端保存配置后调用。"""
    if _llm_router is None:
        raise HTTPException(status_code=500, detail="LLMRouter 未注入，无法重载")
    _llm_router.reload_routing()
    return {"status": "ok", "message": "路由已重载"}
