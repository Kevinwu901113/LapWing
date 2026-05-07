from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from src.adapters.base import NormalizedInboundMessage


@dataclass(frozen=True, slots=True)
class IngressEnvelope:
    event_id: str
    idempotency_key: str
    message: NormalizedInboundMessage


class IngressNormalizer:
    """Assign stable ingress identifiers before EventQueue handoff."""

    def normalize(self, message: NormalizedInboundMessage) -> IngressEnvelope:
        event_id = f"evt_{uuid.uuid4().hex}"
        raw = "|".join([
            message.channel,
            message.chat_id,
            message.user_id,
            str(message.message_id),
            _normal_text(message.text),
        ])
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return IngressEnvelope(
            event_id=event_id,
            idempotency_key=f"ingress:{key}",
            message=message,
        )


def _normal_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())
