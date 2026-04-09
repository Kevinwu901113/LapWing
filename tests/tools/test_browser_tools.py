"""tests/tools/test_browser_tools.py — 浏览器工具集测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.browser_manager import (
    BrowserElementNotFoundError,
    BrowserNavigationError,
    InteractiveElement,
    PageState,
    TabInfo,
)
from src.guards.browser_guard import GuardResult
from src.tools.browser_tools import register_browser_tools
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)


# ── 测试辅助工具 ─────────────────────────────────────────────────────────────


def _make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
    )


def _make_request(name: str, **kwargs) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=kwargs)


def _make_page_state(
    url: str = "https://example.com",
    title: str = "Example",
    elements: list[InteractiveElement] | None = None,
) -> PageState:
    return PageState(
        url=url,
        title=title,
        elements=elements or [],
        text_summary="Test page content",
        visual_description=None,
        scroll_position="top",
        has_more_below=False,
        tab_id="tab-1",
        timestamp="2026-04-09T12:00:00",
        is_image_heavy=False,
    )


def _make_login_page_state() -> PageState:
    """创建带登录表单元素的 PageState。"""
    return _make_page_state(
        url="https://github.com/login",
        title="Sign in to GitHub",
        elements=[
            InteractiveElement(
                index=1, tag="input", element_type="text",
                text="Username", name="login", aria_label=None,
                href=None, value=None, is_visible=True, selector="#login_field",
            ),
            InteractiveElement(
                index=2, tag="input", element_type="password",
                text="Password", name="password", aria_label=None,
                href=None, value=None, is_visible=True, selector="#password",
            ),
            InteractiveElement(
                index=3, tag="button", element_type="submit",
                text="Sign in", name=None, aria_label=None,
                href=None, value=None, is_visible=True, selector="button[type=submit]",
            ),
        ],
    )


def _make_result_page_state() -> PageState:
    """登录成功后的结果页面。"""
    return _make_page_state(
        url="https://github.com/dashboard",
        title="GitHub Dashboard",
    )


def _register_and_get_executors(
    mock_bm: AsyncMock,
    mock_vault: MagicMock | None = None,
    mock_guard: MagicMock | None = None,
    mock_event_bus: MagicMock | None = None,
) -> dict[str, any]:
    """注册工具并返回 name -> executor 映射。"""
    mock_registry = MagicMock()
    register_browser_tools(
        mock_registry, mock_bm, mock_vault, mock_guard, mock_event_bus
    )

    executors = {}
    for call in mock_registry.register.call_args_list:
        spec = call[0][0]
        executors[spec.name] = spec.executor
    return executors


# ── 注册测试 ─────────────────────────────────────────────────────────────────


class TestRegisterBrowserTools:
    def test_register_browser_tools(self):
        """验证 13 个工具全部注册。"""
        mock_registry = MagicMock()
        mock_bm = AsyncMock()
        register_browser_tools(mock_registry, mock_bm)

        assert mock_registry.register.call_count == 13

        registered_names = {
            call[0][0].name for call in mock_registry.register.call_args_list
        }
        expected_names = {
            "browser_open", "browser_click", "browser_type", "browser_select",
            "browser_scroll", "browser_screenshot", "browser_get_text",
            "browser_back", "browser_tabs", "browser_switch_tab",
            "browser_close_tab", "browser_wait", "browser_login",
        }
        assert registered_names == expected_names

        # 验证所有工具的 capability 都是 browser
        for call in mock_registry.register.call_args_list:
            spec = call[0][0]
            assert spec.capability == "browser"


# ── browser_open 测试 ────────────────────────────────────────────────────────


class TestBrowserOpen:
    async def test_browser_open_success(self):
        """导航成功，返回页面状态文本。"""
        mock_bm = AsyncMock()
        page_state = _make_page_state()
        mock_bm.navigate.return_value = page_state

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_open", url="https://example.com")
        result = await executors["browser_open"](req, ctx)

        assert result.success is True
        assert "Example" in result.payload["output"]
        mock_bm.navigate.assert_called_once_with("https://example.com")

    async def test_browser_open_blocked_url(self):
        """Guard 拦截 URL，返回错误。"""
        mock_bm = AsyncMock()
        mock_guard = MagicMock()
        mock_guard.check_url.return_value = GuardResult(
            action="block", reason="禁止访问内网地址"
        )

        executors = _register_and_get_executors(mock_bm, mock_guard=mock_guard)
        ctx = _make_context()
        req = _make_request("browser_open", url="http://192.168.1.1")
        result = await executors["browser_open"](req, ctx)

        assert result.success is False
        assert "禁止访问内网地址" in result.payload["error"]
        mock_bm.navigate.assert_not_called()

    async def test_browser_open_missing_url(self):
        """缺少 url 参数。"""
        mock_bm = AsyncMock()
        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_open")
        result = await executors["browser_open"](req, ctx)

        assert result.success is False
        assert "url" in result.payload["error"]


# ── browser_click 测试 ───────────────────────────────────────────────────────


class TestBrowserClick:
    async def test_browser_click_success(self):
        """点击成功，返回新页面状态。"""
        mock_bm = AsyncMock()
        page_state = _make_page_state()
        mock_bm.click.return_value = page_state

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_click", element="[3]")
        result = await executors["browser_click"](req, ctx)

        assert result.success is True
        mock_bm.click.assert_called_once_with("[3]", None)

    async def test_browser_click_consent_required(self):
        """Guard 要求确认，返回 requires_consent 标记。"""
        mock_bm = AsyncMock()
        mock_guard = MagicMock()

        # get_page_state 返回含支付按钮的页面
        page_state = _make_page_state(
            elements=[
                InteractiveElement(
                    index=3, tag="button", element_type=None,
                    text="立即支付", name=None, aria_label=None,
                    href=None, value=None, is_visible=True, selector="button.pay",
                ),
            ]
        )
        mock_bm.get_page_state.return_value = page_state
        mock_guard.check_action.return_value = GuardResult(
            action="require_consent",
            reason="元素文本包含敏感词「支付」，需要用户确认",
        )

        executors = _register_and_get_executors(mock_bm, mock_guard=mock_guard)
        ctx = _make_context()
        req = _make_request("browser_click", element="[3]")
        result = await executors["browser_click"](req, ctx)

        assert result.success is False
        assert result.payload.get("requires_consent") is True
        assert "支付" in result.payload["error"]
        mock_bm.click.assert_not_called()


# ── browser_type 测试 ────────────────────────────────────────────────────────


class TestBrowserType:
    async def test_browser_type_success(self):
        """输入文本成功。"""
        mock_bm = AsyncMock()
        page_state = _make_page_state()
        mock_bm.type_text.return_value = page_state

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request(
            "browser_type", element="[5]", text="hello world"
        )
        result = await executors["browser_type"](req, ctx)

        assert result.success is True
        mock_bm.type_text.assert_called_once_with(
            "[5]", "hello world", press_enter=False, tab_id=None
        )

    async def test_browser_type_with_enter(self):
        """输入后按回车。"""
        mock_bm = AsyncMock()
        page_state = _make_page_state()
        mock_bm.type_text.return_value = page_state

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request(
            "browser_type", element="[2]", text="search query", press_enter=True
        )
        result = await executors["browser_type"](req, ctx)

        assert result.success is True
        mock_bm.type_text.assert_called_once_with(
            "[2]", "search query", press_enter=True, tab_id=None
        )


# ── browser_login 测试 ───────────────────────────────────────────────────────


class TestBrowserLogin:
    async def test_browser_login_success(self):
        """完整登录流程成功。"""
        mock_bm = AsyncMock()
        mock_vault = MagicMock()

        # 配置凭据
        credential = MagicMock()
        credential.login_url = "https://github.com/login"
        credential.username = "kevin"
        credential.password = "secret123"
        mock_vault.get.return_value = credential

        # 导航返回登录页
        login_page = _make_login_page_state()
        mock_bm.navigate.return_value = login_page

        # get_page_state 返回不同阶段的页面
        result_page = _make_result_page_state()
        mock_bm.get_page_state.side_effect = [login_page, result_page]

        # type_text 和 click 返回页面状态
        mock_bm.type_text.return_value = login_page
        mock_bm.click.return_value = result_page
        mock_bm.wait_for.return_value = True

        executors = _register_and_get_executors(mock_bm, mock_vault=mock_vault)
        ctx = _make_context()
        req = _make_request("browser_login", service="github")
        result = await executors["browser_login"](req, ctx)

        assert result.success is True
        assert "Dashboard" in result.payload["output"]
        mock_vault.get.assert_called_once_with("github")
        mock_bm.navigate.assert_called_once_with("https://github.com/login")
        # 应该输入了用户名和密码
        assert mock_bm.type_text.call_count == 2

    async def test_browser_login_no_vault(self):
        """没有凭据保险库时返回错误。"""
        mock_bm = AsyncMock()
        executors = _register_and_get_executors(mock_bm, mock_vault=None)
        ctx = _make_context()
        req = _make_request("browser_login", service="github")
        result = await executors["browser_login"](req, ctx)

        assert result.success is False
        assert "凭据保险库未配置" in result.payload["error"]

    async def test_browser_login_service_not_found(self):
        """凭据保险库中找不到指定服务。"""
        mock_bm = AsyncMock()
        mock_vault = MagicMock()
        mock_vault.get.return_value = None

        executors = _register_and_get_executors(mock_bm, mock_vault=mock_vault)
        ctx = _make_context()
        req = _make_request("browser_login", service="unknown_service")
        result = await executors["browser_login"](req, ctx)

        assert result.success is False
        assert "unknown_service" in result.payload["error"]


# ── browser_screenshot 测试 ──────────────────────────────────────────────────


class TestBrowserScreenshot:
    async def test_browser_screenshot(self):
        """截图成功，返回文件路径。"""
        mock_bm = AsyncMock()
        mock_bm.screenshot.return_value = "/data/screenshots/screenshot_tab1_20260409.png"

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_screenshot")
        result = await executors["browser_screenshot"](req, ctx)

        assert result.success is True
        assert result.payload["path"] == "/data/screenshots/screenshot_tab1_20260409.png"
        assert result.payload["message"] == "截图已保存"


# ── browser_tabs 测试 ────────────────────────────────────────────────────────


class TestBrowserTabs:
    async def test_browser_tabs(self):
        """列出标签页。"""
        mock_bm = AsyncMock()
        mock_bm.list_tabs.return_value = [
            TabInfo(
                tab_id="tab-1", url="https://example.com",
                title="Example", is_active=True,
            ),
            TabInfo(
                tab_id="tab-2", url="https://github.com",
                title="GitHub", is_active=False,
            ),
        ]

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_tabs")
        result = await executors["browser_tabs"](req, ctx)

        assert result.success is True
        assert result.payload["count"] == 2
        assert "tab-1" in result.payload["output"]
        assert "tab-2" in result.payload["output"]
        assert "当前" in result.payload["output"]

    async def test_browser_tabs_empty(self):
        """没有打开的标签页。"""
        mock_bm = AsyncMock()
        mock_bm.list_tabs.return_value = []

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_tabs")
        result = await executors["browser_tabs"](req, ctx)

        assert result.success is True
        assert "没有打开的标签页" in result.payload["output"]


# ── 错误处理测试 ─────────────────────────────────────────────────────────────


class TestBrowserErrors:
    async def test_browser_navigation_error(self):
        """导航失败时返回人话错误。"""
        mock_bm = AsyncMock()
        mock_bm.navigate.side_effect = BrowserNavigationError(
            "导航失败: https://bad.url — net::ERR_NAME_NOT_RESOLVED"
        )

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_open", url="https://bad.url")
        result = await executors["browser_open"](req, ctx)

        assert result.success is False
        assert "打不开这个网页" in result.payload["error"]

    async def test_browser_element_not_found(self):
        """找不到元素时返回人话错误。"""
        mock_bm = AsyncMock()
        mock_bm.click.side_effect = BrowserElementNotFoundError(
            "元素不存在: [99]"
        )

        executors = _register_and_get_executors(mock_bm)
        ctx = _make_context()
        req = _make_request("browser_click", element="[99]")
        result = await executors["browser_click"](req, ctx)

        assert result.success is False
        assert "找不到" in result.payload["error"]
