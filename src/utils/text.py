"""Shared text utilities."""

import json
import re
from typing import Any


def strip_json_fence(text: str) -> str:
    """Remove ```json ... ``` fences from LLM output, return inner content."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def parse_llm_json(text: str, fallback: Any = None) -> Any:
    """Strip code fences then parse JSON. Return fallback on failure."""
    try:
        return json.loads(strip_json_fence(text))
    except (json.JSONDecodeError, TypeError):
        return fallback


def truncate(text: str, max_len: int, suffix: str = "...") -> str:
    """Truncate text to max_len characters, appending suffix if truncated."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix
