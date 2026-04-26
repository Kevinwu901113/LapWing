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
    "把一段可复用的代码保存为技能。保存后你可以随时通过 run_skill 执行它。"
    "新技能需要先测试成功才能升级。code 里必须有一个 def run(...) 作为入口。"
)
CREATE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "给技能起一个唯一 ID，比如 skill_weather_query"},
        "name": {"type": "string", "description": "技能的显示名称"},
        "description": {"type": "string", "description": "一句话说明这个技能做什么"},
        "code": {"type": "string", "description": "技能代码，必须包含 def run(...) 入口函数"},
        "dependencies": {
            "type": "array", "items": {"type": "string"},
            "description": "需要额外安装的库，比如 [\"requests\"]，不需要就不填",
        },
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "帮助搜索和分类的标签，不需要就不填",
        },
        "category": {
            "type": "string",
            "description": "技能所属分类（可选，不填默认 general）",
        },
        "derived_from": {
            "type": "string",
            "description": "如果这个技能是改进某个已有技能而来，填那个技能的 ID（可选）",
        },
    },
    "required": ["skill_id", "name", "description", "code"],
    "additionalProperties": False,
}

RUN_SKILL_DESCRIPTION = (
    "执行一个已有的技能，返回执行结果。如果执行失败，会返回错误信息。"
    "未验证的技能会在安全隔离环境中运行。"
)
RUN_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要执行的技能 ID"},
        "arguments": {
            "type": "object", "description": "传给技能的输入参数（可选）",
        },
        "timeout": {
            "type": "integer", "description": "最长等待时间，单位秒（可选，默认 30，最多 300）",
            "default": 30, "minimum": 1, "maximum": 300,
        },
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

EDIT_SKILL_DESCRIPTION = (
    "修改一个技能的代码。修改后需要重新测试才能升级使用。"
)
EDIT_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要修改的技能 ID"},
        "code": {"type": "string", "description": "替换后的完整代码"},
    },
    "required": ["skill_id", "code"],
    "additionalProperties": False,
}

LIST_SKILLS_DESCRIPTION = "查看你已有的所有技能，可以按状态或标签筛选。"
LIST_SKILLS_SCHEMA = {
    "type": "object",
    "properties": {
        "maturity": {
            "type": "string",
            "enum": ["draft", "testing", "stable", "broken"],
            "description": "只看某个状态的技能，比如 stable 表示已验证可用（可选）",
        },
        "tag": {"type": "string", "description": "只看带某个标签的技能（可选）"},
    },
    "additionalProperties": False,
}

PROMOTE_SKILL_DESCRIPTION = (
    "将一个经过充分测试的技能升级为正式能力。升级后你可以直接使用它，不需要再通过 run_skill 调用。"
    "只在你确认技能稳定可靠时使用。"
)
PROMOTE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要升级的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

DELETE_SKILL_DESCRIPTION = "永久删除一个技能。删除后无法恢复。"
DELETE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要删除的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

SEARCH_SKILL_DESCRIPTION = (
    "搜索可以学习的新技能，也可以查找你已有的技能。返回匹配的技能名称和简介。"
)
SEARCH_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "想要找什么样的技能，用关键词描述"},
        "source": {
            "type": "string",
            "enum": ["local", "web", "all"],
            "description": "在哪里搜索：local 只找已有的 / web 只在网上找 / all 都找（可选，默认 all）",
            "default": "all",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

INSTALL_SKILL_DESCRIPTION = (
    "从网上安装一个新技能到本地。安装后可以通过 run_skill 测试使用。"
)
INSTALL_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "source_url": {"type": "string", "description": "技能文件的下载地址"},
        "skill_id": {"type": "string", "description": "给这个技能起一个本地 ID，比如 skill_translator"},
    },
    "required": ["source_url", "skill_id"],
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
    category = str(request.arguments.get("category", "general")).strip() or "general"
    derived_from = request.arguments.get("derived_from")

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
            category=category,
            derived_from=derived_from,
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


