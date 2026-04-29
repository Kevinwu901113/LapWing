"""BrowserGuard — pre-execution safety for browser_* tools.

Locks in the contract from commit 8:
- URL scheme allowlist (http/https only); file://, javascript:, data:,
  blob:, vbscript: blocked.
- Localhost + private IPs blocked when block_internal_network is true.
- url_blacklist / url_whitelist applied.
- Per-session action budget — bounded total Playwright operations.
- Sensitive action words on click targets surface as require_consent.
- browser_login forces consent.
- Dangerous JS (cookie/localStorage/eval/window.location/fetch external)
  is blocked.
- Downloads blocked when block_downloads is true.

Plus the harness contract: when no BrowserGuard is mounted, TaskRuntime
must refuse every browser_* tool call.
"""

from __future__ import annotations

import pytest

from src.core.browser_guard import (
    BrowserGuard,
    GuardOutcome,
    _is_internal_host,
)


def _guard(**overrides) -> BrowserGuard:
    cfg = {
        "block_internal_network": True,
        "url_blacklist": (),
        "url_whitelist": (),
        "sensitive_words": ("delete", "submit order", "支付", "确认订单"),
        "max_actions_per_session": 5,
        "block_downloads": True,
    }
    cfg.update(overrides)
    return BrowserGuard(**cfg)


class TestUrlSchemes:
    def test_https_allowed(self):
        assert _guard().check_url("https://example.com/").action == "allow"

    def test_http_allowed(self):
        assert _guard().check_url("http://example.com/").action == "allow"

    def test_file_scheme_blocked(self):
        out = _guard().check_url("file:///etc/passwd")
        assert out.action == "block"
        assert "file" in out.reason

    def test_javascript_scheme_blocked(self):
        out = _guard().check_url("javascript:alert(1)")
        assert out.action == "block"
        assert "javascript" in out.reason

    def test_data_url_blocked(self):
        out = _guard().check_url("data:text/html,<script>alert(1)</script>")
        assert out.action == "block"

    def test_blob_url_blocked(self):
        out = _guard().check_url("blob:https://example.com/abc")
        assert out.action == "block"

    def test_empty_url_blocked(self):
        assert _guard().check_url("").action == "block"
        assert _guard().check_url("   ").action == "block"

    def test_url_without_host_blocked(self):
        # Anchor-only or scheme-only URLs.
        assert _guard().check_url("https://").action == "block"


class TestInternalNetwork:
    def test_localhost_blocked_by_default(self):
        out = _guard().check_url("http://localhost/admin")
        assert out.action == "block"
        assert "内网" in out.reason or "loopback" in out.reason or "私有" in out.reason

    def test_loopback_ipv4_blocked(self):
        assert _guard().check_url("http://127.0.0.1/").action == "block"
        assert _guard().check_url("http://127.7.0.1/").action == "block"

    def test_loopback_ipv6_blocked(self):
        assert _guard().check_url("http://[::1]/").action == "block"

    def test_private_10_range_blocked(self):
        assert _guard().check_url("http://10.0.0.1/").action == "block"

    def test_private_192_168_range_blocked(self):
        assert _guard().check_url("http://192.168.1.1/").action == "block"

    def test_link_local_blocked(self):
        assert _guard().check_url("http://169.254.169.254/").action == "block"

    def test_dot_local_mdns_blocked(self):
        assert _guard().check_url("http://printer.local/").action == "block"

    def test_dot_internal_blocked(self):
        assert _guard().check_url("http://api.internal/").action == "block"

    def test_internal_allowed_when_configured(self):
        guard = _guard(block_internal_network=False)
        assert guard.check_url("http://localhost/dashboard").action == "allow"

    def test_helper_is_internal_host(self):
        assert _is_internal_host("localhost") is True
        assert _is_internal_host("127.0.0.1") is True
        assert _is_internal_host("foo.local") is True
        assert _is_internal_host("8.8.8.8") is False
        assert _is_internal_host("example.com") is False


class TestUrlAllowDenyLists:
    def test_blacklist_blocks(self):
        guard = _guard(url_blacklist=("evil.com",))
        out = guard.check_url("https://evil.com/page")
        assert out.action == "block"
        assert "evil.com" in out.reason

    def test_blacklist_blocks_subdomain(self):
        guard = _guard(url_blacklist=("evil.com",))
        out = guard.check_url("https://login.evil.com/page")
        assert out.action == "block"

    def test_whitelist_only_allows_listed(self):
        guard = _guard(url_whitelist=("trusted.com",))
        assert guard.check_url("https://trusted.com/").action == "allow"
        out = guard.check_url("https://random.com/")
        assert out.action == "block"
        assert "whitelist" in out.reason

    def test_whitelist_allows_subdomain(self):
        guard = _guard(url_whitelist=("trusted.com",))
        assert guard.check_url("https://api.trusted.com/").action == "allow"


class TestActionBudget:
    def test_state_changing_actions_increment_budget(self):
        guard = _guard(max_actions_per_session=3, block_internal_network=False)
        for _ in range(3):
            assert guard.check_action("click", "OK").action == "allow"
        # Fourth click hits the cap
        out = guard.check_action("click", "OK")
        assert out.action == "block"
        assert "action_budget_exceeded" in out.reason

    def test_reset_budget_clears_count(self):
        guard = _guard(max_actions_per_session=2, block_internal_network=False)
        guard.check_action("click", "OK")
        guard.check_action("click", "OK")
        assert guard.actions_used() == 2
        guard.reset_budget()
        assert guard.actions_used() == 0
        assert guard.check_action("click", "OK").action == "allow"


