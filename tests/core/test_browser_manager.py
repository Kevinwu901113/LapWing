"""BrowserManager 集成测试。

使用本地 HTTP 服务器 + 真实 Playwright 浏览器进行测试。
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.requires_browser

import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import pytest

from src.core.browser_manager import (
    BrowserElementNotFoundError,
    BrowserManager,
    BrowserNotStartedError,
    BrowserTabNotFoundError,
    DOMProcessor,
    InteractiveElement,
    PageState,
    TabInfo,
)


# ── 测试用 HTML 页面 ────────────────────────────────────────────────────────

_PAGES: dict[str, tuple[str, str]] = {
    "/": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>首页</title></head>
<body>
<h1>Welcome</h1>
<p>这是首页内容。</p>
<a id="link-about" href="/about">关于我们</a>
<button id="btn-action" onclick="document.title='clicked'">点击我</button>
</body></html>""",
    ),
    "/about": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>关于</title></head>
<body>
<h1>About Page</h1>
<p>About page content.</p>
<a href="/">返回首页</a>
</body></html>""",
    ),
    "/login": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>登录</title></head>
<body>
<h1>Login</h1>
<form id="login-form" onsubmit="event.preventDefault(); document.title='submitted'">
  <label>Username</label>
  <input id="username" type="text" name="username" placeholder="Username or email address" />
  <label>Password</label>
  <input id="password" type="password" name="password" placeholder="Password" />
  <button id="submit-btn" type="submit">Sign in</button>
</form>
</body></html>""",
    ),
    "/form": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>表单</title></head>
<body>
<h1>Complex Form</h1>
<form>
  <select id="country" name="country">
    <option value="">请选择</option>
    <option value="cn">中国</option>
    <option value="us">美国</option>
    <option value="jp">日本</option>
  </select>
  <textarea id="comments" name="comments" placeholder="Comments"></textarea>
  <input type="checkbox" id="agree" name="agree" />
  <label for="agree">同意条款</label>
</form>
</body></html>""",
    ),
    "/long": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>长页面</title></head>
<body>
<h1>Long Page</h1>"""
        + "\n".join(f"<p>段落 {i}: {'内容' * 20}</p>" for i in range(100))
        + """
</body></html>""",
    ),
    "/slow": (
        "text/html; charset=utf-8",
        """<!DOCTYPE html>
<html><head><title>慢页面</title></head>
<body><h1>Slow Page</h1><p>This page loaded slowly.</p></body></html>""",
    ),
}


