"""模型配置 REST API — Phase 5。

包装现有 ModelConfigManager，提供 /api/v2/models/ 端点。
"""

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("lapwing.api.routes.models_v2")

router = APIRouter(prefix="/api/v2/models", tags=["models-v2"])

_config_manager = None
_llm_router = None


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
                _config_manager.assign_slot(slot_id, provider_id, model_id)
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
