"""research 工具：把 search + fetch + refine 封装成单一调用。"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.research_tool")


async def research_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    question = str(req.arguments.get("question", "")).strip()
    scope = str(req.arguments.get("scope", "auto")).strip() or "auto"

    if not question:
        return ToolExecutionResult(
            success=False,
            payload={"error": "question 不能为空"},
            reason="missing question",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    engine = svc.research_engine
    if engine is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "research_engine 未注入"},
            reason="research_engine_unavailable",
        )

    try:
        result = await engine.research(question, scope=scope)
    except Exception as exc:
        logger.warning("research 执行失败 question=%r: %s", question[:80], exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"research 失败：{exc}"},
            reason=str(exc),
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "answer": result.answer,
            "evidence": [ev.to_dict() for ev in result.evidence],
            "confidence": result.confidence,
            "unclear": result.unclear,
            "backends": result.search_backend_used,
        },
        reason=f"confidence={result.confidence}",
    )


def register_research_tool(registry: Any) -> None:
    """把 research 工具注册到 ToolRegistry。"""
    registry.register(ToolSpec(
        name="research",
        description=(
            "回答需要查找信息的问题。输入问题，返回综合答案+证据+置信度。\n"
            "会自动搜索国内外来源、阅读多个网页、综合答案。你不需要自己解析网页。\n"
            "适用于：体育比分、新闻、天气、人物/公司信息、技术文档、任何事实性问题。\n"
            "不适用于：调研长报告（用 delegate 给团队）、你本来就知道的常识。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用自然语言写出你要回答的问题。比如『道奇今天比赛几比几』",
                },
                "scope": {
                    "type": "string",
                    "enum": ["auto", "global", "cn", "both"],
                    "default": "auto",
                    "description": "搜索范围。一般用 auto。强制海外用 global，强制国内用 cn。",
                },
            },
            "required": ["question"],
        },
        executor=research_executor,
        capability="web",
        risk_level="low",
        max_result_tokens=2500,
    ))
