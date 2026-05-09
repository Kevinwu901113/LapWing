"""Deterministic topic/intent helpers for background task lineage."""

from __future__ import annotations

import re


_KNOWN_LOCATION_SLUGS = {
    "广州大学城": "guangzhou-university-city",
    "廣州大學城": "guangzhou-university-city",
    "大学城": "guangzhou-university-city",
    "大學城": "guangzhou-university-city",
    "广州": "guangzhou",
    "廣州": "guangzhou",
}


def normalize_topic_component(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "unspecified"
    for needle, slug in _KNOWN_LOCATION_SLUGS.items():
        if needle in value:
            return slug
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unspecified"


def looks_like_weather_request(text: str) -> bool:
    lower = (text or "").lower()
    return "天气" in lower or "weather" in lower


def infer_topic_key(
    *,
    text: str,
    chat_id: str = "",
    user_id: str = "",
    category: str | None = None,
) -> tuple[str | None, str | None]:
    if category == "weather" or looks_like_weather_request(text):
        for needle, slug in _KNOWN_LOCATION_SLUGS.items():
            if needle in text:
                key = f"weather:{slug}"
                return key, key
        owner = normalize_topic_component(user_id or chat_id or "chat_user")
        key = f"weather:{owner}:unspecified"
        return key, key
    if category:
        key = f"external_info:{normalize_topic_component(category)}:{normalize_topic_component(text)[:80]}"
        return key, key
    return None, None


def is_stop_request_for_weather(text: str) -> bool:
    lower = (text or "").lower()
    stop_words = ("停止", "别查", "不要查", "取消", "stop", "cancel")
    weather_words = ("天气", "weather")
    return any(word in lower for word in stop_words) and any(word in lower for word in weather_words)