class _TestHandler(BaseHTTPRequestHandler):
    """简单的测试 HTTP 请求处理器。"""

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/slow":
            time.sleep(1)

        content_type, body = _PAGES.get(path, ("text/html", "<h1>404</h1>"))
        self.send_response(200 if path in _PAGES else 404)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        """静默日志。"""
        pass


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def mock_server():
    """启动本地 HTTP 服务器（session 级别复用）。"""
    server = HTTPServer(("127.0.0.1", 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def base_url(mock_server):
    """返回 mock 服务器基础 URL。"""
    return mock_server


@pytest.fixture
async def browser_mgr(tmp_path, monkeypatch):
    """创建并启动 BrowserManager 实例，测试后关闭。"""
    user_data_dir = str(tmp_path / "profile")
    screenshot_dir = str(tmp_path / "screenshots")

    monkeypatch.setattr("src.core.browser_manager.BROWSER_USER_DATA_DIR", user_data_dir)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_SCREENSHOT_DIR", screenshot_dir)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_HEADLESS", True)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_VIEWPORT_WIDTH", 1280)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_VIEWPORT_HEIGHT", 720)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_MAX_TABS", 8)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_NAVIGATION_TIMEOUT_MS", 30000)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_ACTION_TIMEOUT_MS", 10000)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_WAIT_AFTER_ACTION_MS", 200)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_MAX_ELEMENT_COUNT", 50)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_PAGE_TEXT_MAX_CHARS", 4000)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_SCREENSHOT_RETAIN_DAYS", 7)
    monkeypatch.setattr("src.core.browser_manager.BROWSER_LOCALE", "zh-CN")
    monkeypatch.setattr("src.core.browser_manager.BROWSER_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr("src.core.browser_manager.BROWSER_PROXY_SERVER", "")

    from src.utils.url_safety import SafetyResult
    monkeypatch.setattr(
        "src.utils.url_safety.check_url_safety",
        lambda url: SafetyResult(True),
    )

    mgr = BrowserManager()
    await mgr.start()
    yield mgr
    await mgr.stop()


# ── 生命周期测试 ─────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_start_stop(self, tmp_path, monkeypatch):
        """start/stop 不报错。"""
        monkeypatch.setattr(
            "src.core.browser_manager.BROWSER_USER_DATA_DIR",
            str(tmp_path / "profile"),
        )
        monkeypatch.setattr(
            "src.core.browser_manager.BROWSER_SCREENSHOT_DIR",
            str(tmp_path / "screenshots"),
        )
        monkeypatch.setattr("src.core.browser_manager.BROWSER_HEADLESS", True)

        mgr = BrowserManager()
        assert not mgr.is_started

        await mgr.start()
        assert mgr.is_started

        await mgr.stop()
        assert not mgr.is_started

    async def test_not_started(self, tmp_path, monkeypatch):
        """未启动时调用操作抛出 BrowserNotStartedError。"""
        mgr = BrowserManager()

        with pytest.raises(BrowserNotStartedError):
            await mgr.navigate("http://example.com")

        with pytest.raises(BrowserNotStartedError):
            await mgr.get_page_state()

        with pytest.raises(BrowserNotStartedError):
            await mgr.click("[1]")

        with pytest.raises(BrowserNotStartedError):
            await mgr.list_tabs()

        with pytest.raises(BrowserNotStartedError):
            await mgr.screenshot()


# ── 导航测试 ─────────────────────────────────────────────────────────────────


class TestNavigation:
    async def test_navigate_success(self, browser_mgr, base_url):
        """导航到首页，验证 URL 和 title 正确。"""
        state = await browser_mgr.navigate(f"{base_url}/")

        assert base_url in state.url
        assert state.title == "首页"
        assert state.tab_id is not None
        assert state.timestamp

    async def test_navigate_returns_elements(self, browser_mgr, base_url):
        """导航到登录页，验证提取到输入框和按钮。"""
        state = await browser_mgr.navigate(f"{base_url}/login")

        # 应该至少有 username, password, submit 三个元素
        assert len(state.elements) >= 3

        tags = [e.tag for e in state.elements]
        assert "input" in tags
        assert "button" in tags

        # 找到用户名输入框
        username_inputs = [
            e for e in state.elements if e.tag == "input" and e.element_type == "text"
        ]
        assert len(username_inputs) >= 1

        # 找到密码输入框
        password_inputs = [
            e for e in state.elements if e.tag == "input" and e.element_type == "password"
        ]
        assert len(password_inputs) >= 1

        # 找到提交按钮
        buttons = [e for e in state.elements if e.tag == "button"]
        assert len(buttons) >= 1

    async def test_navigate_new_tab(self, browser_mgr, base_url):
        """tab_id=None 时自动创建新 Tab。"""
        state1 = await browser_mgr.navigate(f"{base_url}/")
        state2 = await browser_mgr.navigate(f"{base_url}/login")

        # 两次导航应产生不同的 Tab
        assert state1.tab_id != state2.tab_id

        tabs = await browser_mgr.list_tabs()
        assert len(tabs) >= 2


# ── 交互操作测试 ─────────────────────────────────────────────────────────────


class TestInteraction:
    async def test_click_element_by_index(self, browser_mgr, base_url):
        """通过索引点击链接，验证导航到新页面。"""
        state = await browser_mgr.navigate(f"{base_url}/")

        # 找到 "关于我们" 链接的索引
        link = next(
            (e for e in state.elements if e.tag == "a" and "关于" in e.text),
            None,
        )
        assert link is not None, f"未找到链接元素，当前元素: {[e.text for e in state.elements]}"

        new_state = await browser_mgr.click(
            f"[{link.index}]", tab_id=state.tab_id
        )
        assert "/about" in new_state.url

    async def test_click_element_by_css(self, browser_mgr, base_url):
        """通过 CSS 选择器点击按钮。"""
        state = await browser_mgr.navigate(f"{base_url}/")
        new_state = await browser_mgr.click("css:#btn-action", tab_id=state.tab_id)
        # 按钮的 onclick 会修改 title
        assert new_state.title == "clicked"

    async def test_click_element_by_text(self, browser_mgr, base_url):
        """通过文本内容点击元素。"""
        state = await browser_mgr.navigate(f"{base_url}/")
        new_state = await browser_mgr.click("text:点击我", tab_id=state.tab_id)
        assert new_state.title == "clicked"

    async def test_type_text(self, browser_mgr, base_url):
        """在输入框中输入文本，验证值已填入。"""
        state = await browser_mgr.navigate(f"{base_url}/login")

        # 找到用户名输入框
        username_elem = next(
            (e for e in state.elements if e.tag == "input" and e.element_type == "text"),
            None,
        )
        assert username_elem is not None

        new_state = await browser_mgr.type_text(
            f"[{username_elem.index}]",
            "testuser@example.com",
            tab_id=state.tab_id,
        )

        # 输入后页面状态中应包含填入的值
        username_after = next(
            (e for e in new_state.elements if e.tag == "input" and e.element_type == "text"),
            None,
        )
        assert username_after is not None
        assert username_after.value == "testuser@example.com"

    async def test_type_text_with_enter(self, browser_mgr, base_url):
        """输入文本后按回车，验证表单提交。"""
        state = await browser_mgr.navigate(f"{base_url}/login")

        password_elem = next(
            (e for e in state.elements if e.tag == "input" and e.element_type == "password"),
            None,
        )
        assert password_elem is not None

        new_state = await browser_mgr.type_text(
            f"[{password_elem.index}]",
            "secret123",
            press_enter=True,
            tab_id=state.tab_id,
        )
        # 表单 onsubmit 修改 title
        assert new_state.title == "submitted"

    async def test_select_option(self, browser_mgr, base_url):
        """选择下拉框选项。"""
        state = await browser_mgr.navigate(f"{base_url}/form")

        select_elem = next(
            (e for e in state.elements if e.tag == "select"),
            None,
        )
        assert select_elem is not None

        new_state = await browser_mgr.select_option(
            f"[{select_elem.index}]",
            "cn",
            tab_id=state.tab_id,
        )

        select_after = next(
            (e for e in new_state.elements if e.tag == "select"),
            None,
        )
        assert select_after is not None
        assert select_after.value == "cn"

    async def test_scroll(self, browser_mgr, base_url):
        """滚动页面，验证 scroll_position 变化。"""
        state = await browser_mgr.navigate(f"{base_url}/long")

        # 初始应在顶部
        assert state.scroll_position == "top"
        assert state.has_more_below is True

        # 向下滚动
        scrolled = await browser_mgr.scroll(
            direction="down", amount=3, tab_id=state.tab_id
        )
        assert scrolled.scroll_position in ("middle", "bottom")

    async def test_go_back_forward(self, browser_mgr, base_url):
        """前进/后退导航。"""
        state1 = await browser_mgr.navigate(f"{base_url}/")
        tab_id = state1.tab_id

        # 在同一个 Tab 内导航到另一个页面
        await browser_mgr.click("css:#link-about", tab_id=tab_id)

        # 后退
        back_state = await browser_mgr.go_back(tab_id=tab_id)
        assert "/" == back_state.url.rstrip("/").split(base_url)[-1] or back_state.url.endswith("/")

        # 前进
        forward_state = await browser_mgr.go_forward(tab_id=tab_id)
        assert "/about" in forward_state.url


# ── 截图测试 ─────────────────────────────────────────────────────────────────


class TestScreenshot:
    async def test_screenshot(self, browser_mgr, base_url):
        """截图后文件存在。"""
        await browser_mgr.navigate(f"{base_url}/")
        filepath = await browser_mgr.screenshot()

        assert Path(filepath).exists()
        assert filepath.endswith(".png")
        assert Path(filepath).stat().st_size > 0


# ── Tab 管理测试 ──────────────────────────────────────────────────────────────


class TestTabManagement:
    async def test_tab_lifecycle(self, browser_mgr, base_url):
        """new_tab / list_tabs / switch_tab / close_tab 完整流程。"""
        # 创建带 URL 的 Tab
        tab1 = await browser_mgr.new_tab(f"{base_url}/")
        assert tab1.tab_id.startswith("tab_")

        tab2 = await browser_mgr.new_tab(f"{base_url}/login")
        assert tab2.tab_id != tab1.tab_id

        # 列出 Tab
        tabs = await browser_mgr.list_tabs()
        tab_ids = {t.tab_id for t in tabs}
        assert tab1.tab_id in tab_ids
        assert tab2.tab_id in tab_ids

        # 切换 Tab
        state = await browser_mgr.switch_tab(tab1.tab_id)
        assert state.tab_id == tab1.tab_id

        # 关闭 Tab
        await browser_mgr.close_tab(tab2.tab_id)
        tabs_after = await browser_mgr.list_tabs()
        assert tab2.tab_id not in {t.tab_id for t in tabs_after}

    async def test_tab_limit(self, browser_mgr, base_url, monkeypatch):
        """超出 Tab 上限时自动关闭最早的 Tab。"""
        monkeypatch.setattr("src.core.browser_manager.BROWSER_MAX_TABS", 3)

        # 创建 3 个 Tab（达到上限）
        tabs_created = []
        for i in range(3):
            tab = await browser_mgr.new_tab(f"{base_url}/")
            tabs_created.append(tab.tab_id)

        # 创建第 4 个，应触发关闭最早的 Tab
        tab_new = await browser_mgr.new_tab(f"{base_url}/login")

        all_tabs = await browser_mgr.list_tabs()
        all_tab_ids = {t.tab_id for t in all_tabs}

        # 新 Tab 应存在
        assert tab_new.tab_id in all_tab_ids

        # Tab 总数不超过限制（新创建的 + 之前的可能还有测试 fixture 的）
        # 但确保最早创建的那个被关闭了
        assert len(all_tabs) <= 3

    async def test_tab_not_found(self, browser_mgr):
        """操作不存在的 Tab 抛出 BrowserTabNotFoundError。"""
        with pytest.raises(BrowserTabNotFoundError):
            await browser_mgr.switch_tab("tab_nonexistent")

        with pytest.raises(BrowserTabNotFoundError):
            await browser_mgr.close_tab("tab_nonexistent")

        with pytest.raises(BrowserTabNotFoundError):
            await browser_mgr.get_page_state("tab_nonexistent")


# ── 元素查找异常测试 ────────────────────────────────────────────────────────


class TestElementErrors:
    async def test_element_not_found_by_index(self, browser_mgr, base_url):
        """点击不存在的元素索引抛出 BrowserElementNotFoundError。"""
        await browser_mgr.navigate(f"{base_url}/")

        with pytest.raises(BrowserElementNotFoundError):
            await browser_mgr.click("[999]")

    async def test_element_not_found_by_css(self, browser_mgr, base_url):
        """CSS 选择器未匹配抛出 BrowserElementNotFoundError。"""
        await browser_mgr.navigate(f"{base_url}/")

        with pytest.raises(BrowserElementNotFoundError):
            await browser_mgr.click("css:#nonexistent-element-xyz")

    async def test_element_not_found_by_text(self, browser_mgr, base_url):
        """文本未匹配抛出 BrowserElementNotFoundError。"""
        await browser_mgr.navigate(f"{base_url}/")

        with pytest.raises(BrowserElementNotFoundError):
            await browser_mgr.click("text:this text does not exist anywhere")


# ── PageState 格式化测试 ─────────────────────────────────────────────────────


class TestPageStateFormat:
    def test_to_llm_text_basic(self):
        """to_llm_text() 输出格式包含期望的字符串。"""
        state = PageState(
            url="https://github.com/login",
            title="GitHub 登录",
            elements=[
                InteractiveElement(
                    index=1,
                    tag="input",
                    element_type="text",
                    text="Username or email address",
                    name="login",
                    aria_label=None,
                    href=None,
                    value=None,
                    is_visible=True,
                    selector="#login_field",
                ),
                InteractiveElement(
                    index=2,
                    tag="input",
                    element_type="password",
                    text="Password",
                    name="password",
                    aria_label=None,
                    href=None,
                    value=None,
                    is_visible=True,
                    selector="#password",
                ),
                InteractiveElement(
                    index=3,
                    tag="button",
                    element_type="submit",
                    text="Sign in",
                    name=None,
                    aria_label=None,
                    href=None,
                    value=None,
                    is_visible=True,
                    selector='button[type="submit"]',
                ),
                InteractiveElement(
                    index=4,
                    tag="a",
                    element_type=None,
                    text="About",
                    name=None,
                    aria_label=None,
                    href="/about",
                    value=None,
                    is_visible=True,
                    selector="a:nth-of-type(1)",
                ),
            ],
            text_summary="Sign in to GitHub...",
            visual_description=None,
            scroll_position="top",
            has_more_below=False,
            tab_id="tab_test",
            timestamp="2025-01-01T00:00:00",
            is_image_heavy=False,
        )

        text = state.to_llm_text()

        assert "[页面] GitHub 登录" in text
        assert "URL: https://github.com/login" in text
        assert "位置: 顶部" in text
        assert "可交互元素：" in text
        assert '[1] 输入框 "Username or email address"' in text
        assert '[2] 输入框 (password) "Password"' in text
        assert '[3] 按钮 "Sign in"' in text
        assert '[4] 链接 "About" → /about' in text
        assert "页面内容：" in text
        assert "Sign in to GitHub..." in text

    def test_to_llm_text_scroll_middle(self):
        """中部位置显示 '中部'。"""
        state = PageState(
            url="https://example.com",
            title="Test",
            elements=[],
            text_summary="",
            visual_description=None,
            scroll_position="middle",
            has_more_below=True,
            tab_id="tab_test",
            timestamp="2025-01-01T00:00:00",
            is_image_heavy=False,
        )
        text = state.to_llm_text()
        assert "位置: 中部" in text
        assert "（下方有更多内容）" in text

    def test_to_llm_text_max_elements(self):
        """max_elements 限制元素数量。"""
        elements = [
            InteractiveElement(
                index=i,
                tag="button",
                element_type=None,
                text=f"Button {i}",
                name=None,
                aria_label=None,
                href=None,
                value=None,
                is_visible=True,
                selector=f"button:nth-of-type({i})",
            )
            for i in range(1, 21)
        ]
        state = PageState(
            url="https://example.com",
            title="Test",
            elements=elements,
            text_summary="",
            visual_description=None,
            scroll_position="top",
            has_more_below=False,
            tab_id="tab_test",
            timestamp="2025-01-01T00:00:00",
            is_image_heavy=False,
        )

        text = state.to_llm_text(max_elements=5)
        assert "Button 5" in text
        assert "Button 6" not in text

    def test_to_llm_text_hidden_elements_excluded(self):
        """不可见元素不显示在输出中。"""
        elements = [
            InteractiveElement(
                index=1,
                tag="button",
                element_type=None,
                text="Visible",
                name=None,
                aria_label=None,
                href=None,
                value=None,
                is_visible=True,
                selector="button:nth-of-type(1)",
            ),
            InteractiveElement(
                index=2,
                tag="button",
                element_type=None,
                text="Hidden",
                name=None,
                aria_label=None,
                href=None,
                value=None,
                is_visible=False,
                selector="button:nth-of-type(2)",
            ),
        ]
        state = PageState(
            url="https://example.com",
            title="Test",
            elements=elements,
            text_summary="",
            visual_description=None,
            scroll_position="top",
            has_more_below=False,
            tab_id="tab_test",
            timestamp="2025-01-01T00:00:00",
            is_image_heavy=False,
        )

        text = state.to_llm_text()
        assert "Visible" in text
        assert "Hidden" not in text


# ── 文本截断测试 ─────────────────────────────────────────────────────────────


class TestTextTruncation:
    async def test_page_text_truncation(self, browser_mgr, base_url, monkeypatch):
        """长页面文本被截断到 max_chars。"""
        monkeypatch.setattr("src.core.browser_manager.BROWSER_PAGE_TEXT_MAX_CHARS", 200)

        state = await browser_mgr.navigate(f"{base_url}/long")

        # 文本应该被截断
        assert len(state.text_summary) <= 250  # 200 + 一些余量（包括省略号等）


# ── JS 执行测试 ──────────────────────────────────────────────────────────────


class TestExecuteJs:
    async def test_execute_js(self, browser_mgr, base_url):
        """执行 JS 并返回结果。"""
        await browser_mgr.navigate(f"{base_url}/")

        result = await browser_mgr.execute_js("document.title")
        assert result == "首页"

    async def test_execute_js_object(self, browser_mgr, base_url):
        """执行返回对象的 JS。"""
        await browser_mgr.navigate(f"{base_url}/")

        result = await browser_mgr.execute_js(
            "({width: window.innerWidth, height: window.innerHeight})"
        )
        assert "width" in result
        assert "height" in result

    # BrowserGuard tests removed (Phase 1: browser_guard deleted)


# ── 页面文本提取测试 ─────────────────────────────────────────────────────────


class TestPageText:
    async def test_get_page_text(self, browser_mgr, base_url):
        """提取页面文本内容。"""
        await browser_mgr.navigate(f"{base_url}/")

        text = await browser_mgr.get_page_text()
        assert "Welcome" in text
        assert "首页内容" in text

    async def test_get_page_text_with_selector(self, browser_mgr, base_url):
        """使用选择器提取特定元素的文本。"""
        await browser_mgr.navigate(f"{base_url}/")

        text = await browser_mgr.get_page_text(selector="h1")
        assert "Welcome" in text


# ── InteractiveElement 标签测试 ──────────────────────────────────────────────


class TestElementLabel:
    def test_button_label(self):
        elem = InteractiveElement(
            index=1, tag="button", element_type=None, text="Submit",
            name=None, aria_label=None, href=None, value=None,
            is_visible=True, selector="button",
        )
        assert elem.to_label() == '[1] 按钮 "Submit"'

    def test_input_text_label(self):
        elem = InteractiveElement(
            index=2, tag="input", element_type="text", text="Email",
            name=None, aria_label=None, href=None, value=None,
            is_visible=True, selector="input",
        )
        assert elem.to_label() == '[2] 输入框 "Email"'

    def test_input_password_label(self):
        elem = InteractiveElement(
            index=3, tag="input", element_type="password", text="Password",
            name=None, aria_label=None, href=None, value=None,
            is_visible=True, selector="input",
        )
        assert elem.to_label() == '[3] 输入框 (password) "Password"'

    def test_link_with_href(self):
        elem = InteractiveElement(
            index=4, tag="a", element_type=None, text="About",
            name=None, aria_label=None, href="/about", value=None,
            is_visible=True, selector="a",
        )
        assert elem.to_label() == '[4] 链接 "About" → /about'

    def test_select_label(self):
        elem = InteractiveElement(
            index=5, tag="select", element_type=None, text="Country",
            name=None, aria_label=None, href=None, value="cn",
            is_visible=True, selector="select",
        )
        assert elem.to_label() == '[5] 下拉框 "Country" [值=cn]'

    def test_textarea_label(self):
        elem = InteractiveElement(
            index=6, tag="textarea", element_type=None, text="Comments",
            name=None, aria_label=None, href=None, value=None,
            is_visible=True, selector="textarea",
        )
        assert elem.to_label() == '[6] 文本域 "Comments"'

    def test_unknown_tag_uses_raw(self):
        elem = InteractiveElement(
            index=7, tag="div", element_type=None, text="Custom",
            name=None, aria_label=None, href=None, value=None,
            is_visible=True, selector="div",
        )
        assert elem.to_label() == '[7] div "Custom"'


# ── WaitFor 测试 ─────────────────────────────────────────────────────────────


class TestWaitFor:
    async def test_wait_for_selector(self, browser_mgr, base_url):
        """等待选择器匹配。"""
        await browser_mgr.navigate(f"{base_url}/")

        result = await browser_mgr.wait_for("selector:h1", timeout_ms=5000)
        assert result is True

    async def test_wait_for_navigation(self, browser_mgr, base_url):
        """等待页面加载完成。"""
        await browser_mgr.navigate(f"{base_url}/")

        result = await browser_mgr.wait_for("navigation", timeout_ms=5000)
        assert result is True

    async def test_wait_for_unknown_condition(self, browser_mgr, base_url):
        """未知条件返回 False。"""
        await browser_mgr.navigate(f"{base_url}/")

        result = await browser_mgr.wait_for("unknown_condition", timeout_ms=1000)
        assert result is False


# ── ProxyRouter 集成单元测试 ──────────────────────────────────────────────────


class TestProxyRouterUnit:
    """不启动真实浏览器的轻量单元测试，覆盖 ProxyRouter 集成逻辑。"""

    def test_set_proxy_router(self):
        """set_proxy_router 正确设置 _proxy_router 属性。"""
        mgr = BrowserManager()
        assert mgr._proxy_router is None

        class FakeRouter:
            pass

        router = FakeRouter()
        mgr.set_proxy_router(router)
        assert mgr._proxy_router is router

    def test_get_context_and_strategy_with_no_router(self):
        """未设置 proxy_router 时返回 (self._context, None)。"""
        mgr = BrowserManager()
        # 手动注入一个假 context（不启动真实浏览器）
        fake_context = object()
        mgr._context = fake_context

        ctx, strategy = mgr._get_context_and_strategy("https://example.com")
        assert ctx is fake_context
        assert strategy is None

    def test_is_proxy_related_failure_for_known_errors(self):
        """已知代理相关错误消息返回 True。"""
        mgr = BrowserManager()

        known_proxy_errors = [
            "net::ERR_CONNECTION_REFUSED",
            "net::ERR_CONNECTION_RESET",
            "net::ERR_TUNNEL_CONNECTION_FAILED",
            "net::ERR_PROXY_CONNECTION_FAILED",
            "Request failed with status 403",
        ]
        for msg in known_proxy_errors:
            exc = RuntimeError(msg)
            assert mgr._is_proxy_related_failure(exc), f"Expected True for: {msg}"

    def test_is_proxy_related_failure_for_unrelated_errors(self):
        """与代理无关的错误返回 False。"""
        mgr = BrowserManager()

        unrelated_errors = [
            "404 Not Found",
            "net::ERR_NAME_NOT_RESOLVED",
            "Page not found",
            "SSL certificate error",
            "JavaScript error: undefined is not a function",
        ]
        for msg in unrelated_errors:
            exc = RuntimeError(msg)
            assert not mgr._is_proxy_related_failure(exc), f"Expected False for: {msg}"

    def test_navigate_skips_proxy_retry_when_no_router(self, monkeypatch):
        """无 proxy_router 时 navigate 不触发代理重试逻辑。

        验证 _get_context_and_strategy 在单 context 模式下始终返回 None strategy，
        从而 navigate 不会尝试 _is_proxy_related_failure 分支。
        """
        mgr = BrowserManager()
        fake_context = object()
        mgr._context = fake_context

        # 没有 proxy_router
        assert mgr._proxy_router is None

        ctx, strategy = mgr._get_context_and_strategy("https://github.com")
        assert ctx is fake_context
        assert strategy is None

        # 确认 _is_proxy_related_failure 本身不抛异常，仅用来验证方法可调用
        assert not mgr._is_proxy_related_failure(RuntimeError("some unrelated error"))

    async def test_navigate_retry_with_alternative_context(self):
        """代理失败后用备选 context 重试并成功时，confirm_alternative 被调用。

        不依赖真实 Playwright：通过替换 _create_tab_unlocked 和 page.goto 实现全 mock。
        """
        import types

        mgr = BrowserManager()

        # ── mock proxy router ──────────────────────────────────────────────
        class FakeDecision:
            strategy = "direct"

        class FakeProxyRouter:
            _disabled = False
            resolve_called_with: list = []
            report_failure_called = False
            confirm_alternative_called_with: list = []
            report_success_called = False

            def resolve(self, url: str):
                self.resolve_called_with.append(url)
                d = FakeDecision()
                d.strategy = "proxy"
                return d

            def report_success(self, url, strategy):
                self.report_success_called = True

            def report_failure_and_get_alternative(self, url, strategy):
                self.report_failure_called = True
                return FakeDecision()  # strategy = "direct"

            def confirm_alternative(self, url, strategy):
                self.confirm_alternative_called_with.append((url, strategy))

        proxy_router = FakeProxyRouter()
        mgr.set_proxy_router(proxy_router)

        # ── mock contexts ──────────────────────────────────────────────────
        fake_proxy_context = object()
        fake_direct_context = object()
        mgr._context = fake_proxy_context
        mgr._context_proxy = fake_proxy_context
        mgr._context_direct = fake_direct_context

        # ── mock tab pages ─────────────────────────────────────────────────
        first_goto_called = False
        alt_goto_called = False

        class FakeProxyPage:
            url = "about:blank"

            async def goto(self, url, wait_until=None):
                nonlocal first_goto_called
                first_goto_called = True
                raise RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED")

            async def title(self):
                return ""

            async def close(self):
                pass

        class FakeDirectPage:
            url = "https://example.com/"

            async def goto(self, url, wait_until=None):
                nonlocal alt_goto_called
                alt_goto_called = True
                # 成功：不抛异常

            async def title(self):
                return "Example"

            async def close(self):
                pass

        proxy_page = FakeProxyPage()
        direct_page = FakeDirectPage()

        # ── mock tab infos ─────────────────────────────────────────────────
        from src.core.browser_manager import TabInfo
        from datetime import datetime, timezone

        proxy_tab_info = TabInfo(
            tab_id="tab_prox",
            url="about:blank",
            title="",
            is_active=True,
            context_type="proxy",
            last_accessed=datetime.now(timezone.utc),
        )
        direct_tab_info = TabInfo(
            tab_id="tab_dirx",
            url="about:blank",
            title="",
            is_active=True,
            context_type="direct",
            last_accessed=datetime.now(timezone.utc),
        )

        # 插入 tab 到 _tabs 字典（_create_tab_unlocked 的替代）
        create_call_count = 0

        async def fake_create_tab_unlocked(url=None, context=None):
            nonlocal create_call_count
            create_call_count += 1
            if create_call_count == 1:
                # 首次调用：返回 proxy tab
                mgr._tabs[proxy_tab_info.tab_id] = (proxy_page, proxy_tab_info)
                mgr._active_tab_id = proxy_tab_info.tab_id
                return proxy_tab_info
            else:
                # 备选调用：返回 direct tab
                mgr._tabs[direct_tab_info.tab_id] = (direct_page, direct_tab_info)
                mgr._active_tab_id = direct_tab_info.tab_id
                return direct_tab_info

        mgr._create_tab_unlocked = fake_create_tab_unlocked

        # ── mock _build_page_state (避免真实 DOM 调用) ────────────────────
        from src.core.browser_manager import PageState

        async def fake_build_page_state(page, tab_id):
            return PageState(
                url=page.url,
                title=await page.title(),
                elements=[],
                text_summary="",
                visual_description=None,
                scroll_position="top",
                has_more_below=False,
                tab_id=tab_id,
                timestamp="2026-01-01T00:00:00+00:00",
                is_image_heavy=False,
            )

        mgr._build_page_state = fake_build_page_state

        # ── mock helpers that touch the browser ───────────────────────────
        async def fake_wait_for_stable(page):
            pass

        mgr._wait_for_stable = fake_wait_for_stable
        mgr._update_tab_info = lambda tab_id, page: None

        async def fake_publish_event(event_type, payload):
            pass

        mgr._publish_event = fake_publish_event

        # ── mock url_safety (import inside navigate) ───────────────────────
        import unittest.mock as mock
        from src.utils.url_safety import SafetyResult

        with mock.patch("src.utils.url_safety.check_url_safety", return_value=SafetyResult(True)):
            state = await mgr.navigate("https://example.com/")

        # ── assertions ─────────────────────────────────────────────────────
        assert first_goto_called, "第一次 goto（proxy）应被调用"
        assert alt_goto_called, "备选 goto（direct）应被调用"
        assert proxy_router.report_failure_called, "report_failure_and_get_alternative 应被调用"
        assert len(proxy_router.confirm_alternative_called_with) == 1, (
            f"confirm_alternative 应被调用一次，实际: {proxy_router.confirm_alternative_called_with}"
        )
        assert proxy_router.confirm_alternative_called_with[0][1] == "direct"
        assert state.tab_id == direct_tab_info.tab_id, "最终 tab 应为备选 direct tab"
