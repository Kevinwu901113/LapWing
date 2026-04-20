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
