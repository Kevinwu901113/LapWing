"""环境知识工具——预取、查询、兴趣画像管理。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.ambient.models import AmbientEntry
from src.research.types import normalize_confidence
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.ambient_tools")

# ── TTL 默认值（小时）──────────────────────────────────────────────

_DEFAULT_TTL: dict[str, int] = {
    "sports": 6,
    "weather": 3,
    "news": 4,
    "calendar": 12,
}
_FALLBACK_TTL = 6


# ═══════════════════════════════════════════════════════════════════
# prepare_ambient_knowledge
# ═══════════════════════════════════════════════════════════════════

async def prepare_ambient_knowledge_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    topic = str(req.arguments.get("topic", "")).strip()
    if not topic:
        return ToolExecutionResult(
            success=False,
            payload={"error": "topic 不能为空"},
            reason="missing topic",
        )

    category = str(req.arguments.get("category", "")).strip() or topic
    ttl_hours = req.arguments.get("ttl_hours")
    if ttl_hours is None:
        cat_lower = category.lower()
        ttl_hours = next(
            (v for k, v in _DEFAULT_TTL.items() if k in cat_lower),
            _FALLBACK_TTL,
        )
    ttl_hours = int(ttl_hours)

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    engine = svc.research_engine
    if engine is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "research_engine 未注入"},
            reason="research_engine_unavailable",
        )

    ambient_store = svc.ambient_store
    if ambient_store is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "ambient_store 未注入"},
            reason="ambient_store_unavailable",
        )

    try:
        result = await engine.research(topic, scope="auto")
    except Exception as exc:
        logger.warning("prepare_ambient_knowledge 搜索失败 topic=%r: %s", topic[:80], exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"搜索失败：{exc}"},
            reason=str(exc),
        )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    key = f"interest:{category}"

    evidence_dicts = []
    try:
        evidence_dicts = [ev.to_dict() for ev in result.evidence]
    except Exception:
        pass
    confidence = normalize_confidence(getattr(result, "confidence", 0.0))

    if confidence <= 0.3 and not evidence_dicts:
        logger.info(
            "prepare_ambient_knowledge 跳过缓存：低置信且无证据 topic=%r confidence=%.2f",
            topic[:80],
            confidence,
        )
        return ToolExecutionResult(
            success=True,
            payload={
                "answer": result.answer,
                "cached": False,
                "confidence": confidence,
                "reason": "low_confidence_no_evidence",
            },
            reason="low_confidence_no_evidence",
        )

    entry = AmbientEntry(
        key=key,
        category=category,
        topic=topic,
        data=json.dumps({
            "answer": result.answer,
            "evidence": evidence_dicts,
            "confidence": confidence,
            "backends": result.search_backend_used,
        }, ensure_ascii=False),
        summary=result.answer[:300] if result.answer else "",
        fetched_at=now.isoformat(),
        expires_at=expires.isoformat(),
        source="research_engine",
        confidence=confidence,
    )

    try:
        await ambient_store.put(key, entry)
    except Exception as exc:
        logger.warning("ambient_store.put 失败: %s", exc)
        return ToolExecutionResult(
            success=True,
            payload={
                "answer": result.answer,
                "cached": False,
                "reason": f"缓存写入失败: {exc}",
            },
            reason="research_ok_cache_fail",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "answer": result.answer,
            "cached": True,
            "category": category,
            "expires_at": expires.isoformat(),
            "confidence": confidence,
        },
        reason=f"cached as {key}",
    )


# ═══════════════════════════════════════════════════════════════════
# check_ambient_knowledge
# ═══════════════════════════════════════════════════════════════════

async def check_ambient_knowledge_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    category = str(req.arguments.get("category", "")).strip()
    topic = str(req.arguments.get("topic", "")).strip()

    if not category and not topic:
        return ToolExecutionResult(
            success=False,
            payload={"error": "category 或 topic 至少填一个"},
            reason="missing params",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    ambient_store = svc.ambient_store
    if ambient_store is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "ambient_store 未注入"},
            reason="ambient_store_unavailable",
        )

    entries: list[Any] = []

    if category:
        try:
            entries = list(await ambient_store.get_by_category(category))
        except Exception as exc:
            logger.warning("get_by_category 失败: %s", exc)

    if not entries and topic:
        try:
            all_fresh = await ambient_store.get_all_fresh()
            topic_lower = topic.lower()
            entries = [
                e for e in all_fresh
                if topic_lower in e.topic.lower() or topic_lower in e.category.lower()
            ]
        except Exception as exc:
            logger.warning("get_all_fresh 失败: %s", exc)

    if not entries:
        return ToolExecutionResult(
            success=True,
            payload={"entries": [], "message": "无匹配的缓存数据"},
            reason="no_match",
        )

    now = datetime.now(timezone.utc)
    results = []
    for e in entries:
        try:
            fetched_dt = datetime.fromisoformat(e.fetched_at)
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
            age_hours = (now - fetched_dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            age_hours = -1.0

        results.append({
            "key": e.key,
            "category": e.category,
            "topic": e.topic,
            "summary": e.summary,
            "age_hours": round(age_hours, 1),
            "confidence": e.confidence,
        })

    return ToolExecutionResult(
        success=True,
        payload={"entries": results},
        reason=f"found {len(results)} entries",
    )


# ═══════════════════════════════════════════════════════════════════
# manage_interest_profile
# ═══════════════════════════════════════════════════════════════════

async def manage_interest_profile_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    from src.ambient.models import Interest

    action = str(req.arguments.get("action", "")).strip()
    if action not in ("view", "add", "update", "deactivate"):
        return ToolExecutionResult(
            success=False,
            payload={"error": "action 必须是 view/add/update/deactivate"},
            reason="invalid action",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    profile = svc.interest_profile
    if profile is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "interest_profile 未注入"},
            reason="interest_profile_unavailable",
        )

    if action == "view":
        interests = profile.load()
        items = []
        for i in interests:
            items.append({
                "name": i.name,
                "priority": i.priority,
                "details": i.details,
                "frequency": i.frequency,
                "typical_time": i.typical_time,
                "active": i.active,
            })
        return ToolExecutionResult(
            success=True,
            payload={"interests": items, "count": len(items)},
            reason=f"{len(items)} interests",
        )

    name = str(req.arguments.get("name", "")).strip()
    if not name:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"action={action} 需要 name 参数"},
            reason="missing name",
        )

    interests = profile.load()

    if action == "add":
        for existing in interests:
            if existing.name == name:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"兴趣 '{name}' 已存在，用 update 修改"},
                    reason="duplicate",
                )
        new_interest = Interest(
            name=name,
            priority=str(req.arguments.get("priority", "medium")).strip(),
            details=str(req.arguments.get("details", "")).strip(),
            frequency=str(req.arguments.get("frequency", "weekly")).strip(),
            typical_time=str(req.arguments.get("typical_time", "anytime")).strip(),
            source=str(req.arguments.get("source", "观察")).strip(),
            notes=str(req.arguments.get("notes", "")).strip(),
            active=True,
        )
        interests.append(new_interest)
        profile.save(interests)
        return ToolExecutionResult(
            success=True,
            payload={"added": name, "priority": new_interest.priority},
            reason=f"added {name}",
        )

    if action == "update":
        found = False
        updated: list[Interest] = []
        for i in interests:
            if i.name == name:
                found = True
                # 用传入的参数覆盖已有值，未传的保持原样
                updated.append(Interest(
                    name=name,
                    priority=str(req.arguments.get("priority", i.priority)).strip(),
                    details=str(req.arguments.get("details", i.details)).strip(),
                    frequency=str(req.arguments.get("frequency", i.frequency)).strip(),
                    typical_time=str(req.arguments.get("typical_time", i.typical_time)).strip(),
                    source=i.source,
                    notes=str(req.arguments.get("notes", i.notes)).strip(),
                    active=i.active,
                ))
            else:
                updated.append(i)
        if not found:
            return ToolExecutionResult(
                success=False,
                payload={"error": f"未找到兴趣 '{name}'"},
                reason="not_found",
            )
        profile.save(updated)
        return ToolExecutionResult(
            success=True,
            payload={"updated": name},
            reason=f"updated {name}",
        )

    # deactivate
    found = False
    updated_list: list[Interest] = []
    for i in interests:
        if i.name == name:
            found = True
            updated_list.append(Interest(
                name=i.name,
                priority=i.priority,
                details=i.details,
                frequency=i.frequency,
                typical_time=i.typical_time,
                source=i.source,
                notes=i.notes,
                active=False,
            ))
        else:
            updated_list.append(i)
    if not found:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"未找到兴趣 '{name}'"},
            reason="not_found",
        )
    profile.save(updated_list)
    return ToolExecutionResult(
        success=True,
        payload={"deactivated": name},
        reason=f"deactivated {name}",
    )


# ═══════════════════════════════════════════════════════════════════
# 注册
# ═══════════════════════════════════════════════════════════════════

def register_ambient_tools(registry: Any) -> None:
    """注册环境知识相关的 3 个工具。"""
    registry.register(ToolSpec(
        name="prepare_ambient_knowledge",
        description=(
            "预取信息并存入环境知识缓存。在你认为某个话题需要更新数据时使用。\n"
            "内部调用搜索引擎获取最新信息，结果自动缓存。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "要预取的话题，如「道奇今天比赛结果」「洛杉矶天气」",
                },
                "category": {
                    "type": "string",
                    "description": "分类标签（默认=topic），如「MLB棒球」「天气」。后续按此分类查询。",
                },
                "ttl_hours": {
                    "type": "integer",
                    "description": "缓存有效期（小时）。默认按类别自动设置（sports=6, weather=3, news=4）。",
                },
            },
            "required": ["topic"],
        },
        executor=prepare_ambient_knowledge_executor,
        capability="general",
        risk_level="low",
        max_result_tokens=2000,
    ))

    registry.register(ToolSpec(
        name="check_ambient_knowledge",
        description=(
            "查看已缓存的环境知识。在回答问题前，先检查是否已有数据。\n"
            "按 category 或 topic 关键词检索。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "分类标签，如「MLB棒球」「天气」「新闻」",
                },
                "topic": {
                    "type": "string",
                    "description": "话题关键词（模糊匹配）",
                },
            },
        },
        executor=check_ambient_knowledge_executor,
        capability="general",
        risk_level="low",
        max_result_tokens=1500,
    ))

    registry.register(ToolSpec(
        name="manage_interest_profile",
        description=(
            "查看或更新 Kevin 的兴趣画像。\n"
            "用于记录新兴趣、调整优先级、或标记兴趣失效。\n"
            "在观察到 Kevin 反复关注新话题、或已有兴趣长时间未提及时使用。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["view", "add", "update", "deactivate"],
                    "description": "操作类型",
                },
                "name": {
                    "type": "string",
                    "description": "兴趣名称（add/update/deactivate 必填）",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "优先级（add/update 可选）",
                },
                "details": {
                    "type": "string",
                    "description": "具体关注点（add/update 可选）",
                },
                "frequency": {
                    "type": "string",
                    "enum": ["daily", "weekly", "event_driven"],
                    "description": "频率（add/update 可选）",
                },
                "typical_time": {
                    "type": "string",
                    "enum": ["morning", "evening", "anytime"],
                    "description": "典型时段（add/update 可选）",
                },
                "notes": {
                    "type": "string",
                    "description": "备注（add/update 可选）",
                },
            },
            "required": ["action"],
        },
        executor=manage_interest_profile_executor,
        capability="general",
        risk_level="low",
        max_result_tokens=1000,
    ))
