"""环境变量白名单清洗 + 执行输出凭证遮蔽。"""

from __future__ import annotations

import re

_ENV_WHITELIST_PREFIXES = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_", "TZ", "TERM",
    "PYTHON", "VIRTUAL_ENV",
    "TMPDIR", "TEMP", "TMP",
    "COLUMNS", "LINES",
    "XDG_",
)

_ENV_WHITELIST_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "TZ", "TERM", "TMPDIR", "TEMP", "TMP",
    "VIRTUAL_ENV", "COLUMNS", "LINES",
    "SHLVL", "PWD", "OLDPWD", "HOSTNAME",
})

_NETWORK_VARS = frozenset({
    "http_proxy", "HTTP_PROXY",
    "https_proxy", "HTTPS_PROXY",
    "no_proxy", "NO_PROXY",
    "ALL_PROXY", "all_proxy",
})

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)passwd"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)auth"),
    re.compile(r"(?i)private[_-]?key"),
)


def _is_safe_var(name: str) -> bool:
    if name in _ENV_WHITELIST_EXACT:
        return True
    return any(name.startswith(p) for p in _ENV_WHITELIST_PREFIXES)


def _looks_like_credential(name: str) -> bool:
    return any(p.search(name) for p in _CREDENTIAL_PATTERNS)


def sanitize_env(
    env: dict[str, str] | None,
    *,
    allow_network: bool = False,
) -> dict[str, str]:
    """Return a copy of *env* with only safe variables.

    Whitelist + credential-pattern double-check:
    even if a var passes the whitelist, it is dropped if the name
    matches a credential pattern (belt-and-suspenders).
    """
    if env is None:
        return {}
    result: dict[str, str] = {}
    for name, value in env.items():
        if name in _NETWORK_VARS:
            if allow_network:
                result[name] = value
            continue
        if not _is_safe_var(name):
            continue
        if _looks_like_credential(name):
            continue
        result[name] = value
    return result


_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED:github_pat]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github_pat]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_key]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED:jwt]"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----"), "[REDACTED:private_key]"),
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"), "[REDACTED:api_key]"),
    (re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"), "[REDACTED:nvapi_key]"),
    (re.compile(r"tvly-[A-Za-z0-9]{20,}"), "[REDACTED:tavily_key]"),
    (re.compile(r"\bxox[abp]-[A-Za-z0-9-]{20,}\b"), "[REDACTED:slack_token]"),
    (re.compile(r"(?i)(?:bearer|token|authorization)[:\s]+[A-Za-z0-9_\-\.]{40,}"), "[REDACTED:bearer]"),
]


def redact_secrets(text: str) -> str:
    """Scan text for known secret patterns and replace with [REDACTED]."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def truncate_head_tail(
    text: str,
    max_chars: int,
    *,
    head_ratio: float = 0.3,
) -> str:
    """Truncate keeping head and tail, with tail-bias.

    Args:
        text: The text to truncate.
        max_chars: Maximum character count.
        head_ratio: Fraction of max_chars for the head portion (default 0.3).
                    Remaining goes to tail.
    """
    if not text or len(text) <= max_chars:
        return text
    head_chars = int(max_chars * head_ratio)
    tail_chars = max_chars - head_chars
    omitted = len(text) - head_chars - tail_chars
    marker = f"\n\n... [{omitted} chars truncated] ...\n\n"
    return text[:head_chars] + marker + text[-tail_chars:]
