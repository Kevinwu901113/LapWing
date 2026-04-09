"""视觉理解模块测试。"""

from src.core.browser_manager import BrowserManager


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
