"""安全检查：扫描技能代码和文档中的危险操作。"""

import re

_DANGEROUS_CALLS = [
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bos\.exec[lv]p?e?\b",
    r"\bsubprocess\.(?:Popen|call|run|check_call|check_output)\b",
    r"\b(?<!#\s)eval\s*\(",
    r"\b(?<!#\s)exec\s*\(",
    r"\bcompile\s*\(",
    r"\b__import__\s*\(",
    r"\bimportlib\.import_module\b",
    r"\bgetattr\s*\(\s*__builtins__",
    r"\bbuiltins\.(?:eval|exec|compile)\b",
    r"\bpickle\.(?:loads?|Unpickler)\b",
    r"\bmarshal\.loads?\b",
    r"\bshutil\.rmtree\b",
    r"\bctypes\b",
    r"\bsocket\.socket\b",
    r"\burllib\.request\.urlopen\b",
]

_DANGEROUS_FILE_PATTERNS = [
    r"open\s*\(\s*['\"](?:/etc|/usr|/bin|/sbin|/boot|/sys|/proc|/dev|/var)",
    r"open\s*\(\s*['\"](?:.*\.(?:pem|key|crt|env|ssh|shadow|passwd))",
    r"open\s*\(\s*['\"](?:data/identity|data/memory|config/|prompts/|src/)",
]

_DANGEROUS_MD_PATTERNS = [
    r"(?:modify|edit|write|delete|remove|overwrite)\s+(?:/etc|/usr|/bin|/sys|/proc|system\s+file)",
    r"(?:sudo|chmod\s+777|rm\s+-rf\s+/)",
]

_COMPILED_CODE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_CALLS + _DANGEROUS_FILE_PATTERNS]
_COMPILED_MD = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_MD_PATTERNS]


def check_skill_safety(content: str, *, check_markdown: bool = False) -> dict:
    """Return {"safe": bool, "reason": str}."""
    patterns = _COMPILED_MD if check_markdown else _COMPILED_CODE
    for pattern in patterns:
        match = pattern.search(content)
        if match:
            return {"safe": False, "reason": f"检测到危险模式: {match.group()}"}
    return {"safe": True, "reason": ""}
