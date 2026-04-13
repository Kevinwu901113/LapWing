"""Agent 管理 API 端点。"""

import logging

from fastapi import APIRouter

logger = logging.getLogger("lapwing.api.routes.agents")

router = APIRouter(prefix="/api/agents", tags=["agents"])

_brain = None


def init(brain) -> None:
    global _brain
    _brain = brain


@router.get("")
async def list_agents():
    """获取所有注册 Agent 的信息。"""
    registry = getattr(_brain, "agent_registry", None)
    if registry is not None:
        return {"agents": registry.list_agents()}
    return {"agents": []}


@router.get("/active")
async def get_active_tasks():
    """获取当前活跃的 Agent 任务。"""
    dispatcher = getattr(_brain, "agent_dispatcher", None)
    if dispatcher is not None:
        return {"tasks": dispatcher.get_active_tasks()}
    return {"tasks": []}


@router.post("/{agent_name}/cancel")
async def cancel_agent_task(agent_name: str):
    """取消指定 Agent 的当前任务。"""
    dispatcher = getattr(_brain, "agent_dispatcher", None)
    if dispatcher is not None:
        success = await dispatcher.cancel_agent(agent_name)
        return {"success": success}
    return {"success": False, "error": "Agent system not available"}
