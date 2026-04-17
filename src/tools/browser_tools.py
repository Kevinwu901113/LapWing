"""浏览器工具集：通过 BrowserManager 提供网页浏览、交互、截图等工具。"""

from __future__ import annotations

import logging
from typing import Any

from src.core.browser_manager import (
    BrowserElementNotFoundError,
    BrowserError,
    BrowserNavigationError,
    BrowserNotStartedError,
    BrowserTabNotFoundError,
    BrowserTimeoutError,
    PageState,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.browser_tools")

# ── 错误消息映射 ─────────────────────────────────────────────────────────────

_ERROR_MESSAGES: dict[type, str] = {
    BrowserNotStartedError: "浏览器没有启动",
    BrowserTimeoutError: "页面加载超时了",
    BrowserTabNotFoundError: "找不到指定的标签页",
}


def _browser_error_result(exc: BrowserError) -> ToolExecutionResult:
    """将 BrowserError 转为用户友好的 ToolExecutionResult。"""
    if isinstance(exc, BrowserNavigationError):
        msg = f"打不开这个网页：{exc}"
    elif isinstance(exc, BrowserElementNotFoundError):
        msg = f"找不到指定的元素：{exc}"
    else:
        msg = _ERROR_MESSAGES.get(type(exc), f"浏览器操作失败：{exc}")

    return ToolExecutionResult(
        success=False,
        payload={"error": msg},
        reason=msg,
    )


def _page_state_result(page_state: PageState) -> ToolExecutionResult:
    """将 PageState 包装为成功的 ToolExecutionResult。"""
    return ToolExecutionResult(
        success=True,
        payload={"output": page_state.to_llm_text()},
    )


# ── 注册入口 ─────────────────────────────────────────────────────────────────


def register_browser_tools(
    registry: Any,
    browser_manager: Any,
    credential_vault: Any | None = None,
    browser_guard: Any | None = None,
    event_bus: Any | None = None,
) -> None:
    """注册所有浏览器相关工具到 ToolRegistry。

    工具执行器作为闭包定义，通过闭包捕获 browser_manager 等依赖。
    """

    async def _publish(event: str, data: dict[str, Any]) -> None:
        """可选地向 EventBus 发送事件。"""
        if event_bus is not None:
            try:
                await event_bus.publish(event, data)
            except Exception:
                pass

    # ── 1. browser_open ───────────────────────────────────────────────

    async def browser_open(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        url = str(req.arguments.get("url", "")).strip()
        if not url:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 url 参数"},
                reason="缺少 url 参数",
            )

        # BrowserGuard: 检查 URL 安全性
        if browser_guard is not None:
            guard_result = browser_guard.check_url(url)
            if guard_result.action == "block":
                reason = guard_result.reason or "URL 被安全策略拦截"
                return ToolExecutionResult(
                    success=False,
                    payload={"error": reason},
                    reason=reason,
                )

        try:
            page_state = await browser_manager.navigate(url)
            await _publish("browser.navigate", {"url": url})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_open",
            description=(
                "打开一个网页，可以看到完整的页面内容和所有可交互元素"
                "（按钮、输入框等）。适合需要操作页面（点击、填表、登录）"
                "或查看 JavaScript 动态渲染内容的场景。"
                "注意：比 research 慢；只需要查找信息时优先用 research。\n\n"
                "重要提示：\n"
                "1. 使用 [编号] 引用元素，如 browser_click(element=\"[3]\")\n"
                "2. 每次操作后会返回新的页面状态，元素编号会重新分配\n"
                "3. 不要在一次 tool call 中尝试完成所有操作，一步一步来\n"
                "4. 如果页面有很多内容看不完，用 browser_scroll 翻页\n"
                "5. 密码等敏感信息用 browser_login 工具，不要直接在 browser_type 中输入"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网页 URL"},
                },
                "required": ["url"],
            },
            executor=browser_open,
            capability="browser",
            risk_level="medium",
        )
    )

    # ── 2. browser_click ──────────────────────────────────────────────

    async def browser_click(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        element = str(req.arguments.get("element", "")).strip()
        tab_id = req.arguments.get("tab_id")
        if not element:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 element 参数"},
                reason="缺少 element 参数",
            )

        # BrowserGuard: 检查操作安全性
        if browser_guard is not None:
            try:
                # 获取当前页面状态以读取元素文本和 URL
                page_state = await browser_manager.get_page_state(tab_id)
                element_text = ""
                # 从元素列表中找到匹配的元素文本
                for elem in page_state.elements:
                    if f"[{elem.index}]" == element:
                        element_text = elem.text or elem.aria_label or ""
                        break
                guard_result = browser_guard.check_action(
                    "click", element_text, page_state.url
                )
                if guard_result.action == "block":
                    reason = guard_result.reason or "操作被安全策略拦截"
                    return ToolExecutionResult(
                        success=False,
                        payload={"error": reason},
                        reason=reason,
                    )
                if guard_result.action == "require_consent":
                    reason = guard_result.reason or "此操作需要用户确认"
                    return ToolExecutionResult(
                        success=False,
                        payload={
                            "error": reason,
                            "requires_consent": True,
                        },
                        reason=reason,
                    )
            except BrowserError:
                # 获取页面状态失败时，跳过 guard 检查继续执行
                pass

        try:
            page_state = await browser_manager.click(element, tab_id)
            await _publish("browser.click", {"element": element})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_click",
            description=(
                "点击页面上的元素。使用 browser_open 返回的元素编号，"
                "如 browser_click(element=\"[3]\") 表示点击编号为 3 的元素。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "element": {
                        "type": "string",
                        "description": "元素引用，如 [3] 表示编号 3 的元素",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选，默认当前活跃标签页）",
                    },
                },
                "required": ["element"],
            },
            executor=browser_click,
            capability="browser",
            risk_level="medium",
        )
    )

    # ── 3. browser_type ───────────────────────────────────────────────

    async def browser_type(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        element = str(req.arguments.get("element", "")).strip()
        text = str(req.arguments.get("text", ""))
        press_enter = bool(req.arguments.get("press_enter", False))
        tab_id = req.arguments.get("tab_id")
        if not element:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 element 参数"},
                reason="缺少 element 参数",
            )

        try:
            page_state = await browser_manager.type_text(
                element, text, press_enter=press_enter, tab_id=tab_id
            )
            await _publish("browser.type", {"element": element})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_type",
            description=(
                "在输入框中输入文本。指定元素编号和要输入的内容。"
                "默认会先清空输入框再输入。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "element": {
                        "type": "string",
                        "description": "输入框元素引用，如 [5]",
                    },
                    "text": {
                        "type": "string",
                        "description": "要输入的文本",
                    },
                    "press_enter": {
                        "type": "boolean",
                        "description": "输入后是否按回车（默认 false）",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
                "required": ["element", "text"],
            },
            executor=browser_type,
            capability="browser",
            risk_level="medium",
        )
    )

    # ── 4. browser_select ─────────────────────────────────────────────

    async def browser_select(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        element = str(req.arguments.get("element", "")).strip()
        value = str(req.arguments.get("value", ""))
        tab_id = req.arguments.get("tab_id")
        if not element:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 element 参数"},
                reason="缺少 element 参数",
            )

        try:
            page_state = await browser_manager.select_option(element, value, tab_id)
            await _publish("browser.select", {"element": element, "value": value})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_select",
            description="选择下拉框的选项。指定下拉框元素编号和要选择的值。",
            json_schema={
                "type": "object",
                "properties": {
                    "element": {
                        "type": "string",
                        "description": "下拉框元素引用，如 [7]",
                    },
                    "value": {
                        "type": "string",
                        "description": "要选择的选项值",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
                "required": ["element", "value"],
            },
            executor=browser_select,
            capability="browser",
            risk_level="medium",
        )
    )

    # ── 5. browser_scroll ─────────────────────────────────────────────

    async def browser_scroll(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        direction = str(req.arguments.get("direction", "down"))
        amount = int(req.arguments.get("amount", 3) or 3)
        tab_id = req.arguments.get("tab_id")

        try:
            page_state = await browser_manager.scroll(direction, amount, tab_id)
            await _publish("browser.scroll", {"direction": direction, "amount": amount})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_scroll",
            description="滚动页面。可以向上或向下滚动，查看页面更多内容。",
            json_schema={
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "description": "滚动方向：down（向下）或 up（向上），默认 down",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "滚动幅度（视口倍数），默认 3",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
            },
            executor=browser_scroll,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 6. browser_screenshot ─────────────────────────────────────────

    async def browser_screenshot(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        tab_id = req.arguments.get("tab_id")
        full_page = bool(req.arguments.get("full_page", False))

        try:
            screenshot_path = await browser_manager.screenshot(tab_id, full_page)
            await _publish("browser.screenshot", {"path": screenshot_path})
            return ToolExecutionResult(
                success=True,
                payload={"path": screenshot_path, "message": "截图已保存"},
            )
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_screenshot",
            description="对当前页面截图，返回截图文件路径。",
            json_schema={
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "是否截取完整页面（默认 false，只截可见区域）",
                    },
                },
            },
            executor=browser_screenshot,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 7. browser_get_text ───────────────────────────────────────────

    async def browser_get_text(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        selector = req.arguments.get("selector")
        tab_id = req.arguments.get("tab_id")

        try:
            text = await browser_manager.get_page_text(selector, tab_id)
            return ToolExecutionResult(
                success=True,
                payload={"text": text},
            )
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_get_text",
            description=(
                "获取页面的纯文本内容。可指定 CSS 选择器只提取部分内容。"
                "不指定选择器时会自动提取 main/article/body 的文本。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS 选择器（可选），如 '.article-content'",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
            },
            executor=browser_get_text,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 8. browser_back ───────────────────────────────────────────────

    async def browser_back(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        tab_id = req.arguments.get("tab_id")

        try:
            page_state = await browser_manager.go_back(tab_id)
            await _publish("browser.back", {})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_back",
            description="浏览器后退到上一个页面。",
            json_schema={
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
            },
            executor=browser_back,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 9. browser_tabs ───────────────────────────────────────────────

    async def browser_tabs(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        try:
            tabs = await browser_manager.list_tabs()
            lines: list[str] = []
            for tab in tabs:
                active_mark = " ← 当前" if tab.is_active else ""
                lines.append(f"[{tab.tab_id}] {tab.title} ({tab.url}){active_mark}")
            output = "\n".join(lines) if lines else "没有打开的标签页"
            return ToolExecutionResult(
                success=True,
                payload={"output": output, "count": len(tabs)},
            )
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_tabs",
            description="列出所有打开的浏览器标签页。",
            json_schema={
                "type": "object",
                "properties": {},
            },
            executor=browser_tabs,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 10. browser_switch_tab ────────────────────────────────────────

    async def browser_switch_tab(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        tab_id = str(req.arguments.get("tab_id", "")).strip()
        if not tab_id:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 tab_id 参数"},
                reason="缺少 tab_id 参数",
            )

        try:
            page_state = await browser_manager.switch_tab(tab_id)
            await _publish("browser.switch_tab", {"tab_id": tab_id})
            return _page_state_result(page_state)
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_switch_tab",
            description="切换到指定的浏览器标签页。",
            json_schema={
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "要切换到的标签页 ID",
                    },
                },
                "required": ["tab_id"],
            },
            executor=browser_switch_tab,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 11. browser_close_tab ─────────────────────────────────────────

    async def browser_close_tab(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        tab_id = str(req.arguments.get("tab_id", "")).strip()
        if not tab_id:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 tab_id 参数"},
                reason="缺少 tab_id 参数",
            )

        try:
            await browser_manager.close_tab(tab_id)
            await _publish("browser.close_tab", {"tab_id": tab_id})
            return ToolExecutionResult(
                success=True,
                payload={"output": f"标签页 {tab_id} 已关闭"},
            )
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_close_tab",
            description="关闭指定的浏览器标签页。",
            json_schema={
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "要关闭的标签页 ID",
                    },
                },
                "required": ["tab_id"],
            },
            executor=browser_close_tab,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 12. browser_wait ──────────────────────────────────────────────

    async def browser_wait(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        condition = str(req.arguments.get("condition", "")).strip()
        timeout_ms = int(req.arguments.get("timeout_ms", 5000) or 5000)
        tab_id = req.arguments.get("tab_id")
        if not condition:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 condition 参数"},
                reason="缺少 condition 参数",
            )

        try:
            result = await browser_manager.wait_for(condition, timeout_ms, tab_id)
            return ToolExecutionResult(
                success=True,
                payload={"waited": True, "condition": condition, "met": result},
            )
        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_wait",
            description=(
                "等待页面满足指定条件。"
                "condition 可以是：'navigation'（等待导航完成）、"
                "'selector:xxx'（等待元素出现）、'idle'（等待网络空闲）。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": "等待条件：navigation / selector:xxx / idle",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "超时毫秒数（默认 5000）",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "标签页 ID（可选）",
                    },
                },
                "required": ["condition"],
            },
            executor=browser_wait,
            capability="browser",
            risk_level="low",
        )
    )

    # ── 13. browser_login ─────────────────────────────────────────────

    async def browser_login(
        req: ToolExecutionRequest, ctx: ToolExecutionContext
    ) -> ToolExecutionResult:
        service = str(req.arguments.get("service", "")).strip()
        if not service:
            return ToolExecutionResult(
                success=False,
                payload={"error": "缺少 service 参数"},
                reason="缺少 service 参数",
            )

        # 1. 从凭据保险库获取凭据
        if credential_vault is None:
            return ToolExecutionResult(
                success=False,
                payload={"error": "凭据保险库未配置，无法自动登录"},
                reason="凭据保险库未配置",
            )

        credential = credential_vault.get(service)
        if credential is None:
            return ToolExecutionResult(
                success=False,
                payload={"error": f"没有找到 {service} 的登录凭据"},
                reason=f"凭据不存在: {service}",
            )

        try:
            # 2. 导航到登录页面
            await browser_manager.navigate(credential.login_url)

            # 3. 获取页面状态，找到用户名和密码输入框
            page_state = await browser_manager.get_page_state()
            username_ref = None
            password_ref = None
            submit_ref = None

            for elem in page_state.elements:
                if elem.tag == "input" and elem.element_type in (
                    "text", "email", None
                ):
                    if username_ref is None:
                        username_ref = f"[{elem.index}]"
                elif elem.tag == "input" and elem.element_type == "password":
                    if password_ref is None:
                        password_ref = f"[{elem.index}]"
                elif elem.tag == "button" or (
                    elem.tag == "input" and elem.element_type == "submit"
                ):
                    if submit_ref is None:
                        submit_ref = f"[{elem.index}]"

            if username_ref is None or password_ref is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "在登录页面上找不到用户名或密码输入框"},
                    reason="找不到登录表单",
                )

            # 4. 输入用户名和密码
            await browser_manager.type_text(username_ref, credential.username)
            await browser_manager.type_text(password_ref, credential.password)

            # 5. 点击提交按钮
            if submit_ref is not None:
                await browser_manager.click(submit_ref)
            else:
                # 没有按钮，尝试在密码框按回车
                await browser_manager.type_text(
                    password_ref, "", press_enter=True
                )

            # 6. 等待导航完成
            try:
                await browser_manager.wait_for("navigation", timeout_ms=5000)
            except BrowserTimeoutError:
                pass  # 有些网站不触发导航事件，忽略超时

            # 7. 获取结果页面，检查是否需要 2FA
            result_state = await browser_manager.get_page_state()

            # 检查是否出现了验证码输入框
            has_2fa_input = False
            for elem in result_state.elements:
                if elem.tag == "input" and elem.element_type in (
                    "text", "tel", "number", None
                ):
                    text_lower = (elem.text or elem.aria_label or elem.name or "").lower()
                    if any(
                        kw in text_lower
                        for kw in ("验证", "code", "verify", "otp", "2fa", "mfa")
                    ):
                        has_2fa_input = True
                        break

            await _publish("browser.login", {"service": service})

            if has_2fa_input:
                return ToolExecutionResult(
                    success=True,
                    payload={
                        "output": result_state.to_llm_text(),
                        "needs_2fa": True,
                        "message": "需要验证码，请提供验证码后继续",
                    },
                )

            return ToolExecutionResult(
                success=True,
                payload={
                    "output": result_state.to_llm_text(),
                    "message": f"{service} 登录流程已完成",
                },
            )

        except BrowserError as exc:
            return _browser_error_result(exc)

    registry.register(
        ToolSpec(
            name="browser_login",
            description=(
                "使用凭据保险库中保存的账号密码自动登录指定服务。"
                "支持自动检测 2FA 验证码页面。"
                "凭据由 Kevin 预先配置，LLM 看不到明文密码。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "服务名称，如 github、google 等",
                    },
                },
                "required": ["service"],
            },
            executor=browser_login,
            capability="browser",
            risk_level="high",
        )
    )
