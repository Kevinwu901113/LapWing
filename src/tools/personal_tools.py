"""个人工具集：Lapwing 日常行动的核心工具，Phase 4 重铸版。

涵盖：时间感知、消息发送、图片发送/读图、网络搜索/抓取、浏览器一次性访问、委托占位符。
每个工具遵循五项标准：简单参数、自足结果、有意义的错误、结果体积控制、可预期副作用。
"""

from __future__ import annotations

import ipaddress
import inspect
import logging
import re
import time
import urllib.parse
from typing import Any

from src.core.time_utils import local_timezone_name, now as local_now
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.personal_tools")

# 星期中文映射
_WEEKDAY_ZH = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_time
# ─────────────────────────────────────────────────────────────────────────────

async def _get_time(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """返回默认本地时间。"""
    now = local_now()
    return ToolExecutionResult(
        success=True,
        payload={
            "time": now.strftime("%Y年%m月%d日 %H:%M:%S"),
            "weekday": _WEEKDAY_ZH[now.weekday()],
            "timezone": local_timezone_name(),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. send_message
# ─────────────────────────────────────────────────────────────────────────────

_PROACTIVE_PROFILES = frozenset({"inner_tick", "compose_proactive"})
_FACTUAL_CLAIM_KEYWORDS = (
    "刚搜到", "刚查到", "最新", "现在",
    "比分", "领先", "赢了", "输了",
    "股价", "汇率",
)
_SOFTENED_CACHE_PHRASES = (
    "缓存", "之前查到", "之前的信息", "此前查到", "上次查到",
)


def _contains_factual_claim(text: str) -> bool:
    return any(keyword in text for keyword in _FACTUAL_CLAIM_KEYWORDS)


def _uses_softened_cache_wording(text: str) -> bool:
    return any(phrase in text for phrase in _SOFTENED_CACHE_PHRASES)


async def _has_fresh_external_fact_evidence(ctx: ToolExecutionContext) -> bool:
    from src.core.tool_dispatcher import ServiceContextView
    from src.logging.state_mutation_log import MutationType, current_iteration_id

    svc = ServiceContextView(ctx.services or {})
    mutation_log = svc.mutation_log
    iteration_id = current_iteration_id()
    if mutation_log is None or not iteration_id:
        return False

    try:
        rows = await mutation_log.query_by_iteration(iteration_id)
    except Exception:
        logger.debug("[send_message] factual gate mutation lookup failed", exc_info=True)
        return False

    cutoff = time.time() - 60
    for row in rows:
        if getattr(row, "event_type", "") != MutationType.TOOL_RESULT.value:
            continue
        if float(getattr(row, "timestamp", 0.0) or 0.0) < cutoff:
            continue
        payload = getattr(row, "payload", {}) or {}
        if payload.get("tool_name") not in {"research", "get_sports_score"}:
            continue
        result_payload = payload.get("payload") or {}
        if isinstance(result_payload, dict) and result_payload.get("cache_hit") is True:
            continue
        if str(payload.get("reason", "")).startswith("ambient_cache_hit:"):
            continue
        return True
    return False


async def _factual_claim_gate(content: str, ctx: ToolExecutionContext) -> ToolExecutionResult | None:
    if not _contains_factual_claim(content):
        return None
    if _uses_softened_cache_wording(content):
        return None
    if await _has_fresh_external_fact_evidence(ctx):
        return None
    return ToolExecutionResult(
        success=False,
        payload={
            "sent": False,
            "error": (
                "文案包含强事实声明，但当前 turn 内没有真实外部检索证据。"
                "请改写为“之前缓存的信息显示”或先调用 research/get_sports_score。"
            ),
            "content": content,
        },
        reason="factual_claim_requires_fresh_search",
    )


async def _record_proactive_decision(
    *,
    ctx: ToolExecutionContext,
    decision,
    target: str,
    target_chat_id: str | None = None,
    category: str | None,
    urgent: bool,
) -> None:
    """Emit a PROACTIVE_MESSAGE_DECISION mutation log entry. Best-effort —
    errors are logged at warning and swallowed; the user-visible decision
    path is unaffected."""
    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    mutation_log = svc.require_mutation_log_optional()
    if mutation_log is None:
        return
    try:
        from src.logging.state_mutation_log import (
            MutationType,
            current_chat_id,
            current_iteration_id,
        )
        await mutation_log.record(
            MutationType.PROACTIVE_MESSAGE_DECISION,
            {
                "decision": decision.decision,
                "reason": decision.reason,
                "category": category,
                "urgent": bool(urgent),
                "bypassed": bool(getattr(decision, "bypassed", False)),
                "target": target,
                "target_chat_id": target_chat_id,
                "runtime_profile": ctx.runtime_profile or "",
            },
            iteration_id=current_iteration_id(),
            chat_id=current_chat_id() or (ctx.chat_id or None),
        )
    except Exception:
        logger.warning(
            "[send_message] PROACTIVE_MESSAGE_DECISION record failed",
            exc_info=True,
        )


def _is_proactive_context(ctx: ToolExecutionContext) -> bool:
    """send_message is always proactive in the current architecture: bare
    assistant text is the direct-reply path, so any send_message call is
    a cross-channel or autonomous outbound. We still gate explicitly on
    the inner_tick profile + any context flagged ``proactive=True`` in
    services, so future direct-reply paths (if added) can opt out.
    """
    if (ctx.runtime_profile or "") in _PROACTIVE_PROFILES:
        return True
    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    if svc.proactive_send_active:
        return True
    return False


def _resolve_proactive_target_chat_id(
    target: str,
    ctx: ToolExecutionContext,
) -> str | None:
    """Map a send_message target to the canonical chat_id used by inbound routing.

    Returns None when the mapping cannot be resolved. Callers must handle None
    by skipping trajectory write (not by skipping the send itself).
    """
    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})

    if target == "kevin_qq":
        owner_qq_id = svc.owner_qq_id
        if owner_qq_id:
            return str(owner_qq_id)
        return None

    if target == "kevin_desktop":
        from config.settings import DESKTOP_DEFAULT_OWNER, OWNER_IDS
        if DESKTOP_DEFAULT_OWNER and OWNER_IDS:
            return next(iter(OWNER_IDS))
        return None

    if target.startswith("qq_group:"):
        group_id = target.split(":", 1)[1].strip()
        return group_id or None

    return None


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _build_proactive_gate_context(
    *,
    ctx: ToolExecutionContext,
    target_chat_id: str | None,
):
    from src.core.proactive_message_gate import ProactiveGateContext
    from src.core.tool_dispatcher import ServiceContextView

    if not target_chat_id:
        return ProactiveGateContext(target_chat_id=None)

    svc = ServiceContextView(ctx.services or {})
    latest_user_at = None
    latest_assistant_at = None

    tracker = svc.chat_activity_tracker
    if tracker is not None:
        try:
            snapshot = tracker.snapshot(target_chat_id)
            latest_user_at = snapshot.latest_user_message_at
            latest_assistant_at = snapshot.latest_assistant_reply_at
        except Exception:
            logger.debug("[send_message] chat activity snapshot failed", exc_info=True)

    trajectory_store = svc.trajectory_store
    latest_method = getattr(trajectory_store, "latest_chat_turn_times", None)
    if callable(latest_method):
        try:
            result = await _maybe_await(latest_method(target_chat_id))
            if isinstance(result, tuple) and len(result) == 2:
                traj_user, traj_assistant = result
                latest_user_at = _max_timeish(latest_user_at, traj_user)
                latest_assistant_at = _max_timeish(latest_assistant_at, traj_assistant)
        except Exception:
            logger.debug("[send_message] trajectory latest-turn lookup failed", exc_info=True)

    pending_user_message = False
    event_queue = svc.event_queue
    pending_method = getattr(event_queue, "has_user_message_for_chat", None)
    if callable(pending_method):
        try:
            pending_user_message = bool(pending_method(target_chat_id))
        except Exception:
            logger.debug("[send_message] event_queue pending check failed", exc_info=True)

    active_user_turn = False
    stuck_user_turn = False
    main_loop = svc.main_loop
    active_method = getattr(main_loop, "is_handling_foreground_user_turn", None)
    if callable(active_method):
        try:
            active_user_turn = bool(active_method(target_chat_id))
        except Exception:
            logger.debug("[send_message] main_loop active-turn check failed", exc_info=True)
    stuck_method = getattr(main_loop, "has_stuck_user_turn", None)
    if callable(stuck_method):
        try:
            stuck_user_turn = bool(stuck_method(target_chat_id))
        except Exception:
            logger.debug("[send_message] main_loop stuck-turn check failed", exc_info=True)
    elif tracker is not None:
        try:
            from src.config import get_settings
            timeout = get_settings().runtime_interaction_hardening.foreground_turn_timeout_seconds
            stuck_user_turn = bool(
                tracker.has_stuck_user_turn(target_chat_id, timeout_seconds=timeout)
            )
        except Exception:
            logger.debug("[send_message] tracker stuck-turn check failed", exc_info=True)

    queued_user_input = False
    busy = svc.busy_session_controller
    queue_for = getattr(busy, "queue_for", None)
    if callable(queue_for):
        try:
            queued_user_input = bool(queue_for(target_chat_id))
        except Exception:
            logger.debug("[send_message] busy queue check failed", exc_info=True)

    active_user_task = False
    store = svc.background_task_store
    active_task_method = getattr(store, "has_active_user_task_for_chat", None)
    if callable(active_task_method):
        try:
            active_user_task = bool(await _maybe_await(active_task_method(target_chat_id)))
        except Exception:
            logger.debug("[send_message] background task active check failed", exc_info=True)

    arbiter = svc.speaking_arbiter
    can_acquire = getattr(arbiter, "can_acquire", None)
    if callable(can_acquire):
        try:
            allowed, reason = can_acquire(
                target_chat_id,
                purpose="proactive",
                chat_activity_tracker=tracker,
            )
            if not allowed and reason == "active_user_turn":
                active_user_turn = True
            elif not allowed and reason == "unanswered_user_message":
                # Preserve the canonical proactive-gate reason by relying on
                # latest_user_message_at > latest_assistant_reply_at.
                pass
        except Exception:
            logger.debug("[send_message] speaking policy check failed", exc_info=True)

    return ProactiveGateContext(
        target_chat_id=target_chat_id,
        latest_user_message_at=latest_user_at,
        latest_assistant_reply_at=latest_assistant_at,
        pending_user_message=pending_user_message,
        active_user_turn=active_user_turn,
        queued_user_input=queued_user_input,
        active_user_task=active_user_task,
        stuck_user_turn=stuck_user_turn,
    )


def _max_timeish(left, right):
    if left is None:
        return right
    if right is None:
        return left
    try:
        left_ts = left.timestamp() if hasattr(left, "timestamp") else float(left)
        right_ts = right.timestamp() if hasattr(right, "timestamp") else float(right)
        return left if left_ts >= right_ts else right
    except Exception:
        return left


async def _record_proactive_outbound_trajectory(
    *,
    ctx: ToolExecutionContext,
    target: str,
    content: str,
    channel: str,
    resolved_chat_id: str,
) -> None:
    """Best-effort record of a delivered proactive outbound message.

    Failure must not fail send_message — the message already reached the user.
    """
    from src.core.tool_dispatcher import ServiceContextView
    from src.core.trajectory_store import TrajectoryEntryType
    from config.settings import PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED

    if not PROACTIVE_OUTBOUND_TRAJECTORY_ENABLED:
        return

    svc = ServiceContextView(ctx.services or {})
    trajectory_store = svc.trajectory_store
    if trajectory_store is None:
        return

    try:
        await trajectory_store.append(
            TrajectoryEntryType.PROACTIVE_OUTBOUND,
            resolved_chat_id,
            "lapwing",
            {
                "text": content,
                "target": target,
                "channel": channel,
                "kind": "proactive_outbound",
                "source": "send_message",
            },
        )
    except Exception:
        logger.exception(
            "Failed to record proactive outbound trajectory entry "
            "target=%s chat_id=%s",
            target, resolved_chat_id,
        )
        return

    # Observability: warn if the resolved chat_id has no recent inbound
    try:
        import time as _time
        since = _time.time() - 86400
        has_recent = await trajectory_store.has_recent_entry(
            resolved_chat_id,
            TrajectoryEntryType.USER_MESSAGE,
            since,
        )
        if not has_recent:
            logger.warning(
                "proactive_outbound_no_recent_inbound target=%s "
                "resolved_chat_id=%s channel=%s",
                target, resolved_chat_id, channel,
            )
    except Exception:
        logger.debug(
            "has_recent_entry check failed for proactive outbound",
            exc_info=True,
        )


def _record_proactive_send_success(gate: Any, ctx: ToolExecutionContext) -> bool:
    if gate is None or not _is_proactive_context(ctx):
        return False
    record_send = getattr(gate, "record_send", None)
    if not callable(record_send):
        return False
    record_send()
    return True


async def _send_message(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """向指定目标发送文本消息。支持 kevin_desktop、kevin_qq、qq_group:{group_id}。"""
    target = str(req.arguments.get("target", "")).strip()
    content = str(req.arguments.get("content", "")).strip()
    category = str(req.arguments.get("category", "")).strip() or None
    urgent = bool(req.arguments.get("urgent", False))

    if not target:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 target 参数"},
            reason="missing target",
        )
    if not content:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 content 参数"},
            reason="missing content",
        )

    # Defense-in-depth: profiles already exclude send_message from chat /
    # local_execution surfaces, but if anything slips through (custom profile,
    # registry mistake, future regression) the executor itself rejects calls
    # outside proactive context. Bare assistant text is the only legitimate
    # direct-reply path.
    if not _is_proactive_context(ctx):
        logger.warning(
            "[send_message] non-proactive context rejected; profile=%s",
            ctx.runtime_profile,
        )
        return ToolExecutionResult(
            success=False,
            payload={
                "error": (
                    "send_message 仅允许 proactive 场景使用"
                    "（inner_tick / compose_proactive）。"
                    "普通聊天回复请直接用 assistant 文本输出。"
                ),
            },
            reason="send_message_forbidden_in_direct_chat",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})

    factual_gate_result = await _factual_claim_gate(content, ctx)
    if factual_gate_result is not None:
        return factual_gate_result

    resolved_target_chat_id = _resolve_proactive_target_chat_id(target, ctx)
    gate_context = await _build_proactive_gate_context(
        ctx=ctx,
        target_chat_id=resolved_target_chat_id,
    )

    # Proactive gate — fires only on background/autonomous flows. Direct
    # assistant replies use bare model text and never reach this code.
    gate = svc.proactive_message_gate
    if gate is not None and _is_proactive_context(ctx):
        gate_decision = gate.evaluate(
            category=category,
            urgent=urgent,
            context=gate_context,
            reserve=False,
        )
        # Audit every decision (allow / defer / deny) into the mutation log
        # so allow:defer:deny ratios can be inspected after the fact.
        await _record_proactive_decision(
            ctx=ctx,
            decision=gate_decision,
            target=target,
            target_chat_id=resolved_target_chat_id,
            category=category,
            urgent=urgent,
        )
        if gate_decision.decision != "allow":
            logger.info(
                "[send_message] proactive_gate=%s reason=%s target=%s",
                gate_decision.decision, gate_decision.reason, target,
            )
            return ToolExecutionResult(
                success=False,
                payload={
                    "sent": False,
                    "gate_decision": gate_decision.decision,
                    "gate_reason": gate_decision.reason,
                    "target": target,
                    "category": category,
                },
                reason=f"proactive_gate:{gate_decision.decision}:{gate_decision.reason}",
            )
    elif _is_proactive_context(ctx):
        hard_reason = gate_context.hard_denial_reason()
        if hard_reason is not None:
            logger.info(
                "[send_message] proactive hard-deny without gate reason=%s target=%s target_chat_id=%s",
                hard_reason, target, resolved_target_chat_id or "",
            )
            return ToolExecutionResult(
                success=False,
                payload={
                    "sent": False,
                    "gate_decision": "deny",
                    "gate_reason": hard_reason,
                    "target": target,
                    "category": category,
                },
                reason=f"proactive_gate:deny:{hard_reason}",
            )

    channel_manager = svc.channel_manager
    if channel_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "channel_manager 不可用，无法发送消息。"},
            reason="channel_manager unavailable",
        )

    try:
        if target == "kevin_desktop":
            # 取 desktop adapter，检查连接状态
            desktop_adapter = channel_manager.get_adapter("desktop")
            is_connected = await desktop_adapter.is_connected() if desktop_adapter is not None else False
            if not is_connected:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "Desktop 未连接。你可以改用 target='kevin_qq' 发到 QQ。"},
                    reason="desktop_not_connected",
                )
            await desktop_adapter.send_text(desktop_adapter.config.get("kevin_id", "owner"), content)
            gate_recorded = _record_proactive_send_success(gate, ctx)
            _resolved = resolved_target_chat_id
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="desktop", resolved_chat_id=_resolved,
                )
            else:
                logger.info(
                    "[send_message] skipped proactive trajectory write: "
                    "no resolved chat_id for target=%s", target,
                )
            return ToolExecutionResult(
                success=True,
                payload={
                    "sent": True,
                    "target": target,
                    "content": content,
                    "proactive_budget_recorded": gate_recorded,
                },
            )

        elif target == "kevin_qq":
            # 通过 qq adapter 发私信
            owner_qq_id = svc.owner_qq_id
            if not owner_qq_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "owner_qq_id 未配置，无法发送 QQ 私信。"},
                    reason="owner_qq_id_not_configured",
                )

            qq_adapter = None
            try:
                qq_adapter = channel_manager.get_adapter("qq")
            except Exception:
                pass

            if qq_adapter is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "QQ 适配器不可用，无法发送私信。"},
                    reason="qq_adapter_unavailable",
                )
            await qq_adapter.send_private_message(str(owner_qq_id), content)
            gate_recorded = _record_proactive_send_success(gate, ctx)
            _resolved = resolved_target_chat_id
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="qq", resolved_chat_id=_resolved,
                )
            return ToolExecutionResult(
                success=True,
                payload={
                    "sent": True,
                    "target": target,
                    "content": content,
                    "proactive_budget_recorded": gate_recorded,
                },
            )

        elif target.startswith("qq_group:"):
            # 向 QQ 群发消息
            group_id = target.split("qq_group:", 1)[1].strip()
            if not group_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "qq_group target 格式有误，应为 qq_group:{group_id}"},
                    reason="invalid_qq_group_target",
                )

            qq_adapter = None
            try:
                qq_adapter = channel_manager.get_adapter("qq")
            except Exception:
                pass

            if qq_adapter is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "QQ 适配器不可用，无法发送群消息。"},
                    reason="qq_adapter_unavailable",
                )
            await qq_adapter.send_group_message(group_id, content)
            gate_recorded = _record_proactive_send_success(gate, ctx)
            _resolved = resolved_target_chat_id
            if _resolved is not None:
                await _record_proactive_outbound_trajectory(
                    ctx=ctx, target=target, content=content,
                    channel="qq_group", resolved_chat_id=_resolved,
                )
            else:
                logger.info(
                    "[send_message] skipped proactive trajectory write: "
                    "group canonical chat_id not resolved for target=%s",
                    target,
                )
            return ToolExecutionResult(
                success=True,
                payload={
                    "sent": True,
                    "target": target,
                    "content": content,
                    "proactive_budget_recorded": gate_recorded,
                },
            )

        else:
            return ToolExecutionResult(
                success=False,
                payload={
                    "error": (
                        f"未知 target：'{target}'。"
                        "支持的值：kevin_qq、kevin_desktop、qq_group:{{group_id}}"
                    )
                },
                reason="unknown_target",
            )

    except Exception as exc:
        logger.warning("[send_message] 发送失败 target=%s: %s", target, exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"发送消息失败：{exc}"},
            reason=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. send_image
