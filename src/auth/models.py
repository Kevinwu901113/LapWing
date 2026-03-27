from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PurposeName = Literal["default", "chat", "tool", "heartbeat"]
ProfileType = Literal["oauth", "api_key"]
SecretRefKind = Literal["literal", "env", "command"]
FailureKind = Literal["auth", "rate_limit", "timeout", "billing", "other"]

PURPOSES: tuple[PurposeName, ...] = ("default", "chat", "tool", "heartbeat")


@dataclass(frozen=True)
class SecretRef:
    kind: SecretRefKind
    value: str


@dataclass(frozen=True)
class PurposeConfig:
    purpose: str
    base_url: str
    model: str
    api_type: str
    source: str
    provider: str | None = None


@dataclass(frozen=True)
class ResolvedAuthCandidate:
    purpose: str
    base_url: str
    model: str
    api_type: str
    auth_value: str
    auth_kind: str
    source: str
    provider: str | None = None
    profile_id: str | None = None
    profile_type: str | None = None
    binding_purpose: str | None = None
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthRouteContext:
    purpose: str
    session_key: str | None = None
    allow_failover: bool = True
    origin: str | None = None


@dataclass(frozen=True)
class RefreshedProfile:
    profile_id: str
    profile: dict[str, Any]


@dataclass(frozen=True)
class ApiSession:
    token: str
    expires_at: float
