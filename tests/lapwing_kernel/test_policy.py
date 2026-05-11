"""PolicyDecider tests — ALLOW / INTERRUPT / BLOCK across browser + credential."""
from __future__ import annotations

from src.lapwing_kernel.policy import PolicyDecider, PolicyDecision
from src.lapwing_kernel.primitives.action import Action


class StubUseState:
    def __init__(self, *, used: set[str] | None = None):
        self._used = used or set()

    def has_been_used(self, service: str) -> bool:
        return service in self._used


class TestBrowserFetchPolicy:
    def test_fetch_allows_default(self):
        p = PolicyDecider(config={})
        a = Action.new("browser", "navigate", resource_profile="fetch", args={"url": "https://x.com"})
        assert p.decide(a) == PolicyDecision.ALLOW

    def test_fetch_blocks_blocklist_url(self):
        p = PolicyDecider(
            config={"browser_fetch": {"url_blocklist": ["evil.com"]}}
        )
        a = Action.new(
            "browser", "navigate", resource_profile="fetch", args={"url": "https://evil.com/x"}
        )
        assert p.decide(a) == PolicyDecision.BLOCK

    def test_fetch_default_when_profile_unspecified(self):
        """Missing resource_profile defaults to 'fetch' behavior."""
        p = PolicyDecider(config={})
        a = Action.new("browser", "navigate", args={"url": "https://x.com"})
        assert p.decide(a) == PolicyDecision.ALLOW


class TestBrowserPersonalPolicy:
    def test_personal_navigate_allows(self):
        p = PolicyDecider(config={})
        a = Action.new(
            "browser", "navigate", resource_profile="personal", args={"url": "https://x.com"}
        )
        assert p.decide(a) == PolicyDecision.ALLOW

    def test_personal_login_interrupts(self):
        p = PolicyDecider(config={})
        a = Action.new("browser", "login", resource_profile="personal")
        assert p.decide(a) == PolicyDecision.INTERRUPT

    def test_personal_download_interrupts(self):
        p = PolicyDecider(config={})
        a = Action.new("browser", "download", resource_profile="personal")
        assert p.decide(a) == PolicyDecision.INTERRUPT

    def test_personal_form_submit_interrupts(self):
        p = PolicyDecider(config={})
        a = Action.new("browser", "form_submit", resource_profile="personal")
        assert p.decide(a) == PolicyDecision.INTERRUPT


class TestCredentialPolicy:
    def test_use_first_time_interrupts(self):
        p = PolicyDecider(config={}, use_state=StubUseState())
        a = Action.new("credential", "use", args={"service": "github"})
        assert p.decide(a) == PolicyDecision.INTERRUPT

    def test_use_previously_approved_allows(self):
        p = PolicyDecider(config={}, use_state=StubUseState(used={"github"}))
        a = Action.new("credential", "use", args={"service": "github"})
        assert p.decide(a) == PolicyDecision.ALLOW

    def test_use_no_state_injected_conservative_interrupt(self):
        """Without CredentialUseState wired, treat every use as first-use."""
        p = PolicyDecider(config={})
        a = Action.new("credential", "use", args={"service": "github"})
        assert p.decide(a) == PolicyDecision.INTERRUPT

    def test_non_use_verb_allows(self):
        """list_count / exists are not gated by first-use."""
        p = PolicyDecider(config={})
        for verb in ("list_count", "exists"):
            a = Action.new("credential", verb)
            assert p.decide(a) == PolicyDecision.ALLOW


class TestDefaultPolicy:
    def test_unknown_resource_allows(self):
        """Default policy is permissive on unknown resources (real wiring will
        register specific deciders per resource).
        """
        p = PolicyDecider(config={})
        a = Action.new("unknown_resource", "any_verb")
        assert p.decide(a) == PolicyDecision.ALLOW
