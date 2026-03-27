"""清洗回复中的内部思考标签（code-aware）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_THINK_TAG_QUICK_RE = re.compile(
    r"<\s*/?\s*(?:think(?:ing)?|thought|antthinking)\b",
    flags=re.IGNORECASE,
)
_THINK_TAG_RE = re.compile(
    r"<\s*(/?)\s*(?:think(?:ing)?|thought|antthinking)\b[^<>]*>",
    flags=re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(
    r"(^|\n)(```|~~~)[^\n]*\n[\s\S]*?(?:\n\2(?:\n|$)|$)"
)
_INLINE_CODE_RE = re.compile(r"`+[^`]+`+")


@dataclass(frozen=True)
class CodeRegion:
    start: int
    end: int


def _find_code_regions(text: str) -> list[CodeRegion]:
    regions: list[CodeRegion] = []

    for match in _FENCED_CODE_RE.finditer(text):
        prefix = match.group(1) or ""
        start = match.start() + len(prefix)
        end = start + len(match.group(0)) - len(prefix)
        regions.append(CodeRegion(start=start, end=end))

    for match in _INLINE_CODE_RE.finditer(text):
        start = match.start()
        end = match.end()
        inside_fenced = any(start >= item.start and end <= item.end for item in regions)
        if not inside_fenced:
            regions.append(CodeRegion(start=start, end=end))

    regions.sort(key=lambda item: item.start)
    return regions


def _is_inside_code(pos: int, regions: list[CodeRegion]) -> bool:
    return any(item.start <= pos < item.end for item in regions)


def strip_internal_thinking_tags(text: str) -> str:
    """移除普通文本中的思考标签与内容，保留代码区域中的标签示例。"""
    if not text:
        return text
    if not _THINK_TAG_QUICK_RE.search(text):
        return text

    code_regions = _find_code_regions(text)
    parts: list[str] = []
    last_index = 0
    in_thinking = False

    for match in _THINK_TAG_RE.finditer(text):
        index = match.start()
        if _is_inside_code(index, code_regions):
            continue

        is_close = match.group(1) == "/"
        if not in_thinking:
            parts.append(text[last_index:index])
            if not is_close:
                in_thinking = True
        elif is_close:
            in_thinking = False

        last_index = match.end()

    # strict 防泄露：未闭合的 <think> 之后内容全部丢弃。
    if not in_thinking:
        parts.append(text[last_index:])

    return "".join(parts)
