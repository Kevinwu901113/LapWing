"""create_skill / run_skill / edit_skill / list_skills / promote_skill / delete_skill"""
from __future__ import annotations

import logging

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.skill_tools")


# ── Schemas ──────────────────────────────────────────────────────────

CREATE_SKILL_DESCRIPTION = (
    "创建一个新技能。当你写了一段可复用的代码，用这个工具把它保存成技能。"
    "技能创建后状态是 draft，需要在沙盒中测试成功后才能升级。"
    "code 参数必须包含一个 def run(...) 函数作为入口。"
)
CREATE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "唯一标识，格式 skill_{简短描述}"},
        "name": {"type": "string", "description": "人类可读名称"},
        "description": {"type": "string", "description": "一句话说明功能"},
        "code": {"type": "string", "description": "Python 代码，必须包含 def run(...) 入口函数"},
        "dependencies": {
            "type": "array", "items": {"type": "string"},
            "description": "pip 依赖列表（可选）",
        },
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "分类标签（可选）",
        },
    },
    "required": ["skill_id", "name", "description", "code"],
    "additionalProperties": False,
}

RUN_SKILL_DESCRIPTION = (
    "执行一个技能。draft/testing/broken 状态的技能在 Docker 沙盒中执行，"
    "stable 状态的技能在主机上执行。执行结果会自动记录到技能元数据。"
)
RUN_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要执行的技能 ID"},
        "arguments": {
            "type": "object", "description": "传给 run() 函数的参数（可选）",
        },
        "timeout": {
            "type": "integer", "description": "超时秒数（默认 30）",
            "default": 30, "minimum": 1, "maximum": 300,
        },
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

EDIT_SKILL_DESCRIPTION = (
    "修改技能的代码。修改后技能状态会重置为 draft，需要重新测试。"
)
EDIT_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要修改的技能 ID"},
        "code": {"type": "string", "description": "新的 Python 代码"},
    },
    "required": ["skill_id", "code"],
    "additionalProperties": False,
}

LIST_SKILLS_DESCRIPTION = "查看你的技能列表，可以按状态或标签过滤。"
LIST_SKILLS_SCHEMA = {
    "type": "object",
    "properties": {
        "maturity": {
            "type": "string",
            "enum": ["draft", "testing", "stable", "broken"],
            "description": "按状态过滤（可选）",
        },
        "tag": {"type": "string", "description": "按标签过滤（可选）"},
    },
    "additionalProperties": False,
}

PROMOTE_SKILL_DESCRIPTION = (
    "将一个 testing 状态的技能标记为 stable。只有你确信技能足够稳定时才调用。"
    "stable 的技能会被注册为一等工具，可以在对话中直接调用。"
)
PROMOTE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要升级的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

DELETE_SKILL_DESCRIPTION = "删除一个技能。"
DELETE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要删除的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}


# ── Executors ────────────────────────────────────────────────────────

async def create_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "SkillStore 未挂载"},
            reason="create_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    name = str(request.arguments.get("name", "")).strip()
    description = str(request.arguments.get("description", "")).strip()
    code = str(request.arguments.get("code", "")).strip()

    if not all([skill_id, name, description, code]):
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "skill_id, name, description, code 都不能为空"},
            reason="create_skill 缺少必需参数",
        )

    dependencies = request.arguments.get("dependencies") or []
    tags = request.arguments.get("tags") or []

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
        )
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": str(exc)},
            reason=f"SkillStore.create 失败: {exc}",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "created": True,
            "skill_id": result["skill_id"],
            "file_path": result["file_path"],
            "maturity": "draft",
        },
    )


async def run_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    executor = services.get("skill_executor")
    if executor is None:
        return ToolExecutionResult(
            success=False,
            payload={"executed": False, "reason": "SkillExecutor 未挂载"},
            reason="run_skill 在没有 skill_executor 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"executed": False, "reason": "skill_id 不能为空"},
            reason="run_skill 缺少 skill_id",
        )

    arguments = request.arguments.get("arguments") or {}
    timeout = int(request.arguments.get("timeout", 30) or 30)
    timeout = max(1, min(timeout, 300))

    result = await executor.execute(skill_id, arguments=arguments, timeout=timeout)

    return ToolExecutionResult(
        success=result.success,
        payload={
            "executed": True,
            "skill_id": skill_id,
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
    )


async def edit_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "SkillStore 未挂载"},
            reason="edit_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    code = str(request.arguments.get("code", "")).strip()
    if not skill_id or not code:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "skill_id 和 code 不能为空"},
            reason="edit_skill 缺少参数",
        )

    result = store.update_code(skill_id, code)
    return ToolExecutionResult(
        success=result["success"],
        payload={"updated": result["success"], "skill_id": skill_id, "reason": result.get("reason", "")},
        reason=result.get("reason", ""),
    )


