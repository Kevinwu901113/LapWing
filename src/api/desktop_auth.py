"""Shared desktop-token validation for HTTP, SSE, and WebSocket routes."""

from __future__ import annotations

import json
import logging

from config.settings import DESKTOP_AUTH_TOKENS_PATH

logger = logging.getLogger("lapwing.api.desktop_auth")


def validate_desktop_token(token: str | None) -> bool:
    if not token:
        return False
    token = token.strip()
    if not token:
        return False
    try:
        raw = DESKTOP_AUTH_TOKENS_PATH.read_text(encoding="utf-8")
        records = json.loads(raw)
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("desktop token file unreadable: %s", exc)
        return False
    if not isinstance(records, list):
        return False
    return any(isinstance(item, dict) and item.get("token") == token for item in records)
