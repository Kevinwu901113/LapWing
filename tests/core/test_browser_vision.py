"""视觉理解模块测试。"""

import pytest
from unittest.mock import AsyncMock, patch

from src.core.browser_manager import BrowserManager
from src.core.brain import LapwingBrain


class TestShouldUseVision:
    """_should_use_vision() 图片密集度判断测试。"""

    def setup_method(self):
        self.mgr = BrowserManager()

    def test_image_heavy_low_alt(self):
        """图片多且 alt 覆盖率低 → 需要视觉。"""
        metrics = {
            "img_count": 10,
            "img_with_alt_count": 1,
            "text_node_char_count": 2000,
            "canvas_count": 0,
        }
        assert self.mgr._should_use_vision(metrics) is True

    def test_text_heavy_no_vision(self):
        """文字多、图片有 alt → 不需要视觉。"""
        metrics = {
            "img_count": 3,
            "img_with_alt_count": 3,
            "text_node_char_count": 5000,
            "canvas_count": 0,
        }
        assert self.mgr._should_use_vision(metrics) is False

    def test_canvas_needs_vision(self):
        """有 canvas 元素 → 需要视觉。"""
        metrics = {
            "img_count": 0,
            "img_with_alt_count": 0,
            "text_node_char_count": 1000,
            "canvas_count": 1,
        }
        assert self.mgr._should_use_vision(metrics) is True

    def test_few_images_no_vision(self):
        """图片少 → 不需要视觉。"""
        metrics = {
            "img_count": 2,
            "img_with_alt_count": 0,
            "text_node_char_count": 3000,
            "canvas_count": 0,
        }
        assert self.mgr._should_use_vision(metrics) is False

    def test_low_text_some_images(self):
        """文字少图片多（>= 3）→ 需要视觉。"""
        metrics = {
            "img_count": 4,
            "img_with_alt_count": 4,
            "text_node_char_count": 200,
            "canvas_count": 0,
        }
        assert self.mgr._should_use_vision(metrics) is True

    def test_no_images_no_vision(self):
        """无图片 → 不需要视觉。"""
        metrics = {
            "img_count": 0,
            "img_with_alt_count": 0,
            "text_node_char_count": 5000,
            "canvas_count": 0,
        }
        assert self.mgr._should_use_vision(metrics) is False


class TestInjectImagesIntoLastUserMessage:
    """Brain._inject_images_into_last_user_message 多模态注入测试。"""

    def test_base64_image_injection(self):
        """base64 图片注入到最后一条 user 消息。"""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "请描述这张图片"},
        ]
        images = [{"base64": "abc123", "media_type": "image/png"}]
        LapwingBrain._inject_images_into_last_user_message(messages, images)

        content = messages[-1]["content"]
        assert isinstance(content, list)
        assert len(content) == 2  # text + image
        assert content[0] == {"type": "text", "text": "请描述这张图片"}
        assert content[1]["type"] == "image"
        assert content[1]["source"]["type"] == "base64"
        assert content[1]["source"]["data"] == "abc123"

    def test_url_image_injection(self):
        """URL 图片注入。"""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "看这张"},
        ]
        images = [{"url": "https://example.com/img.jpg"}]
        LapwingBrain._inject_images_into_last_user_message(messages, images)

        content = messages[-1]["content"]
        assert isinstance(content, list)
        assert content[1]["type"] == "image"
        assert content[1]["source"]["type"] == "url"
        assert content[1]["source"]["url"] == "https://example.com/img.jpg"

    def test_multiple_images(self):
        """多张图片注入。"""
        messages = [{"role": "user", "content": "对比这两张"}]
        images = [
            {"base64": "img1", "media_type": "image/jpeg"},
            {"base64": "img2", "media_type": "image/png"},
        ]
        LapwingBrain._inject_images_into_last_user_message(messages, images)

        content = messages[-1]["content"]
        assert len(content) == 3  # text + 2 images
        assert content[1]["source"]["data"] == "img1"
        assert content[2]["source"]["data"] == "img2"

    def test_empty_text_with_image(self):
        """空文本 + 图片 → 只有图片 block。"""
        messages = [{"role": "user", "content": ""}]
        images = [{"base64": "abc", "media_type": "image/jpeg"}]
        LapwingBrain._inject_images_into_last_user_message(messages, images)

        content = messages[-1]["content"]
        assert len(content) == 1  # only image, no text block
        assert content[0]["type"] == "image"

    def test_no_user_message_noop(self):
        """没有 user 消息时不崩溃。"""
        messages = [{"role": "system", "content": "sys"}]
        images = [{"base64": "abc", "media_type": "image/jpeg"}]
        LapwingBrain._inject_images_into_last_user_message(messages, images)
        # system message 不受影响
        assert messages[0]["content"] == "sys"

    def test_default_media_type(self):
        """未指定 media_type 时默认 image/jpeg。"""
        messages = [{"role": "user", "content": "看"}]
        images = [{"base64": "data"}]
        LapwingBrain._inject_images_into_last_user_message(messages, images)

        content = messages[-1]["content"]
        assert content[1]["source"]["media_type"] == "image/jpeg"


class TestVisualDescribeVLMFallback:
    """_visual_describe 的 VLM 优先 + Router 回退逻辑。"""

    def setup_method(self):
        self.mgr = BrowserManager()

    @pytest.mark.asyncio
    async def test_vlm_preferred_over_router(self):
        """VLM 客户端可用时优先使用，不调用 router。"""
        mock_vlm = AsyncMock()
        mock_vlm.understand_image = AsyncMock(return_value="VLM 描述结果")
        mock_router = AsyncMock()
        mock_router.complete = AsyncMock(return_value="Router 描述结果")

        self.mgr._vlm_client = mock_vlm
        self.mgr._router = mock_router

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 100)

        result = await self.mgr._visual_describe(mock_page, "test_tab")
        assert result == "VLM 描述结果"
        mock_vlm.understand_image.assert_awaited_once()
        mock_router.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vlm_failure_falls_back_to_router(self):
        """VLM 失败时回退到 router。"""
        mock_vlm = AsyncMock()
        mock_vlm.understand_image = AsyncMock(side_effect=Exception("VLM down"))
        mock_router = AsyncMock()
        mock_router.complete = AsyncMock(return_value="Router 回退结果")

        self.mgr._vlm_client = mock_vlm
        self.mgr._router = mock_router

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 100)

        result = await self.mgr._visual_describe(mock_page, "test_tab_2")
        assert result == "Router 回退结果"
        mock_vlm.understand_image.assert_awaited_once()
        mock_router.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_prevents_repeat_calls(self):
        """缓存有效期内不重复调用。"""
        mock_vlm = AsyncMock()
        mock_vlm.understand_image = AsyncMock(return_value="cached")
        self.mgr._vlm_client = mock_vlm

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 100)

        # 第一次调用
        r1 = await self.mgr._visual_describe(mock_page, "cache_tab")
        assert r1 == "cached"
        # 第二次应命中缓存
        r2 = await self.mgr._visual_describe(mock_page, "cache_tab")
        assert r2 == "cached"
        # VLM 只调用了一次
        assert mock_vlm.understand_image.await_count == 1
