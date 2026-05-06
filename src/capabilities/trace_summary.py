"""TraceSummary: user-provided execution/trace summary model with secrets redaction.

Treats all input as untrusted user data.  No LLM, network, shell, or file reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Keys that must be dropped from input — could contain hidden CoT or internal state.
_DROP_KEYS: frozenset[str] = frozenset({
    "_cot", "_chain_of_thought", "chain_of_thought", "_internal",
    "_reasoning", "_thinking", "reasoning_trace",
    "scratchpad", "hidden_thoughts", "internal_notes",
})

# Upper bound for string fields to prevent ballooning.
_MAX_STR_LEN = 50_000

# Module-level compiled patterns for secrets redaction.
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), 'sk-<REDACTED>'),
    (re.compile(r'API[_-]?KEY\s*=\s*["\' ]?[^\s"\'\n]+', re.IGNORECASE), 'API_KEY=<REDACTED>'),
    (re.compile(r'Authorization:\s*Bearer\s+\S+', re.IGNORECASE), 'Authorization: Bearer <REDACTED>'),
    (re.compile(r'password\s*=\s*["\' ]?[^\s"\'\n]+', re.IGNORECASE), 'password=<REDACTED>'),
    (re.compile(
        r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\s*.*?\s*-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        re.DOTALL,
    ), '-----BEGIN PRIVATE KEY----- <REDACTED> -----END PRIVATE KEY-----'),
]


def _redact_text(text: str) -> str:
    """Apply all secret patterns to a string and return the redacted version."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _coerce_list(value: Any) -> list[str]:
    """Coerce input to a list of strings, defensively."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _truncate(value: str | None, max_len: int = _MAX_STR_LEN) -> str:
    """Truncate a string to max_len while keeping it valid."""
    if value is None:
        return ""
    if len(value) <= max_len:
        return value
    return value[:max_len] + "…[truncated]"


@dataclass
class TraceSummary:
    """A user/developer-provided execution or trace summary.

    All fields are treated as untrusted input.  ``from_dict`` is the
    trusted factory — it drops hidden-inference keys, coerces types,
    and defaults missing fields.
    """

    trace_id: str | None
    user_request: str
    final_result: str | None
    task_type: str | None
    context: str | None
    tools_used: list[str]
    files_touched: list[str]
    commands_run: list[str]
    errors_seen: list[str]
    failed_attempts: list[str]
    successful_steps: list[str]
    verification: list[str]
    user_feedback: str | None
    existing_capability_id: str | None
    created_at: str
    metadata: dict[str, Any]

    def sanitize(self) -> "TraceSummary":
        """Return a new TraceSummary with secrets redacted from all string fields."""
        return TraceSummary(
            trace_id=self.trace_id,
            user_request=_redact_text(self.user_request),
            final_result=_redact_text(self.final_result) if self.final_result else None,
            task_type=self.task_type,
            context=_redact_text(self.context) if self.context else None,
            tools_used=self.tools_used,
            files_touched=self.files_touched,
            commands_run=[_redact_text(c) for c in self.commands_run],
            errors_seen=[_redact_text(e) for e in self.errors_seen],
            failed_attempts=[_redact_text(f) for f in self.failed_attempts],
            successful_steps=self.successful_steps,
            verification=self.verification,
            user_feedback=_redact_text(self.user_feedback) if self.user_feedback else None,
            existing_capability_id=self.existing_capability_id,
            created_at=self.created_at,
            metadata={k: _redact_text(str(v)) for k, v in self.metadata.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "trace_id": self.trace_id,
            "user_request": self.user_request,
            "final_result": self.final_result,
            "task_type": self.task_type,
            "context": self.context,
            "tools_used": self.tools_used,
            "files_touched": self.files_touched,
            "commands_run": self.commands_run,
            "errors_seen": self.errors_seen,
            "failed_attempts": self.failed_attempts,
            "successful_steps": self.successful_steps,
            "verification": self.verification,
            "user_feedback": self.user_feedback,
            "existing_capability_id": self.existing_capability_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceSummary":
        """Trusted factory: parse a dict into a TraceSummary.

        Drops hidden-inference keys (_cot, chain_of_thought, etc.),
        coerces list fields, defaults missing optional fields, and
        truncates overlong strings.
        """
        if not isinstance(d, dict):
            raise ValueError(f"TraceSummary.from_dict expects a dict, got {type(d).__name__}")

        # Drop hidden-inference and prototype-pollution keys.
        sanitized = {k: v for k, v in d.items() if str(k) not in _DROP_KEYS and not str(k).startswith("__")}

        user_request = str(sanitized.get("user_request", ""))
        if not user_request.strip():
            raise ValueError("trace_summary.user_request is required and must be non-empty")

        created_at = str(sanitized.get("created_at", ""))
        if not created_at:
            created_at = datetime.now(timezone.utc).isoformat()

        return cls(
            trace_id=str(sanitized["trace_id"]) if sanitized.get("trace_id") is not None else None,
            user_request=_truncate(user_request),
            final_result=_truncate(str(sanitized["final_result"])) if sanitized.get("final_result") is not None else None,
            task_type=str(sanitized["task_type"]) if sanitized.get("task_type") is not None else None,
            context=_truncate(str(sanitized["context"])) if sanitized.get("context") is not None else None,
            tools_used=_coerce_list(sanitized.get("tools_used")),
            files_touched=_coerce_list(sanitized.get("files_touched")),
            commands_run=_coerce_list(sanitized.get("commands_run")),
            errors_seen=_coerce_list(sanitized.get("errors_seen")),
            failed_attempts=_coerce_list(sanitized.get("failed_attempts")),
            successful_steps=_coerce_list(sanitized.get("successful_steps")),
            verification=_coerce_list(sanitized.get("verification")),
            user_feedback=str(sanitized["user_feedback"]) if sanitized.get("user_feedback") is not None else None,
            existing_capability_id=str(sanitized["existing_capability_id"]) if sanitized.get("existing_capability_id") is not None else None,
            created_at=created_at,
            metadata=sanitized.get("metadata") if isinstance(sanitized.get("metadata"), dict) else {},
        )
