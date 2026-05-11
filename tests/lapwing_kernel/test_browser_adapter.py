"""BrowserAdapter tests — fetch + personal profile dispatch + redaction.

Covers blueprint §6.2, §6.3, §6.4, §15.2 I-3 (no auto-bypass).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.lapwing_kernel.adapters.browser import (
    CHALLENGE_KIND_CAPTCHA,
    CHALLENGE_KIND_WAF,
    BrowserAdapter,
)
from src.lapwing_kernel.pipeline.continuation_registry import ContinuationRegistry
from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.interrupt import Interrupt
from src.lapwing_kernel.stores.interrupt_store import InterruptStore


# ── fakes for legacy BrowserManager ──────────────────────────────────────────


@dataclass
class FakePageState:
    url: str
    title: str
    elements: list
    text_summary: str
    tab_id: str = "tab-1"


class FakeBrowserManager:
    """Minimal stub mimicking BrowserManager surface used by the adapter."""

    def __init__(self, *, navigate_result: FakePageState | None = None,
                 page_text: str | None = None,
                 navigate_raises: Exception | None = None):
        self._navigate = navigate_result
        self._text = page_text
        self._raises = navigate_raises

    async def navigate(self, url: str, tab_id: str | None = None):
        if self._raises:
            raise self._raises
        return self._navigate

    async def get_page_text(self, tab_id: str | None = None):
        return self._text


@pytest.fixture(autouse=True)
def fresh_continuation_registry():
    ContinuationRegistry.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()


# ── Resource Protocol conformance ────────────────────────────────────────────


class TestProtocolConformance:
    def test_name_is_browser(self):
        a = BrowserAdapter(profile="fetch")
        assert a.name == "browser"

    def test_supports_known_verbs(self):
        a = BrowserAdapter(profile="fetch")
        for verb in ("navigate", "click", "type", "login", "form_submit"):
            assert a.supports(verb)

    def test_does_not_support_unknown_verb(self):
        a = BrowserAdapter(profile="fetch")
        assert not a.supports("fly_to_the_moon")


# ── fetch profile happy path ─────────────────────────────────────────────────


class TestFetchNavigate:
    async def test_returns_ok_with_redacted_content(self):
        page = FakePageState(
            url="https://example.com",
            title="Example",
            elements=[],
            text_summary="A normal page with no secrets.",
        )
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "https://example.com"})
        )
        assert obs.status == "ok"
        assert obs.content == "A normal page with no secrets."
        assert obs.provenance["url"] == "https://example.com"
        assert obs.provenance["profile"] == "fetch"

    async def test_page_state_not_leaked_raw(self):
        """PageState must never appear in Observation as raw object."""
        page = FakePageState(url="x", title="t", elements=[], text_summary="abc")
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "x"})
        )
        # Only artifact refs, no raw PageState
        for art in obs.artifacts:
            assert isinstance(art, dict)
            assert "page_state_ref" in art["type"] or "element_list_ref" in art["type"]
        assert not isinstance(obs.content, FakePageState)

    async def test_redacts_secrets_in_text_summary(self):
        """If page text contains a secret-shaped string, content is redacted."""
        page = FakePageState(
            url="x",
            title="t",
            elements=[],
            text_summary="JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJrZXZpbiJ9.signaturepart1234567890abcdef",
        )
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "x"})
        )
        assert "signaturepart1234567890abcdef" not in obs.content
        assert "REDACTED" in obs.content


# ── fetch profile: missing url + exceptions + WAF ────────────────────────────


class TestFetchEdgeCases:
    async def test_missing_url_returns_failed(self):
        a = BrowserAdapter(profile="fetch", legacy_browser_manager=FakeBrowserManager())
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={})
        )
        assert obs.status == "failed"
        assert obs.error == "missing_url"

    async def test_legacy_exception_becomes_failed(self):
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_raises=RuntimeError("boom")),
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "x"})
        )
        assert obs.status == "failed"
        assert obs.error == "RuntimeError"

    async def test_no_legacy_backend_returns_failed(self):
        a = BrowserAdapter(profile="fetch")
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "x"})
        )
        assert obs.status == "failed"
        assert "legacy_browser_manager" in obs.error


class TestFetchWAFInvariant:
    """Blueprint §15.2 I-3 + §6.x: fetch profile WAF does NOT create
    Interrupt. Observation marks waf_challenge but interrupt_id stays None."""

    async def test_waf_detected_returns_waf_challenge(self, tmp_path):
        page = FakePageState(
            url="https://blocked.com",
            title="Just a moment...",
            elements=[],
            text_summary="Checking your browser before accessing... cloudflare",
        )
        store = InterruptStore(tmp_path / "lapwing.db")
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
            interrupt_store=store,
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "https://blocked.com"})
        )
        assert obs.status == "waf_challenge"
        # Critical: no Interrupt persisted on fetch profile
        assert obs.interrupt_id is None
        assert store.list_pending() == []

    async def test_waf_provenance_records_url_and_profile(self):
        page = FakePageState(
            url="https://blocked.com",
            title="Just a moment...",
            elements=[],
            text_summary="checking your browser before accessing...",
        )
        a = BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="fetch", args={"url": "https://blocked.com"})
        )
        assert obs.provenance["profile"] == "fetch"
        assert obs.provenance["url"] == "https://blocked.com"


# ── personal profile: creates Interrupt with continuation_ref ────────────────


class TestPersonalProfileInterrupt:
    """Personal profile CAN takeover (persistent identity + headful via Xvfb),
    so challenges create resumable Interrupts (blueprint §15.2 I-3)."""

    async def test_captcha_creates_interrupt_with_continuation(self, tmp_path):
        page = FakePageState(
            url="https://login.com",
            title="Login",
            elements=[],
            text_summary="please complete the captcha to continue",
        )
        store = InterruptStore(tmp_path / "lapwing.db")
        a = BrowserAdapter(
            profile="personal",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
            interrupt_store=store,
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="personal", args={"url": "https://login.com"})
        )
        assert obs.status == "captcha_required"
        assert obs.interrupt_id is not None
        # Persisted with continuation_ref
        persisted = store.get(obs.interrupt_id)
        assert persisted is not None
        assert persisted.kind == "browser.captcha"
        assert persisted.continuation_ref is not None
        assert persisted.expires_at is not None
        # Continuation alive in registry
        assert ContinuationRegistry.instance().has(persisted.continuation_ref)

    async def test_waf_on_personal_creates_interrupt(self, tmp_path):
        page = FakePageState(
            url="https://blocked.com",
            title="Just a moment...",
            elements=[],
            text_summary="checking your browser before accessing... cloudflare",
        )
        store = InterruptStore(tmp_path / "lapwing.db")
        a = BrowserAdapter(
            profile="personal",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
            interrupt_store=store,
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="personal", args={"url": "https://blocked.com"})
        )
        assert obs.status == "waf_challenge"
        assert obs.interrupt_id is not None
        persisted = store.get(obs.interrupt_id)
        assert persisted.kind == "browser.waf"

    async def test_personal_normal_page_returns_ok(self, tmp_path):
        page = FakePageState(
            url="https://normal.com",
            title="Normal",
            elements=[],
            text_summary="a normal page",
        )
        store = InterruptStore(tmp_path / "lapwing.db")
        a = BrowserAdapter(
            profile="personal",
            legacy_browser_manager=FakeBrowserManager(navigate_result=page),
            interrupt_store=store,
        )
        obs = await a.execute(
            Action.new("browser", "navigate", resource_profile="personal", args={"url": "https://normal.com"})
        )
        assert obs.status == "ok"
        # No interrupt persisted
        assert store.list_pending() == []

    async def test_login_verb_requires_credential_adapter(self):
        a = BrowserAdapter(profile="personal", legacy_browser_manager=FakeBrowserManager())
        obs = await a.execute(
            Action.new("browser", "login", resource_profile="personal", args={"service": "github"})
        )
        assert obs.status == "failed"
        assert "CredentialAdapter" in obs.error


# ── invariants ───────────────────────────────────────────────────────────────


class TestNoAutoBypass:
    """I-3: captcha_required does NOT trigger auto-solve / vision OCR /
    proxy rotation / fingerprint randomization."""

    async def test_no_auto_solve_module_invoked(self, tmp_path, monkeypatch):
        # If any "solve_captcha" / "stealth" / "ua_rotate" path existed in
        # the adapter, it would have to be importable here. We just assert
        # the source has no such imports.
        from pathlib import Path

        adapter_src = Path(
            "/home/kevin/lapwing/src/lapwing_kernel/adapters/browser.py"
        ).read_text()
        for forbidden in (
            "solve_captcha",
            "ocr_captcha",
            "stealth_mode",
            "proxy_rotation",
            "fingerprint_random",
            "user_agent_rotate",
        ):
            assert forbidden not in adapter_src, (
                f"BrowserAdapter source contains {forbidden!r} — violates "
                f"I-3 no-auto-bypass invariant (blueprint §15.2)."
            )

    async def test_unknown_profile_raises(self):
        a = BrowserAdapter(profile="operator")  # v1 disabled
        with pytest.raises(ValueError, match="Unknown browser profile"):
            await a.execute(Action.new("browser", "navigate", resource_profile="operator"))


# ── challenge detection delegation ───────────────────────────────────────────


class TestChallengeDetection:
    def test_smartfetcher_is_challenge_page_is_public(self):
        """The rename from _is_challenge_page → is_challenge_page surfaces
        the static method for adapter reuse without poking private API."""
        from src.research.fetcher import SmartFetcher

        assert callable(SmartFetcher.is_challenge_page)
        # And the basic Cloudflare-shaped string detects positive
        assert SmartFetcher.is_challenge_page(
            "checking your browser before accessing"
        )
        # Plain text doesn't
        assert not SmartFetcher.is_challenge_page("hello world this is normal")
