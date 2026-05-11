"""PolicyDecider — function-shaped policy decisions.

Single decision point: ALLOW / INTERRUPT / BLOCK. No class hierarchy until ≥3
concrete rule families share behavior (blueprint §4.4 / §11.1).

See docs/architecture/lapwing_v1_blueprint.md §4.4.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from .primitives.action import Action


class PolicyDecision(Enum):
    ALLOW = "allow"
    INTERRUPT = "interrupt"
    BLOCK = "block"


class CredentialUseStateProtocol(Protocol):
    """First-use approval state. Implemented by Slice G."""

    def has_been_used(self, service: str) -> bool: ...


class PolicyDecider:
    """Single function-shaped decision point.

    Rule sources:
      - browser.fetch: URL allowlist / blocklist
      - browser.personal: more permissive (signed-in profile)
      - browser sensitive verbs (login, download, form_submit): INTERRUPT
      - credential.use: INTERRUPT on first-time use, ALLOW after (state lives
        in CredentialUseState, NOT config — blueprint §7.4)
      - high-risk verbs: INTERRUPT
    """

    def __init__(
        self,
        config: dict[str, Any],
        use_state: CredentialUseStateProtocol | None = None,
    ):
        self._cfg = config
        browser_fetch_cfg = config.get("browser_fetch", {}) or {}
        self._url_allowlist = set(browser_fetch_cfg.get("url_allowlist", []) or [])
        self._url_blocklist = set(browser_fetch_cfg.get("url_blocklist", []) or [])
        # Slice G injects real CredentialUseState. Until then, conservative
        # default treats every credential.use as first-use → INTERRUPT.
        self._use_state = use_state

    def decide(self, action: Action) -> PolicyDecision:
        if action.resource == "browser":
            return self._decide_browser(action)
        if action.resource == "credential":
            return self._decide_credential(action)
        return PolicyDecision.ALLOW

    def _decide_browser(self, action: Action) -> PolicyDecision:
        profile = action.resource_profile or "fetch"
        verb = action.verb
        url = action.args.get("url", "")

        if profile == "fetch":
            if self._url_blocklist and any(b in url for b in self._url_blocklist):
                return PolicyDecision.BLOCK
            return PolicyDecision.ALLOW

        if profile == "personal":
            if verb in {"login", "download", "form_submit"}:
                return PolicyDecision.INTERRUPT
            return PolicyDecision.ALLOW

        return PolicyDecision.ALLOW

    def _decide_credential(self, action: Action) -> PolicyDecision:
        if action.verb == "use":
            service = action.args.get("service")
            if self._use_state is None:
                return PolicyDecision.INTERRUPT
            if service and self._use_state.has_been_used(service):
                return PolicyDecision.ALLOW
            return PolicyDecision.INTERRUPT
        return PolicyDecision.ALLOW