# ── Approval gate for run_skill ──────────────────────────────────────────
#
# Profiles where run_skill is exposed but must NOT execute arbitrary skills
# without an explicit maturity/tag check. Two-tier policy:
#   - chat_extended: only stable skills whose trust_required is satisfied
#                    by the caller's auth_level
#   - inner_tick:    only stable skills explicitly marked autonomous via
#                    tags "auto_run" or "inner_tick"
# Any other profile (CLI / OWNER tooling) bypasses the gate — those
# surfaces have their own access controls.

_GATED_PROFILES = frozenset({"chat_extended", "inner_tick"})
_AUTONOMOUS_TAGS = frozenset({"auto_run", "inner_tick"})

# auth_level integers — keep aligned with src.core.authority_gate.AuthLevel
# (0=GUEST / 1=TRUSTED / 2=OWNER).
_TRUST_RANK = {"guest": 0, "trusted": 1, "owner": 2}


def _trust_satisfied(required: str, auth_level: int) -> bool:
    """Return True if auth_level meets or exceeds the required trust tier."""
    needed = _TRUST_RANK.get(str(required or "guest").lower(), 0)
    return int(auth_level) >= needed


def _gate_run_skill(
    skill: dict | None,
    *,
    profile: str,
    auth_level: int,
) -> str | None:
    """Return a deny reason if the gate refuses execution, else None.

    The gate fires only on profiles in ``_GATED_PROFILES``. Non-gated
    profiles return None (allowed) regardless of skill state.
    """
    if profile not in _GATED_PROFILES:
        return None
    if skill is None:
        return "技能不存在"
    meta = skill.get("meta") or {}
    maturity = str(meta.get("maturity", "")).lower()
    if maturity != "stable":
        return (
            f"run_skill 在 {profile} 下只能执行 maturity=stable 的技能，"
            f"当前技能状态为 {maturity or 'unmarked'}"
        )
    if profile == "chat_extended":
        required = meta.get("trust_required", "guest")
        if not _trust_satisfied(required, auth_level):
            return (
                f"run_skill 拒绝执行：技能要求 trust_required={required}，"
                f"当前 auth_level={auth_level} 不满足"
            )
        return None
    if profile == "inner_tick":
        tags = {str(t).lower() for t in (meta.get("tags") or [])}
        if not tags & _AUTONOMOUS_TAGS:
            return (
                "run_skill 拒绝执行：inner_tick 只能运行明确标记 auto_run "
                "或 inner_tick 的稳定技能"
            )
        return None
    return None


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

    profile = (context.runtime_profile or "").strip()
    if profile in _GATED_PROFILES:
        store = services.get("skill_store")
        skill = store.read(skill_id) if store is not None else None
        deny_reason = _gate_run_skill(
            skill,
            profile=profile,
            auth_level=context.auth_level,
        )
        if deny_reason is not None:
            logger.info(
                "[run_skill] gate denied: profile=%s skill_id=%s reason=%s",
                profile, skill_id, deny_reason,
            )
            return ToolExecutionResult(
                success=False,
                payload={
                    "executed": False,
                    "skill_id": skill_id,
                    "denied": True,
                    "profile": profile,
                    "reason": deny_reason,
                },
                reason=deny_reason,
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


async def search_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "reason": "SkillStore 未挂载"},
            reason="search_skill: SkillStore 不可用",
        )

    query = str(request.arguments.get("query", "")).strip()
    source = str(request.arguments.get("source", "all")).strip()
    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "reason": "query 不能为空"},
            reason="search_skill: 缺少 query",
        )

    results = []

    # 本地技能搜索
    if source in ("local", "all"):
        query_lower = query.lower()
        for skill_meta in store.get_skill_index():
            text = f"{skill_meta.get('name', '')} {skill_meta.get('description', '')} {' '.join(skill_meta.get('tags', []))}".lower()
            if query_lower in text:
                results.append({
                    "source": "local",
                    "id": skill_meta["id"],
                    "name": skill_meta.get("name", ""),
                    "description": skill_meta.get("description", ""),
                    "maturity": skill_meta.get("maturity", ""),
                    "tags": skill_meta.get("tags", []),
                })

    # 网络搜索（仅在本地无结果时触发）
    if source in ("web", "all") and not results:
        research_engine = services.get("research_engine")
        tavily = getattr(research_engine, "tavily", None) if research_engine else None
        if tavily is not None:
            try:
                web_results = await tavily.search(
                    f"{query} agent skill SKILL.md github",
                    max_results=5,
                )
                for item in web_results:
                    results.append({
                        "source": "web",
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                    })
            except Exception as exc:
                logger.warning("search_skill web search failed: %s", exc)

    return ToolExecutionResult(
        success=True,
        payload={"results": results, "query": query, "source": source},
    )


