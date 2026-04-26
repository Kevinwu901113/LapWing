"""BrowserGuard — pre-execution safety checks for the browser_* tools.

Lapwing's Playwright browser automation can drive any web page on the
host. Without a guard, the LLM could be redirected (or convinced) into
navigating to file://, internal admin panels on localhost, or pages
that look like a Kevin-grade login form. This module enforces:

- URL scheme allowlist (http / https only).
- file:// URLs blocked.
- Localhost and private/loopback IP ranges blocked when configured.
- Per-domain blacklist + whitelist.
- Per-session action budget — bounds total Playwright operations.
- Sensitive action words on click targets ("submit order", "delete", …)
  surface as require_consent so the loop can pause for OWNER ack.
- Form-field / credential / destructive detection on type and click.
- JavaScript evaluation guarded against ``window.location`` / ``eval``
  / data exfil patterns.

When the BrowserGuard is *not* installed at all (``None``), browser
tools must refuse to run — the harness wires this in
``src.tools.browser_tools.register_browser_tools`` and in
``TaskRuntime`` when ``tool.capability == "browser"``.

The guard returns a ``GuardOutcome`` with ``action`` in
{"allow", "block", "require_consent"} and a ``reason`` string. Callers
short-circuit on anything that isn't ``allow``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

logger = logging.getLogger("lapwing.core.browser_guard")


# Schemes the guard *ever* allows. file://, javascript:, data:, blob:
# stay blocked unconditionally — they're either local-FS access or
# common XSS-style payload carriers.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Schemes we explicitly call out as blocked so the reason is precise.
_BLOCKED_SCHEMES: frozenset[str] = frozenset(
    {"file", "javascript", "data", "blob", "vbscript", "view-source"}
)

# Action names for browser tools that we treat as state-changing — these
# are the ones we run sensitive-word + budget checks on.
_STATE_CHANGING_ACTIONS: frozenset[str] = frozenset(
    {"click", "type", "select", "submit", "login"}
)

# Destructive verbs in element text that a reasonable user would expect
# to confirm before executing. require_consent (not block) so OWNER can
# still proceed with explicit ack.
_DESTRUCTIVE_VERBS_DEFAULT: tuple[str, ...] = (
    "delete", "remove", "pay", "purchase", "buy",
    "submit order", "confirm order", "place order", "transfer",
    "删除", "移除", "支付", "购买", "确认订单", "提交订单", "转账",
)

# Patterns in JS expressions that look like exfiltration / location
# rewrite / unsafe eval. Block on match.
_DANGEROUS_JS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bdocument\.cookie\b",
        r"\blocalstorage\b",
        r"\bsessionstorage\b",
        r"\beval\s*\(",
        r"\bnew\s+function\s*\(",
        r"\bwindow\.location\s*=",
        r"\blocation\.href\s*=",
        r"\blocation\.replace\s*\(",
        r"\bxmlhttprequest\b",
        # data exfil via fetch to absolute URLs is ambiguous — guard catches
        # the common "send something out via fetch" shape conservatively.
        r"\bfetch\s*\(\s*['\"]https?://",
    )
)


@dataclass(frozen=True)
class GuardOutcome:
    action: str           # "allow" | "block" | "require_consent"
    reason: str = ""

    @classmethod
    def allow(cls) -> "GuardOutcome":
        return cls(action="allow", reason="")

    @classmethod
    def block(cls, reason: str) -> "GuardOutcome":
        return cls(action="block", reason=reason)

    @classmethod
    def consent(cls, reason: str) -> "GuardOutcome":
        return cls(action="require_consent", reason=reason)


@dataclass
class BrowserGuard:
    """Stateful guard. Holds the per-session action budget."""

    block_internal_network: bool = True
    url_blacklist: tuple[str, ...] = ()
    url_whitelist: tuple[str, ...] = ()
    sensitive_words: tuple[str, ...] = _DESTRUCTIVE_VERBS_DEFAULT
    max_actions_per_session: int = 60
    block_downloads: bool = True
    _action_count: int = field(default=0, init=False, repr=False)

    @classmethod
    def from_settings(cls, browser_cfg) -> "BrowserGuard":
        """Build from a BrowserConfig pydantic model.

        Picks up url_blacklist, url_whitelist, sensitive_action_words,
        and block_internal_network. Other fields keep dataclass defaults.
        """
        return cls(
            block_internal_network=getattr(browser_cfg, "block_internal_network", True),
            url_blacklist=tuple(getattr(browser_cfg, "url_blacklist", []) or []),
            url_whitelist=tuple(getattr(browser_cfg, "url_whitelist", []) or []),
            sensitive_words=tuple(
                getattr(browser_cfg, "sensitive_action_words", _DESTRUCTIVE_VERBS_DEFAULT)
                or _DESTRUCTIVE_VERBS_DEFAULT
            ),
        )

    def reset_budget(self) -> None:
        """Reset the per-session action counter (e.g. between user turns)."""
        self._action_count = 0

    def actions_used(self) -> int:
        return self._action_count

    # ── URL gate ────────────────────────────────────────────────────

    def check_url(self, url: str) -> GuardOutcome:
        if not url or not url.strip():
            return GuardOutcome.block("URL is empty")
        try:
            parsed = urlparse(url.strip())
        except Exception as exc:
            return GuardOutcome.block(f"URL 解析失败: {exc}")
        scheme = (parsed.scheme or "").lower()
        if scheme in _BLOCKED_SCHEMES:
            return GuardOutcome.block(f"scheme '{scheme}://' 不允许")
        if scheme not in _ALLOWED_SCHEMES:
            return GuardOutcome.block(
                f"只接受 http/https，收到 '{scheme or '<empty>'}://'"
            )
        host = (parsed.hostname or "").lower()
        if not host:
            return GuardOutcome.block("URL 缺少 host")
        # Whitelist short-circuit (if non-empty, only whitelisted hosts pass)
        if self.url_whitelist:
            if not _host_matches_any(host, self.url_whitelist):
                return GuardOutcome.block(
                    f"host '{host}' 不在 url_whitelist 中"
                )
        if _host_matches_any(host, self.url_blacklist):
            return GuardOutcome.block(f"host '{host}' 在 url_blacklist 中")
        if self.block_internal_network and _is_internal_host(host):
            return GuardOutcome.block(
                f"host '{host}' 是内网/loopback/私有地址"
            )
        return GuardOutcome.allow()

    # ── Action gate ─────────────────────────────────────────────────

    def check_action(
        self,
        action: str,
        element_text: str = "",
        url: str = "",
    ) -> GuardOutcome:
        action_norm = (action or "").strip().lower()
        # Action budget
        if action_norm in _STATE_CHANGING_ACTIONS:
            if self._action_count >= self.max_actions_per_session:
                return GuardOutcome.block(
                    f"action_budget_exceeded:{self._action_count}/"
                    f"{self.max_actions_per_session}"
                )
            self._action_count += 1
        # Re-check the URL on each state-changing action — page may have
        # navigated since the last check.
        if url:
            url_outcome = self.check_url(url)
            if url_outcome.action != "allow":
                return url_outcome
        # Login / credential / form-submit actions: always require consent.
        if action_norm == "login":
            return GuardOutcome.consent(
                "browser_login 要求 OWNER 显式确认凭据使用"
            )
        # Sensitive words on element text — destructive verbs surface
        # as require_consent, not block; OWNER can ack and proceed.
        text_lc = (element_text or "").lower()
        for word in self.sensitive_words:
            if not word:
                continue
            if word.lower() in text_lc:
                return GuardOutcome.consent(
                    f"敏感操作词 '{word}' 触发确认: 元素='{element_text[:80]}'"
                )
        return GuardOutcome.allow()

    # ── JS gate ─────────────────────────────────────────────────────

    def check_js(self, expression: str) -> GuardOutcome:
        if not expression or not expression.strip():
            return GuardOutcome.block("JS 表达式为空")
        text = expression.strip()
        for pattern in _DANGEROUS_JS_PATTERNS:
            m = pattern.search(text)
            if m:
                return GuardOutcome.block(
                    f"JS 触发危险模式: {m.group(0)[:60]}"
                )
        return GuardOutcome.allow()

    # ── Download gate ───────────────────────────────────────────────

    def check_download(self, url: str = "", filename: str = "") -> GuardOutcome:
        if self.block_downloads:
            return GuardOutcome.block(
                f"downloads disabled by guard (url={url!r}, file={filename!r})"
            )
        return self.check_url(url) if url else GuardOutcome.allow()


# ── Helpers ────────────────────────────────────────────────────────


def _host_matches_any(host: str, patterns: Iterable[str]) -> bool:
    """Match host against a list of plain-host or suffix patterns.

    A pattern may be ``example.com`` (matches example.com and any
    subdomain) or ``foo.example.com`` (exact host).
    """
    host = host.lower().strip()
    for raw in patterns:
        if not raw:
            continue
        pat = raw.lower().strip().lstrip("*").lstrip(".")
        if not pat:
            continue
        if host == pat or host.endswith("." + pat):
            return True
    return False


def _is_internal_host(host: str) -> bool:
    """Return True for localhost, .local, and private IP ranges."""
    host = host.lower().strip()
    if host in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan"):
        return True
    # Strip IPv6 brackets if present
    cleaned = host.strip("[]")
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
    )
