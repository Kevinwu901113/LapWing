"""BrowserAdapter — single class, multiple profiles.

Profiles:
  - fetch:    headless, ephemeral, public web only. Wraps the existing
              BrowserManager (src/core/browser_manager.py) as legacy backend.
              WAF/CAPTCHA on fetch profile does NOT create an Interrupt
              (blueprint §6.2 / §15.2 I-3): ephemeral has no persistent
              identity, owner takeover is meaningless, and routing the
              attention queue with un-actionable items pollutes it.
              Instead: Observation(status="waf_challenge", interrupt_id=None).
  - personal: headful (Xvfb-backed on PVE, blueprint §6.6), persistent
              user_data_dir, signed-in identity. CAPTCHA → real Interrupt
              with continuation_ref so the agent worker can await owner
              takeover. v1 implements the policy surface and challenge
              detection; the actual Playwright persistent context launch
              lands in PR-08 (resume e2e) where it is exercised end-to-end.

LLM-facing fields are redacted (defense-in-depth Layer 2) — PageState and
artifacts never reach LLM raw; only the redacted text_summary goes into
Observation.content; element lists / screenshots are artifacts referenced
by id, not auto-rendered.

See docs/architecture/lapwing_v1_blueprint.md §6.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.interrupt import (
    DEFAULT_INTERRUPT_EXPIRY,
    Interrupt,
)
from src.lapwing_kernel.primitives.observation import Observation
from src.lapwing_kernel.redactor import SecretRedactor

logger = logging.getLogger(__name__)


# Challenge kinds that personal profile can resolve via owner takeover.
CHALLENGE_KIND_CAPTCHA = "captcha"
CHALLENGE_KIND_WAF = "waf"
CHALLENGE_KIND_LOGIN = "login_required"
CHALLENGE_KIND_2FA = "auth_2fa"


def _new_id() -> str:
    return str(uuid.uuid4())


class BrowserAdapter:
    """Single adapter, multiple profiles. Conforms to Resource Protocol."""

    name: ClassVar[str] = "browser"
    SUPPORTED_VERBS: ClassVar[frozenset[str]] = frozenset(
        {
            "navigate",
            "click",
            "type",
            "select",
            "scroll",
            "screenshot",
            "get_text",
            "back",
            "forward",
            "wait",
            "login",
            "form_submit",
            "download",
        }
    )

    def __init__(
        self,
        *,
        profile: str,
        config: dict[str, Any] | None = None,
        redactor: SecretRedactor | None = None,
        interrupt_store: Any = None,
        legacy_browser_manager: Any = None,
    ):
        self.profile = profile
        self._cfg = config or {}
        self._redactor = redactor or SecretRedactor()
        self._interrupts = interrupt_store
        self._legacy = legacy_browser_manager
        # personal profile owns its own Playwright context; populated lazily.
        self._personal_context: Any = None

    def supports(self, verb: str) -> bool:
        return verb in self.SUPPORTED_VERBS

    async def execute(self, action: Action) -> Observation:
        if self.profile == "fetch":
            return await self._execute_fetch(action)
        if self.profile == "personal":
            return await self._execute_personal(action)
        raise ValueError(f"Unknown browser profile: {self.profile!r}")

    # ── fetch profile (wraps BrowserManager) ─────────────────────────────────

    async def _execute_fetch(self, action: Action) -> Observation:
        if self._legacy is None:
            return Observation.failure(
                action.id,
                "browser",
                status="failed",
                error="fetch profile requires legacy_browser_manager",
            )

        verb = action.verb

        if verb == "navigate":
            url = action.args.get("url")
            if not url:
                return Observation.failure(
                    action.id, "browser", status="failed", error="missing_url"
                )
            try:
                page_state = await self._legacy.navigate(url)
            except Exception as exc:
                logger.warning("browser.fetch.navigate failed: %s", exc)
                return Observation.failure(
                    action.id,
                    "browser",
                    status="failed",
                    error=type(exc).__name__,
                    summary=str(exc)[:200],
                )

            challenge = self._detect_challenge_from_page_state(page_state)
            if challenge is not None:
                # fetch profile: NO Interrupt. See class docstring.
                return Observation(
                    id=_new_id(),
                    action_id=action.id,
                    resource="browser",
                    status="waf_challenge",
                    interrupt_id=None,
                    summary=(
                        f"WAF/challenge ({challenge}) on {url}; fetch profile "
                        f"cannot bypass. Consider retry via personal profile "
                        f"if persistent identity helps."
                    ),
                    provenance={"url": url, "profile": "fetch", "challenge": challenge},
                )

            return self._translate_page_state(action, page_state)

        if verb == "get_text":
            url = action.args.get("url")
            tab_id = action.args.get("tab_id")
            try:
                text = await self._legacy.get_page_text(tab_id=tab_id)
            except Exception as exc:
                return Observation.failure(
                    action.id,
                    "browser",
                    status="failed",
                    error=type(exc).__name__,
                    summary=str(exc)[:200],
                )
            if text is None:
                return Observation(
                    id=_new_id(),
                    action_id=action.id,
                    resource="browser",
                    status="empty_content",
                    summary=f"no content from {url or tab_id}",
                )
            redacted = self._redactor.redact_text(text)
            return Observation.ok(
                action.id,
                "browser",
                summary=f"got_text {len(text)} chars",
                content=redacted,
                provenance={"profile": "fetch", "url": url, "tab_id": tab_id},
            )

        # Unsupported verbs on fetch profile return failed
        return Observation.failure(
            action.id,
            "browser",
            status="failed",
            error=f"unsupported_verb_on_fetch:{verb}",
        )

    # ── personal profile (headful, persistent, can interrupt) ────────────────

    async def _execute_personal(self, action: Action) -> Observation:
        """Personal profile dispatch.

        v1: scaffold + interrupt path. The actual Playwright persistent
        context lifecycle is wired in PR-08 (resume e2e) where the
        closed-loop §15.1 test drives it. Until then, an action on the
        personal profile either:
          - hits a challenge → returns Observation(status=...)+Interrupt
          - succeeds via the same BrowserManager fallback (for non-interruptable
            verbs, sharing the legacy backend is fine — the persistent context
            difference matters mainly for cookies + headful takeover)
        """
        # Personal profile honors policy INTERRUPT for login/download/form_submit
        # before reaching the adapter — that's handled in PolicyDecider. If we
        # get here it's an allowed verb. Currently delegate to the same legacy
        # backend as fetch; the persistent-context distinction is exercised in
        # PR-08.
        if self._legacy is None:
            return Observation.failure(
                action.id,
                "browser",
                status="failed",
                error="personal profile requires legacy_browser_manager in this build",
            )

        verb = action.verb
        if verb == "navigate":
            url = action.args.get("url")
            if not url:
                return Observation.failure(
                    action.id, "browser", status="failed", error="missing_url"
                )
            try:
                page_state = await self._legacy.navigate(url)
            except Exception as exc:
                return Observation.failure(
                    action.id,
                    "browser",
                    status="timeout",
                    error=type(exc).__name__,
                    summary=str(exc)[:200],
                )

            challenge = self._detect_challenge_from_page_state(page_state)
            if challenge is not None:
                # Personal profile CAN takeover — emit Interrupt with continuation
                return self._emit_personal_interrupt(action, challenge, url=url)

            return self._translate_page_state(action, page_state)

        if verb == "login":
            # login on personal profile: PolicyDecider returns INTERRUPT, so
            # the executor's policy-INTERRUPT branch handles it before reaching
            # here. If we ever do reach here (e.g. after owner approve resumes
            # the action), credential lookup goes through Action(credential.use)
            # — the lease consumption logic lands in PR-07.
            return Observation.failure(
                action.id,
                "browser",
                status="failed",
                error="login requires CredentialAdapter wiring (Slice G / PR-07)",
            )

        # Other verbs: delegate to legacy backend like fetch
        return await self._execute_fetch(action)

    def _emit_personal_interrupt(
        self, action: Action, challenge: str, *, url: str
    ) -> Observation:
        """Create + persist a personal-profile Interrupt with continuation_ref."""
        from src.lapwing_kernel.pipeline.continuation_registry import (
            ContinuationRegistry,
        )

        if self._interrupts is None:
            # No store wired → fall back to non-actionable observation
            return Observation(
                id=_new_id(),
                action_id=action.id,
                resource="browser",
                status="captcha_required" if challenge == CHALLENGE_KIND_CAPTCHA else "user_attention_required",
                summary=f"{challenge} on {url}; no InterruptStore wired",
                provenance={"url": url, "profile": "personal", "challenge": challenge},
            )

        continuation_ref = ContinuationRegistry.instance().register(action.task_ref)
        kind = f"browser.{challenge}"
        interrupt = Interrupt.new(
            kind=kind,
            actor_required="owner",
            resource="browser",
            resource_ref=None,
            continuation_ref=continuation_ref,
            summary=f"{challenge} on {url}; awaiting owner takeover",
            payload_redacted=self._redactor.redact_dict(
                {"url": url, "profile": "personal", "challenge": challenge}
            ),
            expires_in=DEFAULT_INTERRUPT_EXPIRY.get(kind),
        )
        self._interrupts.persist(interrupt)

        status_map = {
            CHALLENGE_KIND_CAPTCHA: "captcha_required",
            CHALLENGE_KIND_WAF: "waf_challenge",
            CHALLENGE_KIND_LOGIN: "auth_required",
            CHALLENGE_KIND_2FA: "user_attention_required",
        }
        return Observation(
            id=_new_id(),
            action_id=action.id,
            resource="browser",
            status=status_map.get(challenge, "user_attention_required"),
            summary=interrupt.summary,
            interrupt_id=interrupt.id,
            provenance={"url": url, "profile": "personal", "challenge": challenge},
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _detect_challenge_from_page_state(self, page_state: Any) -> str | None:
        """Returns the challenge kind ("captcha"/"waf"/...) or None.

        Surfaces SmartFetcher.is_challenge_page (blueprint §6.x: reuse, don't
        re-implement). For the basic WAF/Cloudflare case, SmartFetcher's text
        marker detection covers it. CAPTCHA-specific markers can be added here
        without changing SmartFetcher.
        """
        from src.research.fetcher import SmartFetcher

        text_summary = getattr(page_state, "text_summary", None) or ""
        if SmartFetcher.is_challenge_page(text_summary):
            return CHALLENGE_KIND_WAF

        # Lightweight CAPTCHA heuristic. Full detection (image classifiers,
        # iframe inspection, Cloudflare-Turnstile-specific markers) is out of
        # scope for v1 — interrupts are the safety mechanism, not bypass.
        lowered = text_summary.lower()
        for marker in ("captcha", "i'm not a robot", "verify you are human"):
            if marker in lowered:
                return CHALLENGE_KIND_CAPTCHA
        return None

    def _translate_page_state(self, action: Action, page_state: Any) -> Observation:
        """PageState → Observation translation contract (blueprint §6.3).

          - PageState is internal; never leaves the adapter as raw object
          - Observation.content: redacted text_summary
          - Observation.artifacts: page_state_ref / screenshot_ref / element_list_ref
            (NOT auto LLM-facing)
        """
        text_summary = self._redactor.redact_text(
            getattr(page_state, "text_summary", "") or ""
        )

        artifacts: list[dict] = [
            {
                "type": "page_state_ref",
                "ref": getattr(page_state, "tab_id", _new_id()),
                "url": getattr(page_state, "url", ""),
            }
        ]
        # element_list_ref: reference, not rendered into content
        elements = getattr(page_state, "elements", None) or []
        artifacts.append(
            {
                "type": "element_list_ref",
                "ref": getattr(page_state, "tab_id", _new_id()),
                "count": len(elements),
            }
        )

        title = getattr(page_state, "title", "")
        url = getattr(page_state, "url", "")
        return Observation.ok(
            action.id,
            "browser",
            summary=f"loaded {url}: {title}" if title else f"loaded {url}",
            content=text_summary,
            artifacts=[self._redactor.redact_dict(a) for a in artifacts],
            provenance={
                "url": url,
                "title": title,
                "profile": self.profile,
            },
        )
