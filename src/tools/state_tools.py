"""state_tools — read_state / update_state / read_fact ToolSpec registrations.

Façade entrypoints that replace the granular reminder/promise/focus/correction/
note/capability tools on the cognitive main surface (blueprint §11.1).
The granular tools STAY DEFINED in personal_tools / commitments / etc., and
remain available in INNER_TICK_PROFILE and LOCAL_EXECUTION_PROFILE — only
STANDARD_PROFILE switches to these three façade tools.

See docs/architecture/lapwing_v1_blueprint.md §11.
"""
from __future__ import annotations

import logging
from typing import Any

from src.lapwing_kernel.state_facade import (
    FACT_SCOPES,
    STATE_SCOPES,
    read_fact,
    read_state,
    update_state,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.state_tools")


async def _read_state_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext
) -> ToolExecutionResult:
    scope = req.arguments.get("scope", "")
    query = req.arguments.get("query") or {}
    payload = await read_state(scope=scope, query=query, services=ctx.services)
    return ToolExecutionResult(success=payload.get("status") == "ok", payload=payload)


async def _update_state_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext
) -> ToolExecutionResult:
    scope = req.arguments.get("scope", "")
    op = req.arguments.get("op", "")
    value = req.arguments.get("value") or {}
    payload = await update_state(
        scope=scope, op=op, value=value, services=ctx.services
    )
    return ToolExecutionResult(success=payload.get("status") == "ok", payload=payload)


async def _read_fact_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext
) -> ToolExecutionResult:
    scope = req.arguments.get("scope", "")
    query = req.arguments.get("query") or {}
    payload = await read_fact(scope=scope, query=query, services=ctx.services)
    return ToolExecutionResult(success=payload.get("status") == "ok", payload=payload)


def register_state_tools(registry: Any) -> None:
    """Register the three façade tools on the given ToolRegistry."""

    registry.register(
        ToolSpec(
            name="read_state",
            description=(
                "读取自身状态。scope 是一个命名空间字符串(reminder / focus / agents / "
                "identity / datetime / note / promise / correction / capability),query "
                "可选用于细化过滤。返回 {status, scope, value} 或 not_yet_routed 提示。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": f"状态命名空间。已知:{sorted(STATE_SCOPES)}",
                        "enum": sorted(STATE_SCOPES),
                    },
                    "query": {
                        "type": "object",
                        "description": "查询参数(可选,scope 决定形状)",
                    },
                },
                "required": ["scope"],
            },
            executor=_read_state_executor,
            capability="general",
            risk_level="low",
            max_result_tokens=600,
        )
    )

    registry.register(
        ToolSpec(
            name="update_state",
            description=(
                "更新自身状态。scope 是命名空间,op 是动词(add / cancel / close / "
                "commit / fulfill / abandon / set),value 是 op 需要的数据。"
                "替代旧的 set_reminder / cancel_reminder / commit_promise / "
                "fulfill_promise / abandon_promise / close_focus / add_correction 等。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": f"状态命名空间。已知:{sorted(STATE_SCOPES)}",
                        "enum": sorted(STATE_SCOPES),
                    },
                    "op": {
                        "type": "string",
                        "description": "动词(add / cancel / close / commit / fulfill / abandon / set)",
                    },
                    "value": {
                        "type": "object",
                        "description": "op 需要的数据(scope+op 决定 shape)",
                    },
                },
                "required": ["scope", "op"],
            },
            executor=_update_state_executor,
            capability="general",
            risk_level="medium",
            max_result_tokens=300,
        )
    )

    registry.register(
        ToolSpec(
            name="read_fact",
            description=(
                "读取事实源(append-only 历史)。scope: wiki / eventlog / trajectory。"
                "EventLog 不被默认注入 prompt,需要显式查询(blueprint §9 / §15.2 I-5)。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": f"事实源命名空间。已知:{sorted(FACT_SCOPES)}",
                        "enum": sorted(FACT_SCOPES),
                    },
                    "query": {
                        "type": "object",
                        "description": (
                            "查询参数(可选)。eventlog 支持 type_prefix / resource / "
                            "actor / outcome / limit。trajectory 支持 limit。"
                        ),
                    },
                },
                "required": ["scope"],
            },
            executor=_read_fact_executor,
            capability="general",
            risk_level="low",
            max_result_tokens=800,
        )
    )

    logger.info("Registered state facade tools: read_state, update_state, read_fact")