class TestSensitiveActionWords:
    def test_destructive_verb_requires_consent(self):
        out = _guard().check_action("click", "Delete account")
        assert out.action == "require_consent"
        assert "Delete" in out.reason or "delete" in out.reason

    def test_chinese_destructive_verb_requires_consent(self):
        out = _guard().check_action("click", "支付订单")
        assert out.action == "require_consent"

    def test_submit_order_button_requires_consent(self):
        out = _guard().check_action("click", "Submit order now")
        assert out.action == "require_consent"

    def test_safe_button_passes(self):
        out = _guard().check_action("click", "Read more")
        assert out.action == "allow"


class TestLoginAndCredentialActions:
    def test_login_action_requires_consent(self):
        out = _guard().check_action("login")
        assert out.action == "require_consent"
        assert "OWNER" in out.reason or "凭据" in out.reason


class TestUrlReChecksOnAction:
    def test_url_blocked_on_action_after_navigation(self):
        """If the page has navigated to a now-banned URL, even a click
        that doesn't itself touch URLs is blocked."""
        guard = _guard(url_blacklist=("evil.com",))
        out = guard.check_action("click", "Read", url="https://evil.com/p")
        assert out.action == "block"


class TestJavaScriptGate:
    def test_blocks_eval(self):
        out = _guard().check_js("eval('1+1')")
        assert out.action == "block"
        assert "eval" in out.reason.lower()

    def test_blocks_document_cookie(self):
        out = _guard().check_js("return document.cookie")
        assert out.action == "block"

    def test_blocks_localstorage(self):
        out = _guard().check_js("return localStorage.getItem('x')")
        assert out.action == "block"

    def test_blocks_window_location_assign(self):
        out = _guard().check_js("window.location = 'https://x.com'")
        assert out.action == "block"

    def test_blocks_xmlhttprequest(self):
        out = _guard().check_js("new XMLHttpRequest()")
        assert out.action == "block"

    def test_allows_dom_query(self):
        # Reading text content from the DOM is fine.
        out = _guard().check_js("document.querySelector('h1').textContent")
        assert out.action == "allow"

    def test_empty_expression_blocked(self):
        assert _guard().check_js("").action == "block"


class TestDownloadGate:
    def test_downloads_blocked_when_configured(self):
        out = _guard(block_downloads=True).check_download(
            url="https://example.com/x.zip", filename="x.zip"
        )
        assert out.action == "block"
        assert "downloads" in out.reason.lower()

    def test_downloads_allowed_when_disabled_and_url_passes(self):
        out = _guard(block_downloads=False).check_download(
            url="https://example.com/x.zip"
        )
        assert out.action == "allow"

    def test_downloads_allowed_disabled_but_url_blocked(self):
        out = _guard(block_downloads=False).check_download(
            url="file:///etc/passwd"
        )
        assert out.action == "block"


class TestBuildFromSettings:
    def test_picks_up_browser_config_fields(self):
        from src.config.settings import BrowserConfig

        cfg = BrowserConfig(
            url_blacklist=["bad.com"],
            url_whitelist=["good.com"],
            block_internal_network=False,
        )
        guard = BrowserGuard.from_settings(cfg)
        assert guard.url_blacklist == ("bad.com",)
        assert guard.url_whitelist == ("good.com",)
        assert guard.block_internal_network is False
        # Sensitive words come from the BrowserConfig defaults
        assert any("delete" in w for w in guard.sensitive_words)


class TestTaskRuntimeBlocksWhenGuardMissing:
    """When no BrowserGuard is mounted, TaskRuntime must refuse every
    browser_* tool call — the guard is mandatory once the browser
    subsystem is enabled."""

    @pytest.mark.asyncio
    async def test_browser_open_blocked_without_guard(self):
        from unittest.mock import MagicMock
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _noop_executor(req, ctx):
            return ToolExecutionResult(success=True, payload={"ran": True})

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="browser_open",
            description="open",
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        # Explicitly: no set_browser_guard call.
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_open",
                arguments={"url": "https://example.com/"},
            ),
            profile="local_execution",
        )
        assert result.success is False
        assert "BrowserGuard" in result.reason
        assert result.payload.get("ran") is not True

    @pytest.mark.asyncio
    async def test_browser_click_blocked_without_guard(self):
        from unittest.mock import MagicMock
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _noop_executor(req, ctx):
            return ToolExecutionResult(success=True, payload={"ran": True})

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="browser_click",
            description="click",
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_click",
                arguments={"element": "[1]"},
            ),
            profile="local_execution",
        )
        assert result.success is False
        assert "BrowserGuard" in result.reason

    @pytest.mark.asyncio
    async def test_browser_open_passes_when_guard_mounted(self):
        from unittest.mock import MagicMock
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        ran: list = []

        async def _noop_executor(req, ctx):
            ran.append(req.name)
            return ToolExecutionResult(success=True, payload={"ran": True})

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="browser_open",
            description="open",
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        runtime.set_browser_guard(_guard(block_internal_network=False))

        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_open",
                arguments={"url": "https://example.com/"},
            ),
            profile="local_execution",
        )
        assert result.success is True
        assert ran == ["browser_open"]