# ─────────────────────────────────────────────────────────────────────────────

async def _send_image(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """发送图片给 owner（默认 QQ）。需要 image_url 或 image_path 至少一个。"""
    target = str(req.arguments.get("target", "kevin_qq")).strip()
    image_url = str(req.arguments.get("image_url", "")).strip() or None
    image_path = str(req.arguments.get("image_path", "")).strip() or None
    caption = str(req.arguments.get("caption", "")).strip()

    if not image_url and not image_path:
        return ToolExecutionResult(
            success=False,
            payload={"error": "必须提供 image_url 或 image_path 参数"},
            reason="missing image source",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    channel_manager = svc.channel_manager
    if channel_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "channel_manager 不可用，无法发送图片。"},
            reason="channel_manager unavailable",
        )

    try:
        await channel_manager.send_image_to_owner(
            url=image_url,
            path=image_path,
            caption=caption,
        )
        return ToolExecutionResult(
            success=True,
            payload={
                "sent": True,
                "target": target,
                "url": image_url or "",
                "path": image_path or "",
                "caption": caption,
            },
        )
    except Exception as exc:
        logger.warning("[send_image] 发送失败: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"发送图片失败：{exc}"},
            reason=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. view_image（占位符 — VLM 不一定可用）
# ─────────────────────────────────────────────────────────────────────────────

_VIEW_IMAGE_MAX_CHARS = 1500


async def _view_image(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """用 VLM 描述图片内容。传 base64 数据或本地路径均可。"""
    image = str(req.arguments.get("image", "")).strip()
    if not image:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 image 参数（base64 或文件路径）"},
            reason="missing image",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    vlm = svc.vlm
    if vlm is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "视觉理解不可用。"},
            reason="vlm_unavailable",
        )

    try:
        description = await vlm.describe(image, prompt="描述这张图片的内容。")
        # 结果体积控制
        if len(description) > _VIEW_IMAGE_MAX_CHARS:
            description = description[:_VIEW_IMAGE_MAX_CHARS] + "…（已截断）"
        return ToolExecutionResult(
            success=True,
            payload={"description": description},
        )
    except Exception as exc:
        logger.warning("[view_image] VLM 调用失败: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"图片描述失败：{exc}"},
            reason=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# browse 安全检查辅助函数
# ─────────────────────────────────────────────────────────────────────────────

_INTERNAL_IP_RE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+"
    r"|::1|fc[0-9a-f]{2}::.*)$",
    re.IGNORECASE,
)


