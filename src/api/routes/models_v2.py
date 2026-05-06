"""模型配置 REST API — Phase 5。

包装现有 ModelConfigManager，提供 /api/v2/models/ 端点。
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.models_v2")

router = APIRouter(prefix="/api/v2/models", tags=["models-v2"])

_config_manager = None
_llm_router = None


class ProviderCreate(BaseModel):
    id: str
    name: str
    base_url: str = ""
    api_key: str = ""
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


def init(config_manager, llm_router=None) -> None:
    global _config_manager, _llm_router
    _config_manager = config_manager
    _llm_router = llm_router


@router.get("/routing")
async def get_routing():
    """当前模型路由配置。"""
    if _config_manager is None:
        return {"error": "ModelConfigManager not available"}
    return _config_manager.get_config()


@router.put("/routing")
async def update_routing(config: dict):
    """修改路由配置（批量更新 slots）。"""
    if _config_manager is None:
        raise HTTPException(status_code=503, detail="ModelConfigManager not available")

    # 按 slot 逐一更新
    slots = config.get("slots", {})
    for slot_id, assignment in slots.items():
        provider_id = assignment.get("provider_id")
        model_id = assignment.get("model_id")
        if provider_id and model_id:
            try:
                _config_manager.assign_slot(
                    slot_id,
                    provider_id,
                    model_id,
                    fallback_model_ids=assignment.get("fallback_model_ids"),
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Slot {slot_id}: {e}")

    # 重载路由
    if _llm_router is not None and hasattr(_llm_router, "reload_routing"):
        _llm_router.reload_routing()

    return {"success": True}


@router.get("/available")
async def list_available_models():
    """列出可用的模型 slot 定义和已配置的 providers。"""
    from src.core.model_config import SLOT_DEFINITIONS

    providers = []
    if _config_manager is not None:
        full_config = _config_manager.get_config()
        providers = full_config.get("providers", [])

    return {
        "slots": list(SLOT_DEFINITIONS.keys()),
        "slot_definitions": SLOT_DEFINITIONS,
        "providers": providers,
    }


@router.post("/providers")
async def add_provider(body: ProviderCreate):
    """新增 provider，包括其初始 model registry。"""
    if _config_manager is None:
        raise HTTPException(status_code=503, detail="ModelConfigManager not available")
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
        if _llm_router is not None and hasattr(_llm_router, "reload_routing"):
            _llm_router.reload_routing()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate):
    """更新 provider 和它下面登记的 models。"""
    if _config_manager is None:
        raise HTTPException(status_code=503, detail="ModelConfigManager not available")
    try:
        result = _config_manager.update_provider(
            provider_id,
            **body.model_dump(exclude_unset=True),
        )
        if _llm_router is not None and hasattr(_llm_router, "reload_routing"):
            _llm_router.reload_routing()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str):
    """删除未被任何 slot 使用的 provider。"""
    if _config_manager is None:
        raise HTTPException(status_code=503, detail="ModelConfigManager not available")
    try:
        result = _config_manager.remove_provider(provider_id)
        if _llm_router is not None and hasattr(_llm_router, "reload_routing"):
            _llm_router.reload_routing()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
