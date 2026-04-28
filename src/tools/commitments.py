"""commit_promise / fulfill_promise / abandon_promise — Lapwing 承诺工具。

当 Lapwing 对用户做出"等我查一下 / 我去看看 / 帮你做X"这类需要工具执行
的承诺时，必须调用 ``commit_promise`` 登记。承诺有 deadline；超时未完成
会被 inner tick 巡检注入到下次 tick 的 prompt 里。

完成时调 ``fulfill_promise``，无法完成时调 ``abandon_promise`` 并说明
原因。两者都更新承诺状态，写 mutation log。
"""
from __future__ import annotations

import logging
import time

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)

logger = logging.getLogger("lapwing.tools.commitments")


# ── commit_promise ────────────────────────────────────────────────────

COMMIT_PROMISE_DESCRIPTION = (
    "登记一个你对用户的承诺。判断标准：这件事能不能在当前 turn 内完成？\n"
    "当前 turn 内能完成的小查询（一次 research、一次工具调用）→ 不需要 commit，直接做。\n"
    "需要跨 turn、较长等待、多步骤耗时、未来交付、或用户可以先离开的事情 → commit。\n"
    "例如：'查个比分' → 不 commit，直接 research；\n"
    "'调研一份报告' → commit；'今晚提醒我' → commit；'查完发你' → commit。\n"
    "deadline_minutes 默认 10 分钟，搜索类轻任务通常 5 分钟够用。"
)

COMMIT_PROMISE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "承诺内容的简短描述，用于稍后回看时识别这是什么任务",
        },
        "deadline_minutes": {
            "type": "integer",
            "description": "预计完成时间（分钟）。超过此时间未完成将被 inner tick 标记 overdue",
            "default": 10,
            "minimum": 1,
            "maximum": 1440,
        },
        "reasoning": {
            "type": "string",
            "description": "（可选）这个承诺的背景，比如用户上下文或自己的判断",
        },
    },
    "required": ["description"],
    "additionalProperties": False,
}


async def commit_promise_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    description = str(request.arguments.get("description", "")).strip()
    if not description:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "description 不能为空"},
            reason="commit_promise 缺少 description",
        )

    deadline_minutes = request.arguments.get("deadline_minutes", 10)
    try:
        deadline_minutes = int(deadline_minutes)
    except (TypeError, ValueError):
        deadline_minutes = 10
    deadline_minutes = max(1, min(deadline_minutes, 1440))

    reasoning = request.arguments.get("reasoning")
    if reasoning is not None:
        reasoning = str(reasoning).strip() or None

    services = context.services or {}
    store = services.get("commitment_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "CommitmentStore 未挂载"},
            reason="commit_promise 在没有 commitment_store 的上下文中被调用",
        )

    chat_id = context.chat_id or "_unknown"
    deadline = time.time() + deadline_minutes * 60.0
    source_id = int(services.get("last_tell_user_trajectory_id") or 0)

    try:
        promise_id = await store.create(
            chat_id,
            description,
            source_id,
            reasoning=reasoning,
            deadline=deadline,
            source_focus_id=context.focus_id,
        )
    except Exception as exc:
        logger.warning("commit_promise create 失败: %s", exc, exc_info=True)
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": str(exc)},
            reason=f"CommitmentStore.create 失败: {exc}",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "created": True,
            "promise_id": promise_id,
            "description": description,
            "deadline_epoch": deadline,
            "deadline_minutes": deadline_minutes,
        },
    )


# ── fulfill_promise ───────────────────────────────────────────────────

FULFILL_PROMISE_DESCRIPTION = (
    "标记一个承诺已完成。在你完成了承诺要做的事情之后调用——"
    "工具执行完了、结果已经告诉用户了，再调这个把"
    "承诺关掉。result_summary 是给未来的自己回看的简短记录。"
)

FULFILL_PROMISE_SCHEMA = {
    "type": "object",
    "properties": {
        "promise_id": {
            "type": "string",
            "description": "commit_promise 返回的 promise_id",
        },
        "result_summary": {
            "type": "string",
            "description": "完成结果的简短描述，例如 '查到道奇 4/20 22:00 vs 教士'",
        },
    },
    "required": ["promise_id", "result_summary"],
    "additionalProperties": False,
}


async def fulfill_promise_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    promise_id = str(request.arguments.get("promise_id", "")).strip()
    if not promise_id:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "promise_id 不能为空"},
            reason="fulfill_promise 缺少 promise_id",
        )

    result_summary = str(request.arguments.get("result_summary", "")).strip()
    if not result_summary:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "result_summary 不能为空"},
            reason="fulfill_promise 缺少 result_summary",
        )

    services = context.services or {}
    store = services.get("commitment_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "CommitmentStore 未挂载"},
            reason="fulfill_promise 在没有 commitment_store 的上下文中被调用",
        )

    from src.core.commitments import CommitmentStatus

    try:
        await store.set_status(
            promise_id,
            CommitmentStatus.FULFILLED.value,
            closing_note=result_summary,
        )
    except KeyError:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "找不到这个 promise_id"},
            reason=f"承诺 {promise_id} 不存在",
        )
    except Exception as exc:
        logger.warning("fulfill_promise 失败: %s", exc, exc_info=True)
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": str(exc)},
            reason=f"CommitmentStore.set_status 失败: {exc}",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "updated": True,
            "promise_id": promise_id,
            "status": "fulfilled",
            "result_summary": result_summary,
        },
    )


# ── abandon_promise ───────────────────────────────────────────────────

ABANDON_PROMISE_DESCRIPTION = (
    "放弃一个承诺。在你无法完成承诺要做的事情时调用——必须说明原因"
    "（reason），并且在调这个工具之前应该已经把放弃原因告诉用户了，"
    "不要默默放弃。"
)

ABANDON_PROMISE_SCHEMA = {
    "type": "object",
    "properties": {
        "promise_id": {
            "type": "string",
            "description": "commit_promise 返回的 promise_id",
        },
        "reason": {
            "type": "string",
            "description": "放弃原因，例如 '搜不到结果，已告诉用户'",
        },
    },
    "required": ["promise_id", "reason"],
    "additionalProperties": False,
}


async def abandon_promise_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    promise_id = str(request.arguments.get("promise_id", "")).strip()
    if not promise_id:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "promise_id 不能为空"},
            reason="abandon_promise 缺少 promise_id",
        )

    reason = str(request.arguments.get("reason", "")).strip()
    if not reason:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "reason 不能为空"},
            reason="abandon_promise 缺少 reason",
        )

    services = context.services or {}
    store = services.get("commitment_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "CommitmentStore 未挂载"},
            reason="abandon_promise 在没有 commitment_store 的上下文中被调用",
        )

    from src.core.commitments import CommitmentStatus

    try:
        await store.set_status(
            promise_id,
            CommitmentStatus.ABANDONED.value,
            closing_note=reason,
        )
    except KeyError:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "找不到这个 promise_id"},
            reason=f"承诺 {promise_id} 不存在",
        )
    except Exception as exc:
        logger.warning("abandon_promise 失败: %s", exc, exc_info=True)
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": str(exc)},
            reason=f"CommitmentStore.set_status 失败: {exc}",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "updated": True,
            "promise_id": promise_id,
            "status": "abandoned",
            "reason": reason,
        },
    )
