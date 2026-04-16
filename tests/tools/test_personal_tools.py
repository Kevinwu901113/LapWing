"""personal_tools 个人工具集单元测试 — Phase 4。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.types import ToolExecutionRequest, ToolExecutionContext, ToolExecutionResult


def _make_ctx(**overrides) -> ToolExecutionContext:
    """构造测试用 ToolExecutionContext。"""
    defaults = dict(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services={},
        adapter="qq",
        user_id="user1",
        auth_level=2,
        chat_id="test_chat",
    )
    defaults.update(overrides)
    return ToolExecutionContext(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_time
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetTime:

    async def test_returns_success_with_time_fields(self):
        """get_time 返回 success=True，payload 含 time/weekday/timezone。"""
        from src.tools.personal_tools import _get_time

        req = ToolExecutionRequest(name="get_time", arguments={})
        ctx = _make_ctx()
        result = await _get_time(req, ctx)

        assert result.success is True
        assert "time" in result.payload
        assert "weekday" in result.payload
        assert "timezone" in result.payload

    async def test_timezone_is_taipei(self):
        """timezone 固定为 Asia/Taipei。"""
        from src.tools.personal_tools import _get_time

        req = ToolExecutionRequest(name="get_time", arguments={})
        ctx = _make_ctx()
        result = await _get_time(req, ctx)

        assert result.payload["timezone"] == "Asia/Taipei"

    async def test_weekday_is_chinese(self):
        """weekday 为中文格式（周一~周日）。"""
        from src.tools.personal_tools import _get_time

        req = ToolExecutionRequest(name="get_time", arguments={})
        ctx = _make_ctx()
        result = await _get_time(req, ctx)

        assert result.payload["weekday"] in ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


# ─────────────────────────────────────────────────────────────────────────────
# 2. send_message
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSendMessage:

    async def test_kevin_qq_success(self):
        """target=kevin_qq 成功发送 QQ 私信。"""
        from src.tools.personal_tools import _send_message

        mock_qq_adapter = MagicMock()
        mock_qq_adapter.send_private_message = AsyncMock()

        mock_cm = MagicMock()
        mock_cm.get_adapter = MagicMock(return_value=mock_qq_adapter)

        ctx = _make_ctx(services={"channel_manager": mock_cm, "owner_qq_id": "12345"})
        req = ToolExecutionRequest(name="send_message", arguments={
            "target": "kevin_qq",
            "content": "你好",
        })
        result = await _send_message(req, ctx)

        assert result.success is True
        assert result.payload["sent"] is True
        assert result.payload["target"] == "kevin_qq"
        mock_qq_adapter.send_private_message.assert_awaited_once_with("12345", "你好")

    async def test_kevin_desktop_success(self):
        """target=kevin_desktop 成功发送桌面消息。"""
        import sys
        from src.tools.personal_tools import _send_message

        mock_desktop_adapter = MagicMock()
        mock_desktop_adapter.is_connected = MagicMock(return_value=True)
        mock_desktop_adapter.send_message = AsyncMock()

        mock_cm = MagicMock()
        mock_cm.get_adapter = MagicMock(return_value=mock_desktop_adapter)

        # 确保 DesktopAdapter 可被导入（模块可能不存在）
        mock_desktop_module = MagicMock()
        with patch.dict(sys.modules, {"src.adapters.desktop": mock_desktop_module}):
            ctx = _make_ctx(services={"channel_manager": mock_cm})
            req = ToolExecutionRequest(name="send_message", arguments={
                "target": "kevin_desktop",
                "content": "桌面消息",
            })
            result = await _send_message(req, ctx)

        assert result.success is True
        assert result.payload["sent"] is True
        assert result.payload["target"] == "kevin_desktop"
        mock_desktop_adapter.send_message.assert_awaited_once_with("桌面消息")

    async def test_desktop_not_connected(self):
        """Desktop 未连接时返回有意义的错误提示。"""
        import sys
        from src.tools.personal_tools import _send_message

        mock_desktop_adapter = MagicMock()
        mock_desktop_adapter.is_connected = MagicMock(return_value=False)

        mock_cm = MagicMock()
        mock_cm.get_adapter = MagicMock(return_value=mock_desktop_adapter)

        mock_desktop_module = MagicMock()
        with patch.dict(sys.modules, {"src.adapters.desktop": mock_desktop_module}):
            ctx = _make_ctx(services={"channel_manager": mock_cm})
            req = ToolExecutionRequest(name="send_message", arguments={
                "target": "kevin_desktop",
                "content": "测试",
            })
            result = await _send_message(req, ctx)

        assert result.success is False
        assert "Desktop" in result.payload["error"] or "未连接" in result.payload["error"]

    async def test_unknown_target(self):
        """未知 target 返回错误，列出可用选项。"""
        from src.tools.personal_tools import _send_message

        mock_cm = MagicMock()
        ctx = _make_ctx(services={"channel_manager": mock_cm})
        req = ToolExecutionRequest(name="send_message", arguments={
            "target": "unknown_target",
            "content": "测试",
        })
        result = await _send_message(req, ctx)

        assert result.success is False
        assert "unknown_target" in result.payload["error"]
        # 应列出可用目标
        assert "kevin_qq" in result.payload["error"]
        assert "kevin_desktop" in result.payload["error"]

    async def test_missing_content(self):
        """缺少 content 参数返回错误。"""
        from src.tools.personal_tools import _send_message

        mock_cm = MagicMock()
        ctx = _make_ctx(services={"channel_manager": mock_cm})
        req = ToolExecutionRequest(name="send_message", arguments={
            "target": "kevin_qq",
            "content": "",
        })
        result = await _send_message(req, ctx)

        assert result.success is False
        assert "content" in result.payload["error"]

    async def test_missing_target(self):
        """缺少 target 参数返回错误。"""
        from src.tools.personal_tools import _send_message

        ctx = _make_ctx(services={"channel_manager": MagicMock()})
        req = ToolExecutionRequest(name="send_message", arguments={
            "target": "",
            "content": "hello",
        })
        result = await _send_message(req, ctx)

        assert result.success is False
        assert "target" in result.payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. send_image
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSendImage:

    async def test_success_with_image_url(self):
        """提供 image_url 成功发送图片。"""
        from src.tools.personal_tools import _send_image

        mock_cm = MagicMock()
        mock_cm.send_image_to_owner = AsyncMock()

        ctx = _make_ctx(services={"channel_manager": mock_cm})
        req = ToolExecutionRequest(name="send_image", arguments={
            "image_url": "https://example.com/img.png",
            "caption": "测试图片",
        })
        result = await _send_image(req, ctx)

        assert result.success is True
        assert result.payload["sent"] is True
        mock_cm.send_image_to_owner.assert_awaited_once_with(
            url="https://example.com/img.png",
            path=None,
            caption="测试图片",
        )

    async def test_failure_no_url_or_path(self):
        """缺少 image_url 和 image_path 返回错误。"""
        from src.tools.personal_tools import _send_image

        ctx = _make_ctx(services={"channel_manager": MagicMock()})
        req = ToolExecutionRequest(name="send_image", arguments={})
        result = await _send_image(req, ctx)

        assert result.success is False
        assert "image_url" in result.payload["error"] or "image_path" in result.payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. view_image
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestViewImage:

    async def test_no_vlm_available(self):
        """VLM 不可用时返回 '视觉理解不可用'。"""
        from src.tools.personal_tools import _view_image

        ctx = _make_ctx(services={})  # 无 vlm
        req = ToolExecutionRequest(name="view_image", arguments={"image": "base64data..."})
        result = await _view_image(req, ctx)

        assert result.success is False
        assert "视觉理解不可用" in result.payload["error"]

    async def test_vlm_success(self):
        """VLM 正常返回描述。"""
        from src.tools.personal_tools import _view_image

        mock_vlm = MagicMock()
        mock_vlm.describe = AsyncMock(return_value="一张猫的照片")

        ctx = _make_ctx(services={"vlm": mock_vlm})
        req = ToolExecutionRequest(name="view_image", arguments={"image": "/tmp/cat.jpg"})
        result = await _view_image(req, ctx)

        assert result.success is True
        assert result.payload["description"] == "一张猫的照片"
        mock_vlm.describe.assert_awaited_once_with("/tmp/cat.jpg", prompt="描述这张图片的内容。")

    async def test_vlm_description_truncated(self):
        """超长描述被截断到 1500 字。"""
        from src.tools.personal_tools import _view_image, _VIEW_IMAGE_MAX_CHARS

        long_text = "x" * 3000
        mock_vlm = MagicMock()
        mock_vlm.describe = AsyncMock(return_value=long_text)

        ctx = _make_ctx(services={"vlm": mock_vlm})
        req = ToolExecutionRequest(name="view_image", arguments={"image": "data"})
        result = await _view_image(req, ctx)

        assert result.success is True
        assert len(result.payload["description"]) <= _VIEW_IMAGE_MAX_CHARS + 20  # 截断标记
        assert "已截断" in result.payload["description"]

    async def test_missing_image_param(self):
        """缺少 image 参数返回错误。"""
        from src.tools.personal_tools import _view_image

        ctx = _make_ctx(services={"vlm": MagicMock()})
        req = ToolExecutionRequest(name="view_image", arguments={})
        result = await _view_image(req, ctx)

        assert result.success is False
        assert "image" in result.payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. web_search
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestWebSearch:

    async def test_success_returns_results(self):
        """正常搜索返回结果列表，受 max 5 限制，snippet 截断。"""
        from src.tools.personal_tools import _web_search, _SEARCH_SNIPPET_MAX

        mock_results = [
            {"title": f"Title {i}", "url": f"https://example.com/{i}", "snippet": "x" * 300}
            for i in range(7)  # 7 条，应只取 5
        ]

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_search", arguments={"query": "python"})

        with patch("src.tools.web_search.search", new=AsyncMock(return_value=mock_results)):
            result = await _web_search(req, ctx)

        assert result.success is True
        assert len(result.payload["results"]) == 5
        # snippet 应被截断
        for item in result.payload["results"]:
            assert len(item["snippet"]) <= _SEARCH_SNIPPET_MAX + 5  # 加上省略号

    async def test_empty_query_returns_error(self):
        """空 query 返回错误。"""
        from src.tools.personal_tools import _web_search

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_search", arguments={"query": ""})
        result = await _web_search(req, ctx)

        assert result.success is False
        assert "query" in result.payload["error"]

    async def test_search_exception_returns_error(self):
        """搜索抛异常时返回有意义的错误。"""
        from src.tools.personal_tools import _web_search

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_search", arguments={"query": "test"})

        with patch("src.tools.web_search.search", new=AsyncMock(side_effect=RuntimeError("API error"))):
            result = await _web_search(req, ctx)

        assert result.success is False
        assert "API error" in result.payload["error"]

    async def test_empty_results(self):
        """搜索结果为空返回 '没有找到相关结果'。"""
        from src.tools.personal_tools import _web_search

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_search", arguments={"query": "nonexistent"})

        with patch("src.tools.web_search.search", new=AsyncMock(return_value=[])):
            result = await _web_search(req, ctx)

        assert result.success is False
        assert "没有找到相关结果" in result.payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. web_fetch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestWebFetch:

    async def test_success_returns_content(self):
        """正常抓取返回标题和文本。"""
        from src.tools.personal_tools import _web_fetch
        from src.tools.web_fetcher import FetchResult

        mock_result = FetchResult(
            url="https://example.com",
            title="Example",
            text="Hello World",
            success=True,
            error="",
        )

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_fetch", arguments={"url": "https://example.com"})

        with patch("src.tools.web_fetcher.fetch", new=AsyncMock(return_value=mock_result)):
            result = await _web_fetch(req, ctx)

        assert result.success is True
        assert result.payload["title"] == "Example"
        assert result.payload["text"] == "Hello World"
        assert "truncation_note" not in result.payload

    async def test_empty_url_returns_error(self):
        """空 url 返回错误。"""
        from src.tools.personal_tools import _web_fetch

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_fetch", arguments={"url": ""})
        result = await _web_fetch(req, ctx)

        assert result.success is False
        assert "url" in result.payload["error"]

    async def test_long_content_truncated(self):
        """超过 3000 字的内容被截断并附上 truncation_note。"""
        from src.tools.personal_tools import _web_fetch, _WEB_FETCH_MAX_CHARS
        from src.tools.web_fetcher import FetchResult

        long_text = "A" * 5000
        mock_result = FetchResult(
            url="https://example.com/long",
            title="Long Page",
            text=long_text,
            success=True,
            error="",
        )

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_fetch", arguments={"url": "https://example.com/long"})

        with patch("src.tools.web_fetcher.fetch", new=AsyncMock(return_value=mock_result)):
            result = await _web_fetch(req, ctx)

        assert result.success is True
        assert len(result.payload["text"]) == _WEB_FETCH_MAX_CHARS
        assert "truncation_note" in result.payload

    async def test_fetch_failure_propagated(self):
        """抓取失败时 success=False。"""
        from src.tools.personal_tools import _web_fetch
        from src.tools.web_fetcher import FetchResult

        mock_result = FetchResult(
            url="https://example.com/404",
            title="",
            text="",
            success=False,
            error="404 Not Found",
        )

        ctx = _make_ctx()
        req = ToolExecutionRequest(name="web_fetch", arguments={"url": "https://example.com/404"})

        with patch("src.tools.web_fetcher.fetch", new=AsyncMock(return_value=mock_result)):
            result = await _web_fetch(req, ctx)

        assert result.success is False
        assert "404" in result.payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. browse
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestBrowse:

    async def test_file_url_blocked(self):
        """file:// URL 被安全检查拦截。"""
        from src.tools.personal_tools import _browse

        ctx = _make_ctx(services={"browser_manager": MagicMock()})
        req = ToolExecutionRequest(name="browse", arguments={"url": "file:///etc/passwd"})
        result = await _browse(req, ctx)

        assert result.success is False
        assert "不支持" in result.payload["error"] or "协议" in result.payload["error"]

    async def test_localhost_blocked(self):
        """localhost URL 被安全检查拦截。"""
        from src.tools.personal_tools import _browse

        ctx = _make_ctx(services={"browser_manager": MagicMock()})
        req = ToolExecutionRequest(name="browse", arguments={"url": "http://localhost:8080/admin"})
        result = await _browse(req, ctx)

        assert result.success is False
        assert "内网" in result.payload["error"] or "禁止" in result.payload["error"]

    async def test_internal_ip_blocked(self):
        """内网 IP 被安全检查拦截。"""
        from src.tools.personal_tools import _browse

        ctx = _make_ctx(services={"browser_manager": MagicMock()})
        req = ToolExecutionRequest(name="browse", arguments={"url": "http://192.168.1.1/config"})
        result = await _browse(req, ctx)

        assert result.success is False
        assert "内网" in result.payload["error"] or "禁止" in result.payload["error"]

    async def test_no_browser_manager(self):
        """browser_manager 不可用返回 '浏览器不可用'。"""
        from src.tools.personal_tools import _browse

        ctx = _make_ctx(services={})  # 无 browser_manager
        req = ToolExecutionRequest(name="browse", arguments={"url": "https://example.com"})
        result = await _browse(req, ctx)

        assert result.success is False
        assert "浏览器不可用" in result.payload["error"]

    async def test_success_with_vlm(self):
        """有 VLM 时走 screenshot+vlm 路径。"""
        from src.tools.personal_tools import _browse

        mock_bm = MagicMock()
        mock_bm.open_tab = AsyncMock(return_value="tab-1")
        mock_bm.take_screenshot = AsyncMock(return_value=b"screenshot_data")
        mock_bm.close_tab = AsyncMock()

        mock_vlm = MagicMock()
        mock_vlm.describe = AsyncMock(return_value="一个示例网页，显示了标题和内容。")

        ctx = _make_ctx(services={"browser_manager": mock_bm, "vlm": mock_vlm})
        req = ToolExecutionRequest(name="browse", arguments={"url": "https://example.com"})
        result = await _browse(req, ctx)

        assert result.success is True
        assert result.payload["method"] == "screenshot+vlm"
        assert "description" in result.payload
        mock_bm.open_tab.assert_awaited_once_with("https://example.com")
        mock_bm.close_tab.assert_awaited_once_with("tab-1")

    async def test_success_without_vlm_fallback(self):
        """无 VLM 时回退到文本提取。"""
        from src.tools.personal_tools import _browse

        mock_page_state = MagicMock()
        mock_page_state.to_llm_text = MagicMock(return_value="页面文本内容")

        mock_bm = MagicMock()
        mock_bm.open_tab = AsyncMock(return_value="tab-2")
        mock_bm.get_page_state = AsyncMock(return_value=mock_page_state)
        mock_bm.close_tab = AsyncMock()

        ctx = _make_ctx(services={"browser_manager": mock_bm})  # 无 vlm
        req = ToolExecutionRequest(name="browse", arguments={"url": "https://example.com"})
        result = await _browse(req, ctx)

        assert result.success is True
        assert result.payload["method"] == "text_fallback"
        assert result.payload["text"] == "页面文本内容"

    async def test_tab_closed_on_error(self):
        """即使浏览过程出错，标签页也会被关闭。"""
        from src.tools.personal_tools import _browse

        mock_bm = MagicMock()
        mock_bm.open_tab = AsyncMock(return_value="tab-err")
        mock_bm.take_screenshot = AsyncMock(side_effect=RuntimeError("截图失败"))
        mock_bm.close_tab = AsyncMock()

        mock_vlm = MagicMock()

        ctx = _make_ctx(services={"browser_manager": mock_bm, "vlm": mock_vlm})
        req = ToolExecutionRequest(name="browse", arguments={"url": "https://example.com"})
        result = await _browse(req, ctx)

        assert result.success is False
        # 确保 close_tab 被调用（finally 块）
        mock_bm.close_tab.assert_awaited_once_with("tab-err")


# ─────────────────────────────────────────────────────────────────────────────
# 8. delegate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
# 9. register_personal_tools
# ─────────────────────────────────────────────────────────────────────────────


class TestRegisterPersonalTools:

    def test_registers_7_tools(self):
        """register_personal_tools 注册 7 个工具。"""
        from src.tools.personal_tools import register_personal_tools

        mock_registry = MagicMock()
        register_personal_tools(mock_registry, services={})

        assert mock_registry.register.call_count == 7

    def test_registered_tool_names(self):
        """验证注册的工具名称列表。"""
        from src.tools.personal_tools import register_personal_tools

        registered = []
        mock_registry = MagicMock()
        mock_registry.register = MagicMock(side_effect=lambda spec: registered.append(spec.name))

        register_personal_tools(mock_registry, services={})

        expected_names = {
            "get_time", "send_message", "send_image", "view_image",
            "web_search", "web_fetch", "browse",
        }
        assert set(registered) == expected_names