async def list_skills_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"skills": [], "reason": "SkillStore 未挂载"},
            reason="list_skills 在没有 skill_store 的上下文中被调用",
        )

    maturity = request.arguments.get("maturity")
    tag = request.arguments.get("tag")
    skills = store.list_skills(maturity=maturity, tag=tag)

    return ToolExecutionResult(
        success=True,
        payload={
            "skills": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "maturity": s["maturity"],
                    "usage_count": s.get("usage_count", 0),
                    "success_count": s.get("success_count", 0),
                    "tags": s.get("tags", []),
                }
                for s in skills
            ],
            "total": len(skills),
        },
    )


async def promote_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": "SkillStore 未挂载"},
            reason="promote_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": "skill_id 不能为空"},
            reason="promote_skill 缺少 skill_id",
        )

    skill = store.read(skill_id)
    if skill is None:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": f"技能 {skill_id} 不存在"},
            reason=f"技能 {skill_id} 不存在",
        )

    if skill["meta"]["maturity"] not in ("testing", "broken"):
        return ToolExecutionResult(
            success=False,
            payload={
                "promoted": False,
                "reason": f"只能从 testing/broken 升级到 stable，当前状态: {skill['meta']['maturity']}",
            },
            reason=f"promote_skill: 当前状态 {skill['meta']['maturity']} 不可升级",
        )

    store.update_meta(skill_id, maturity="stable")

    # Hot-register as a ToolSpec if tool_registry is available
    tool_registry = services.get("tool_registry")
    if tool_registry is not None:
        _register_skill_as_tool(tool_registry, store, services.get("skill_executor"), skill_id)

    return ToolExecutionResult(
        success=True,
        payload={
            "promoted": True,
            "skill_id": skill_id,
            "maturity": "stable",
        },
    )


async def delete_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"deleted": False, "reason": "SkillStore 未挂载"},
            reason="delete_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"deleted": False, "reason": "skill_id 不能为空"},
            reason="delete_skill 缺少 skill_id",
        )

    result = store.delete(skill_id)
    return ToolExecutionResult(
        success=result["success"],
        payload={"deleted": result["success"], "skill_id": skill_id, "reason": result.get("reason", "")},
        reason=result.get("reason", ""),
    )


# ── Dynamic tool registration ────────────────────────────────────────

def _register_skill_as_tool(tool_registry, skill_store, skill_executor, skill_id: str) -> None:
    skill = skill_store.read(skill_id)
    if skill is None:
        return
    meta = skill["meta"]

    async def _executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
        executor = (ctx.services or {}).get("skill_executor")
        if executor is None:
            return ToolExecutionResult(success=False, payload={}, reason="SkillExecutor 未挂载")
        result = await executor.execute(skill_id, arguments=req.arguments or {})
        return ToolExecutionResult(
            success=result.success,
            payload={"output": result.output, "error": result.error},
        )

    tool_registry.register(ToolSpec(
        name=skill_id,
        description=meta.get("description", ""),
        json_schema={"type": "object", "properties": {}, "additionalProperties": True},
        executor=_executor,
        capability="skill",
        risk_level="medium",
    ))


def register_skill_tools(tool_registry) -> None:
    """Register the 6 skill management tools into the registry."""
    tool_registry.register(ToolSpec(
        name="create_skill",
        description=CREATE_SKILL_DESCRIPTION,
        json_schema=CREATE_SKILL_SCHEMA,
        executor=create_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="run_skill",
        description=RUN_SKILL_DESCRIPTION,
        json_schema=RUN_SKILL_SCHEMA,
        executor=run_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="edit_skill",
        description=EDIT_SKILL_DESCRIPTION,
        json_schema=EDIT_SKILL_SCHEMA,
        executor=edit_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="list_skills",
        description=LIST_SKILLS_DESCRIPTION,
        json_schema=LIST_SKILLS_SCHEMA,
        executor=list_skills_executor,
        capability="skill",
        risk_level="low",
    ))
    tool_registry.register(ToolSpec(
        name="promote_skill",
        description=PROMOTE_SKILL_DESCRIPTION,
        json_schema=PROMOTE_SKILL_SCHEMA,
        executor=promote_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="delete_skill",
        description=DELETE_SKILL_DESCRIPTION,
        json_schema=DELETE_SKILL_SCHEMA,
        executor=delete_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
