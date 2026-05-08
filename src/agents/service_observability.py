"""Small logging helpers for agent runtime service invariants."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.agents.researcher import Researcher


def required_service_presence(
    services: Mapping[str, Any] | None,
    required: tuple[str, ...] | None = None,
) -> dict[str, bool]:
    raw = services or {}
    return {
        key: raw.get(key) is not None
        for key in (required or Researcher.REQUIRED_SERVICES)
    }


def log_required_service_presence(
    logger: logging.Logger,
    boundary: str,
    services: Mapping[str, Any] | None,
    required: tuple[str, ...] | None = None,
) -> list[str]:
    presence = required_service_presence(services, required)
    missing = [key for key, present in presence.items() if not present]
    if missing:
        logger.warning(
            "agent required services incomplete at %s: missing=%s presence=%s",
            boundary,
            missing,
            presence,
        )
    else:
        logger.info(
            "agent required services present at %s: presence=%s",
            boundary,
            presence,
        )
    return missing
