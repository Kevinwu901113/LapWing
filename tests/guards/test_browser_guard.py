"""Tests for BrowserGuard."""

import sys

import config.settings
from src.guards import browser_guard as _bg_module
from src.guards.browser_guard import BrowserGuard


def _ensure_settings_identity():
    """确保 browser_guard 模块引用的 _settings 与 config.settings 是同一对象。

    test_llm_router 的 autouse fixture 会清除 sys.modules 中的 settings 模块缓存，
    导致后续 import config.settings 产生新对象。这里让 guard 模块指向当前有效的模块。
    """
    # 如果 config.settings 被清出了 sys.modules，重新导入
    if "config.settings" not in sys.modules:
        import importlib
        import config.settings as _fresh  # noqa: F811
        sys.modules["config.settings"] = _fresh
    current = sys.modules["config.settings"]
    _bg_module._settings = current


class TestCheckUrlBlocksInternalNetwork:
    """内网地址应被拦截（BROWSER_BLOCK_INTERNAL_NETWORK=True 时）。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_block_internal_network(self, monkeypatch):
        monkeypatch.setattr(config.settings, "BROWSER_BLOCK_INTERNAL_NETWORK", True)
        monkeypatch.setattr(config.settings, "BROWSER_URL_BLACKLIST", [])

        internal_urls = [
            "http://127.0.0.1:8080/admin",
            "http://10.0.0.1/secret",
            "http://192.168.1.1/router",
            "http://localhost:3000",
        ]
        for url in internal_urls:
            result = self.guard.check_url(url)
            assert result.action == "block", f"应拦截内网地址: {url}"
            assert result.reason is not None


class TestCheckUrlAllowsPublic:
    """公网 URL 应放行。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_allow_public_url(self, monkeypatch):
        monkeypatch.setattr(config.settings, "BROWSER_BLOCK_INTERNAL_NETWORK", True)
        monkeypatch.setattr(config.settings, "BROWSER_URL_BLACKLIST", [])

        public_urls = [
            "https://github.com",
            "https://google.com/search?q=python",
        ]
        for url in public_urls:
            result = self.guard.check_url(url)
            assert result.action == "pass", f"应放行公网地址: {url}"


class TestCheckUrlBlocksDangerousProtocols:
    """javascript: 和 data: 协议应被拦截。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_block_javascript_protocol(self, monkeypatch):
        monkeypatch.setattr(config.settings, "BROWSER_URL_BLACKLIST", [])

        result = self.guard.check_url("javascript:alert(1)")
        assert result.action == "block"
        assert "javascript:" in (result.reason or "")

    def test_block_data_protocol(self, monkeypatch):
        monkeypatch.setattr(config.settings, "BROWSER_URL_BLACKLIST", [])

        result = self.guard.check_url("data:text/html,<script>alert(1)</script>")
        assert result.action == "block"
        assert "data:" in (result.reason or "")


class TestCheckUrlBlocksBlacklist:
    """黑名单域名应被拦截。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_block_blacklisted_domain(self, monkeypatch):
        monkeypatch.setattr(config.settings, "BROWSER_BLOCK_INTERNAL_NETWORK", False)
        monkeypatch.setattr(config.settings, "BROWSER_URL_BLACKLIST", ["evil.com"])

        result = self.guard.check_url("https://evil.com/phishing")
        assert result.action == "block"
        assert "evil.com" in (result.reason or "")

        # 子域名也应匹配
        result = self.guard.check_url("https://sub.evil.com/page")
        assert result.action == "block"


class TestCheckActionSensitiveWords:
    """包含敏感词的元素操作应要求用户确认。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_sensitive_button_consent(self, monkeypatch):
        monkeypatch.setattr(
            config.settings,
            "BROWSER_SENSITIVE_ACTION_WORDS",
            ["delete", "remove", "pay", "purchase", "buy", "submit order",
             "删除", "移除", "支付", "购买", "确认订单", "提交订单"],
        )

        # 中文敏感词
        result = self.guard.check_action("click", "删除", "https://example.com")
        assert result.action == "require_consent"

        # 英文敏感词
        result = self.guard.check_action("click", "Pay Now", "https://example.com")
        assert result.action == "require_consent"

    def test_normal_button_pass(self, monkeypatch):
        monkeypatch.setattr(
            config.settings,
            "BROWSER_SENSITIVE_ACTION_WORDS",
            ["delete", "remove", "pay", "purchase", "buy", "submit order",
             "删除", "移除", "支付", "购买", "确认订单", "提交订单"],
        )

        result = self.guard.check_action("click", "搜索", "https://example.com")
        assert result.action == "pass"

        result = self.guard.check_action("click", "Submit", "https://example.com")
        assert result.action == "pass"


class TestCheckActionCheckoutPage:
    """结账/支付页面上的操作应要求用户确认。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_checkout_page_consent(self, monkeypatch):
        monkeypatch.setattr(
            config.settings,
            "BROWSER_SENSITIVE_ACTION_WORDS",
            [],  # 清空敏感词，确保是 URL 触发
        )

        checkout_urls = [
            "https://shop.com/checkout",
            "https://store.com/payment?id=123",
            "https://example.com/pay.html",
        ]
        for url in checkout_urls:
            result = self.guard.check_action("click", "Next", url)
            assert result.action == "require_consent", (
                f"应要求确认: {url}"
            )


class TestCheckJsBlocksDangerous:
    """危险 JavaScript 表达式应被拦截。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_js_block_eval(self):
        result = self.guard.check_js('eval("alert(1)")')
        assert result.action == "block"

    def test_js_block_new_function(self):
        result = self.guard.check_js('new Function("return 1")')
        assert result.action == "block"

    def test_js_block_document_cookie_assign(self):
        result = self.guard.check_js('document.cookie="x=y"')
        assert result.action == "block"

    def test_js_block_window_location_assign(self):
        result = self.guard.check_js('window.location="https://evil.com"')
        assert result.action == "block"

    def test_js_block_location_href_assign(self):
        result = self.guard.check_js('location.href="https://evil.com"')
        assert result.action == "block"

    def test_js_block_document_write(self):
        result = self.guard.check_js('document.write("<h1>pwned</h1>")')
        assert result.action == "block"


class TestCheckJsPassesReadOnly:
    """只读 DOM 查询和属性读取应放行。"""

    def setup_method(self):
        _ensure_settings_identity()
        self.guard = BrowserGuard()

    def test_js_pass_read_only(self):
        safe_expressions = [
            "document.title",
            'document.querySelectorAll("a")',
            'document.getElementById("main").innerText',
            "window.innerHeight",
            "document.body.scrollHeight",
        ]
        for expr in safe_expressions:
            result = self.guard.check_js(expr)
            assert result.action == "pass", f"应放行只读表达式: {expr}"