def _check_browse_safety(url: str) -> dict[str, Any]:
    """检查 URL 是否允许被 browse 工具访问。含 DNS 解析级检查。

    Returns:
        dict with keys: allowed (bool), reason (str, only when denied)
    """
    from src.utils.url_safety import check_url_safety
    result = check_url_safety(url)
    if result.safe:
        return {"allowed": True}
    return {"allowed": False, "reason": result.reason}


# ─────────────────────────────────────────────────────────────────────────────
# 7. browse
# ─────────────────────────────────────────────────────────────────────────────

_BROWSE_DESC_MAX_CHARS = 2000
_BROWSE_TEXT_FALLBACK_MAX = 2000


async def _browse(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """一次性打开网页 → 截图 → VLM 描述 → 关闭标签页。

    如果没有 VLM 则回退为提取页面文本。
    """
    url = str(req.arguments.get("url", "")).strip()
    if not url:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 url 参数"},
            reason="missing url",
        )

    # 安全检查
    safety = _check_browse_safety(url)
    if not safety["allowed"]:
        return ToolExecutionResult(
            success=False,
            payload={"error": safety["reason"]},
            reason="url_blocked",
        )

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    browser_manager = svc.browser_manager
    if browser_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "浏览器不可用。改用 research 工具回答问题。"},
            reason="browser_unavailable",
        )

    vlm = svc.vlm
    tab_id: str | None = None

    try:
        # 打开页面
        tab_info = await browser_manager.new_tab(url)
        tab_id = tab_info.tab_id
        logger.info("[browse] 打开标签页 tab_id=%s url=%s", tab_id, url)

        if vlm is not None:
            # 截图 → VLM 描述（VLM 接收图片文件路径）
            screenshot_path = await browser_manager.screenshot(tab_id=tab_id)
            description = await vlm.understand_image(
                prompt="描述这张网页截图的内容，包括页面标题、主要信息和关键内容。",
                image_source=screenshot_path,
            )
            if len(description) > _BROWSE_DESC_MAX_CHARS:
                description = description[:_BROWSE_DESC_MAX_CHARS] + "…（已截断）"
            payload: dict[str, Any] = {
                "url": url,
                "description": description,
                "method": "screenshot+vlm",
            }
        else:
            # 回退：提取页面文本
            page_state = await browser_manager.get_page_state(tab_id=tab_id)
            text = page_state.to_llm_text() if hasattr(page_state, "to_llm_text") else str(page_state)
            if len(text) > _BROWSE_TEXT_FALLBACK_MAX:
                text = text[:_BROWSE_TEXT_FALLBACK_MAX] + "…（已截断）"
            payload = {
                "url": url,
                "text": text,
                "method": "text_fallback",
            }

        return ToolExecutionResult(success=True, payload=payload)

    except Exception as exc:
        logger.warning("[browse] 浏览失败 url=%s: %s", url, exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"浏览失败：{exc}"},
            reason=str(exc),
        )
    finally:
        # 一次性：无论成功与否，关闭标签页
        if tab_id is not None:
            try:
                await browser_manager.close_tab(tab_id)
                logger.info("[browse] 已关闭标签页 tab_id=%s", tab_id)
            except Exception as exc:
                logger.warning("[browse] 关闭标签页失败 tab_id=%s: %s", tab_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# 注册函数
# ─────────────────────────────────────────────────────────────────────────────

def register_personal_tools(registry: Any, services: dict[str, Any]) -> None:
    """将所有个人工具注册到 ToolRegistry。

    Args:
        registry: ToolRegistry 实例
        services: 服务字典，应包含：channel_manager, scheduler, browser_manager, vlm, owner_qq_id
    """

    registry.register(ToolSpec(
        name="get_time",
        description="获取当前时间。返回日期、时间、星期。",
        json_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        executor=_get_time,
        capability="general",
        risk_level="low",
        max_result_tokens=50,
    ))

    registry.register(ToolSpec(
        name="send_message",
        description=(
            "向指定目标发送文字消息。"
            "target 支持：kevin_qq（Kevin 的 QQ）、kevin_desktop（桌面客户端）、"
            "qq_group:{group_id}（QQ 群）。"
            "在 inner_tick 等自主流程下会被 ProactiveMessageGate 限速；"
            "可设置 category=reminder_due/safety/explicit_commitment 触发紧急豁免。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "消息目标：kevin_qq / kevin_desktop / qq_group:{group_id}",
                },
                "content": {
                    "type": "string",
                    "description": "消息正文",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "消息分类（可选）。仅 reminder_due/safety/explicit_commitment "
                        "在配置允许时才能跳过限流；其他分类按常规速率限制处理。"
                    ),
                },
                "urgent": {
                    "type": "boolean",
                    "description": "显式标记紧急（可选）；与 category 任一命中即触发豁免。",
                },
            },
            "required": ["target", "content"],
        },
        executor=_send_message,
        capability="general",
        risk_level="medium",
        max_result_tokens=100,
    ))

    registry.register(ToolSpec(
        name="send_image",
        description="发送图片给 owner（默认走 QQ）。需要 image_url 或 image_path 至少一个。",
        json_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "发送目标（默认 kevin_qq）",
                    "default": "kevin_qq",
                },
                "image_url": {
                    "type": "string",
                    "description": "图片 URL（与 image_path 二选一）",
                },
                "image_path": {
                    "type": "string",
                    "description": "本地图片路径（与 image_url 二选一）",
                },
                "caption": {
                    "type": "string",
                    "description": "图片说明文字（可选）",
                },
            },
            "required": [],
        },
        executor=_send_image,
        capability="general",
        risk_level="medium",
        max_result_tokens=100,
    ))

    registry.register(ToolSpec(
        name="view_image",
        description="用视觉模型描述图片内容。传入 base64 数据或本地文件路径。",
        json_schema={
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "图片 base64 编码或本地文件路径",
                },
            },
            "required": ["image"],
        },
        executor=_view_image,
        capability="general",
        risk_level="low",
        max_result_tokens=400,
    ))

    registry.register(ToolSpec(
        name="browse",
        description=(
            "你想亲自看看一个网页长什么样时用这个。会打开页面、截图、描述。\n"
            "注意：大多数问题用 research 更合适——它会自动搜索、阅读、综合答案。\n"
            "只有当 research 查不到、或你想看页面的视觉布局时才用 browse。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要访问的网页 URL（仅支持 http/https，不允许内网地址）",
                },
            },
            "required": ["url"],
        },
        executor=_browse,
        capability="browser",
        risk_level="medium",
        max_result_tokens=500,
    ))

    logger.info("[personal_tools] 已注册 5 个个人工具")
