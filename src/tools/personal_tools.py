"""个人工具集：Lapwing 日常行动的核心工具，Phase 4 重铸版。

涵盖：时间感知、消息发送、图片发送/读图、网络搜索/抓取、浏览器一次性访问、委托占位符。
每个工具遵循五项标准：简单参数、自足结果、有意义的错误、结果体积控制、可预期副作用。
"""

from __future__ import annotations

import ipaddress
import logging
import re
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


async def _record_proactive_decision(
    *,
    ctx: ToolExecutionContext,
    decision,
    target: str,
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
    services = ctx.services or {}
    if services.get("proactive_send_active"):
        return True
    return False


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

    # Proactive gate — fires only on background/autonomous flows. Direct
    # assistant replies use bare model text and never reach this code.
    gate = (ctx.services or {}).get("proactive_message_gate")
    if gate is not None and _is_proactive_context(ctx):
        gate_decision = gate.evaluate(category=category, urgent=urgent)
        # Audit every decision (allow / defer / deny) into the mutation log
        # so allow:defer:deny ratios can be inspected after the fact.
        await _record_proactive_decision(
            ctx=ctx,
            decision=gate_decision,
            target=target,
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

    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
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
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
            )

        elif target == "kevin_qq":
            # 通过 qq adapter 发私信
            owner_qq_id = ctx.services.get("owner_qq_id")
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
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
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
            return ToolExecutionResult(
                success=True,
                payload={"sent": True, "target": target, "content": content},
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
