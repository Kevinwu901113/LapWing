"""SecretRedactor — two-layer defense for LLM-visible browser content.

Layer 1: JS-side sensitive-input filter (extraction never emits value)
Layer 2: Python-side secret-shaped string scrubbing (defense in depth)

Wraps src/core/credential_sanitizer.redact_secrets for the conservative
patterns shared with shell/log redaction, and adds broader patterns that are
acceptable false-positive-wise only on browser DOM content.

See docs/architecture/lapwing_v1_blueprint.md §5.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from src.core.credential_sanitizer import redact_secrets

from .primitives.observation import Observation


REDACTED = "[REDACTED]"


# ── Layer 1: sensitive input detection ──────────────────────────────────────

SENSITIVE_INPUT_TYPES = frozenset({"password"})

SENSITIVE_AUTOCOMPLETE = frozenset(
    {
        "one-time-code",
        "current-password",
        "new-password",
    }
)

# Field-name / placeholder / aria-label substring trigger
SENSITIVE_NAME_PATTERNS = re.compile(
    r"(?i)("
    r"otp|passcode|password|token|secret|"
    r"recovery[\W_]?code|api[\W_]?key|"
    r"private[\W_]?key|access[\W_]?token|refresh[\W_]?token"
    r")"
)


# ── Layer 2: broader secret-shaped patterns (browser-only) ──────────────────
# These are intentionally more aggressive than credential_sanitizer because
# the consumer is LLM-facing browser DOM content — false positives there cost
# less than a leak. Conservative shared patterns live in credential_sanitizer.

_BROWSER_EXTRA_PATTERNS: list[re.Pattern] = [
    # Long base64-ish blobs (40+ chars, typical secret length)
    # Slack tokens (xox*-) handled by upstream credential_sanitizer.
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    # Long hex-ish blobs (32+ chars, typical secret length)
    re.compile(r"\b[a-f0-9]{32,}\b"),
]


class SecretRedactor:
    """Two-layer defense:
      Layer 1: is_sensitive_input() — caller (JS extractor) drops value entirely
      Layer 2: redact_text / redact_dict / redact_observation — Python-side scrub
    """

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self._extra_patterns: list[re.Pattern] = [
            re.compile(p) for p in config.get("extra_patterns", [])
        ]

    # Layer 2: text scrub
    def redact_text(self, text: str | None) -> str | None:
        if text is None or not text:
            return text
        # First: shared conservative patterns (existing credential_sanitizer)
        text = redact_secrets(text)
        # Then: broader browser-only patterns
        for pat in _BROWSER_EXTRA_PATTERNS:
            text = pat.sub(REDACTED, text)
        for pat in self._extra_patterns:
            text = pat.sub(REDACTED, text)
        return text

    def redact_dict(self, d: dict[str, Any] | None) -> dict[str, Any]:
        if not d:
            return {}
        out: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(k, str) and SENSITIVE_NAME_PATTERNS.search(k):
                out[k] = REDACTED
                continue
            if isinstance(v, str):
                out[k] = self.redact_text(v)
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif isinstance(v, list):
                out[k] = [
                    self.redact_text(x) if isinstance(x, str) else x for x in v
                ]
            else:
                out[k] = v
        return out

    def redact_observation(self, obs: Observation) -> Observation:
        """Defense-in-depth: even if adapter forgot to redact, content + artifacts
        + provenance get scrubbed here before the Observation leaves the executor.
        """
        return replace(
            obs,
            summary=self.redact_text(obs.summary),
            content=self.redact_text(obs.content),
            artifacts=[self.redact_dict(a) for a in obs.artifacts],
            provenance=self.redact_dict(obs.provenance),
        )

    # Layer 1: caller-side sensitive-input check
    @staticmethod
    def is_sensitive_input(
        input_type: str | None,
        name: str | None,
        autocomplete: str | None,
        placeholder: str | None,
        aria_label: str | None,
    ) -> bool:
        if input_type and input_type.lower() in SENSITIVE_INPUT_TYPES:
            return True
        if autocomplete and autocomplete.lower() in SENSITIVE_AUTOCOMPLETE:
            return True
        for s in (name, placeholder, aria_label):
            if s and SENSITIVE_NAME_PATTERNS.search(s):
                return True
        return False
