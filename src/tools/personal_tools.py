"""个人工具集：Lapwing 日常行动的核心工具，Phase 4 重铸版。

涵盖：时间感知、消息发送、图片发送/读图、网络搜索/抓取、浏览器一次性访问、委托占位符。
每个工具遵循五项标准：简单参数、自足结果、有意义的错误、结果体积控制、可预期副作用。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.personal_tools")

_TZ_TAIPEI = ZoneInfo("Asia/Taipei")

# 星期中文映射
_WEEKDAY_ZH = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_time
# ─────────────────────────────────────────────────────────────────────────────

async def _get_time(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """返回台北时区当前时间。"""
    now = datetime.now(_TZ_TAIPEI)
    return ToolExecutionResult(
        success=True,
        payload={
            "time": now.strftime("%Y年%m月%d日 %H:%M:%S"),
            "weekday": _WEEKDAY_ZH[now.weekday()],
            "timezone": "Asia/Taipei",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. send_message
# ─────────────────────────────────────────────────────────────────────────────

async def _send_message(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """向指定目标发送文本消息。支持 kevin_desktop、kevin_qq、qq_group:{group_id}。"""
    target = str(req.arguments.get("target", "")).strip()
    content = str(req.arguments.get("content", "")).strip()

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

    channel_manager = ctx.services.get("channel_manager")
    if channel_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "channel_manager 不可用，无法发送消息。"},
            reason="channel_manager unavailable",
        )

    try:
        if target == "kevin_desktop":
            # 取 desktop adapter，检查连接状态
            desktop_adapter = None
            try:
                from src.adapters.desktop import DesktopAdapter
                desktop_adapter = channel_manager.get_adapter("desktop")
            except Exception:
                pass

            if desktop_adapter is None or not desktop_adapter.is_connected():
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "Desktop 未连接。你可以改用 target='kevin_qq' 发到 QQ。"},
                    reason="desktop_not_connected",
                )
            await desktop_adapter.send_message(content)
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

    channel_manager = ctx.services.get("channel_manager")
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

    vlm = ctx.services.get("vlm")
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
# 5. web_search
# ─────────────────────────────────────────────────────────────────────────────

_SEARCH_MAX_RESULTS = 5
_SEARCH_SNIPPET_MAX = 200


async def _web_search(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """联网搜索，返回前 5 条结果，每条摘要不超过 200 字。"""
    from src.tools.web_search import search as tavily_search

    query = str(req.arguments.get("query", "")).strip()
    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 query 参数"},
            reason="missing query",
        )

    try:
        raw_results = await tavily_search(query, max_results=_SEARCH_MAX_RESULTS)
    except Exception as exc:
        logger.warning("[web_search] 搜索异常: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"搜索失败：{exc}。可以换个关键词试试。"},
            reason=str(exc),
        )

    if not raw_results:
        return ToolExecutionResult(
            success=False,
            payload={"error": "没有找到相关结果。可以换个关键词试试。"},
            reason="no_results",
        )

    # 结果体积控制：摘要截断
    results = []
    for item in raw_results[:_SEARCH_MAX_RESULTS]:
        snippet = item.get("snippet") or item.get("content", "")
        if len(snippet) > _SEARCH_SNIPPET_MAX:
            snippet = snippet[:_SEARCH_SNIPPET_MAX] + "…"
        entry: dict[str, Any] = {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": snippet,
        }
        if item.get("published_date"):
            entry["published_date"] = item["published_date"]
        results.append(entry)

    return ToolExecutionResult(
        success=True,
        payload={"query": query, "results": results},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. web_fetch
# ─────────────────────────────────────────────────────────────────────────────

_WEB_FETCH_MAX_CHARS = 3000
_WEB_FETCH_TRUNCATION_NOTE = "内容较长，已截断。如需查看完整页面，可以用 browse 工具。"


async def _web_fetch(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """抓取网页正文，返回纯文本。可提供 question 聚焦抓取目标。"""
    from src.tools.web_fetcher import fetch as fetch_url

    url = str(req.arguments.get("url", "")).strip()
    question = str(req.arguments.get("question", "")).strip()

    if not url:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 url 参数"},
            reason="missing url",
        )

    try:
        result = await fetch_url(url)
    except Exception as exc:
        logger.warning("[web_fetch] 抓取异常 url=%s: %s", url, exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"抓取失败：{exc}"},
            reason=str(exc),
        )

    if not result.success:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"抓取失败：{result.error}", "url": result.url},
            reason=result.error,
        )

    text = result.text
    truncated = False
    if len(text) > _WEB_FETCH_MAX_CHARS:
        text = text[:_WEB_FETCH_MAX_CHARS]
        truncated = True

    payload: dict[str, Any] = {
        "url": result.url,
        "title": result.title,
        "text": text,
    }
    if truncated:
        payload["truncation_note"] = _WEB_FETCH_TRUNCATION_NOTE
    if result.published_date:
        payload["published_date"] = result.published_date
    if question:
        payload["question"] = question

    return ToolExecutionResult(success=True, payload=payload)


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
    """检查 URL 是否允许被 browse 工具访问。

    Returns:
        dict with keys: allowed (bool), reason (str, only when denied)
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return {"allowed": False, "reason": f"URL 解析失败：{exc}"}

    # 只允许 http/https
    if parsed.scheme not in ("http", "https"):
        return {
            "allowed": False,
            "reason": f"不支持的 URL 协议 '{parsed.scheme}'，只允许 http 和 https。",
        }

    hostname = parsed.hostname or ""

    # 拒绝 localhost / 内网地址
    if _INTERNAL_IP_RE.match(hostname):
        return {
            "allowed": False,
            "reason": f"禁止访问内网地址：{hostname}",
        }

    # 尝试解析为 IP，检查是否内网段
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return {
                "allowed": False,
                "reason": f"禁止访问内网 IP：{hostname}",
            }
    except ValueError:
        pass  # 不是裸 IP，是域名，不处理

    return {"allowed": True}


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

    browser_manager = ctx.services.get("browser_manager")
    if browser_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "浏览器不可用。可以用 web_fetch 抓取文本内容。"},
            reason="browser_unavailable",
        )

    vlm = ctx.services.get("vlm")
    tab_id: str | None = None

    try:
        # 打开页面
        tab_id = await browser_manager.open_tab(url)
        logger.info("[browse] 打开标签页 tab_id=%s url=%s", tab_id, url)

        if vlm is not None:
            # 截图 → VLM 描述
            screenshot_data = await browser_manager.take_screenshot(tab_id)
            description = await vlm.describe(
                screenshot_data,
                prompt="描述这张网页截图的内容，包括页面标题、主要信息和关键内容。",
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
            page_state = await browser_manager.get_page_state(tab_id)
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
        description="获取当前时间（台北时区）。返回日期、时间、星期。",
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
        name="web_search",
        description="联网搜索，返回最多 5 条结果（标题、URL、摘要）。",
        json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题",
                },
            },
            "required": ["query"],
        },
        executor=_web_search,
        capability="web",
        risk_level="low",
        max_result_tokens=600,
    ))

    registry.register(ToolSpec(
        name="web_fetch",
        description=(
            "抓取指定网页的正文文本（最多 3000 字）。"
            "可提供 question 参数聚焦感兴趣的内容。"
            "需要完整交互的页面请用 browse 工具。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页 URL",
                },
                "question": {
                    "type": "string",
                    "description": "希望从页面中获取的具体信息（可选）",
                },
            },
            "required": ["url"],
        },
        executor=_web_fetch,
        capability="web",
        risk_level="low",
        max_result_tokens=800,
    ))

    registry.register(ToolSpec(
        name="browse",
        description=(
            "打开网页、截图并用视觉模型描述页面内容，然后自动关闭标签页。"
            "适合需要「看」页面的场景（图表、视觉布局等）。"
            "纯文本内容优先用 web_fetch，速度更快。"
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

    logger.info("[personal_tools] 已注册 7 个个人工具")