_MAX_SKILL_DOWNLOAD_BYTES = 512 * 1024


async def _fetch_skill_content(url: str) -> str:
    """从 URL 下载 SKILL.md 内容。调用前应先校验 URL 安全性。"""
    import httpx
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_length = resp.headers.get("content-length")
        if content_length and int(content_length) > _MAX_SKILL_DOWNLOAD_BYTES:
            raise ValueError(f"响应过大（{content_length} 字节，上限 {_MAX_SKILL_DOWNLOAD_BYTES}）")
        text = resp.text
        if len(text.encode("utf-8")) > _MAX_SKILL_DOWNLOAD_BYTES:
            raise ValueError(f"响应过大（超过 {_MAX_SKILL_DOWNLOAD_BYTES} 字节）")
        return text


async def install_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "SkillStore 未挂载"},
            reason="install_skill: SkillStore 不可用",
        )

    source_url = str(request.arguments.get("source_url", "")).strip()
    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not source_url or not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "source_url 和 skill_id 不能为空"},
            reason="install_skill: 缺少参数",
        )

    # SSRF 防护：校验 URL
    from src.tools.personal_tools import _check_browse_safety
    safety = _check_browse_safety(source_url)
    if not safety["allowed"]:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": f"URL 被拒绝: {safety['reason']}"},
            reason=f"install_skill: URL 被拒绝: {safety['reason']}",
        )

    # 下载 SKILL.md
    try:
        content = await _fetch_skill_content(source_url)
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": f"下载失败: {exc}"},
            reason=f"install_skill: 下载失败: {exc}",
        )

    # 解析文件（静态方法，直接通过类调用）
    from src.skills.skill_store import SkillStore
    meta, body = SkillStore._parse(content)
    if meta is None:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "无法解析 SKILL.md 格式"},
            reason="install_skill: SKILL.md 解析失败",
        )

    code = SkillStore._extract_code(body)

    # 写入技能
    name = meta.get("name", skill_id)
    description = meta.get("description", "")
    dependencies = meta.get("dependencies", [])
    tags = meta.get("tags", [])
    category = meta.get("category", "general")

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
            category=category,
            origin="installed",
            source_url=source_url,
        )
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": str(exc)},
            reason=f"install_skill: 写入失败: {exc}",
        )

    # 安装完成后将状态设为 testing（而非 draft）
    try:
        store.update_meta(skill_id, maturity="testing")
    except Exception as exc:
        logger.warning("install_skill: update_meta 失败，回滚 %s: %s", skill_id, exc)
        store.delete(skill_id)
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "设置 maturity 失败，已回滚"},
            reason="install_skill: update_meta 失败",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "installed": True,
            "skill_id": result["skill_id"],
            "name": name,
            "source_url": source_url,
            "maturity": "testing",
        },
    )


# ── Dynamic tool registration ────────────────────────────────────────

# TODO(agent-team): promote_skill 注册的工具使用空 schema，LLM 无法获得参数信息。
# 根本原因：SkillStore.create 未捕获 run() 的参数签名。
# 修复：创建时通过 inspect.signature 提取 schema 并存入 meta。
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
    """Register the 8 skill management tools into the registry."""
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
    tool_registry.register(ToolSpec(
        name="search_skill",
        description=SEARCH_SKILL_DESCRIPTION,
        json_schema=SEARCH_SKILL_SCHEMA,
        executor=search_skill_executor,
        capability="skill",
        risk_level="low",
    ))
    tool_registry.register(ToolSpec(
        name="install_skill",
        description=INSTALL_SKILL_DESCRIPTION,
        json_schema=INSTALL_SKILL_SCHEMA,
        executor=install_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
