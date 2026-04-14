from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator

from config.settings import AUTH_PROFILES_PATH
from src.auth.models import FailureKind, PURPOSES
from src.core.time_utils import parse_iso_datetime


_COOLDOWN_STEPS = (60, 5 * 60, 25 * 60, 60 * 60)
_BILLING_STEPS = (5 * 60 * 60, 10 * 60 * 60, 20 * 60 * 60, 24 * 60 * 60)


@dataclass(frozen=True)
class ProfileStatus:
    status: str
    reason_code: str | None = None


class AuthStore:
    def __init__(self, path: Path = AUTH_PROFILES_PATH) -> None:
        self.path = path
        self.lock_path = Path(f"{path}.lock")

    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        with self._lock():
            if self.path.exists():
                return
            self._write_locked(self._empty_store())

    def read(self) -> dict[str, Any]:
        self.ensure_exists()
        with self._lock():
            return self._read_locked()

    def mutate(self, mutator: Callable[[dict[str, Any]], Any]) -> Any:
        self.ensure_exists()
        with self._lock():
            data = self._read_locked()
            result = mutator(data)
            self._write_locked(data)
            return result

    def list_profiles(self, provider: str | None = None) -> list[dict[str, Any]]:
        data = self.read()
        items: list[dict[str, Any]] = []
        for profile_id, profile in data["profiles"].items():
            if provider and profile.get("provider") != provider:
                continue
            status = self.profile_status(data, profile_id)
            items.append(
                {
                    "profileId": profile_id,
                    "provider": profile.get("provider", ""),
                    "type": profile.get("type", ""),
                    "expiresAt": profile.get("expiresAt"),
                    "status": status.status,
                    "reasonCode": status.reason_code,
                }
            )
        items.sort(key=lambda item: item["profileId"])
        return items

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        data = self.read()
        profile = data["profiles"].get(profile_id)
        if profile is None:
            return None
        return copy.deepcopy(profile)

    def upsert_profile(self, profile_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        def _mutate(data: dict[str, Any]) -> dict[str, Any]:
            data["profiles"][profile_id] = copy.deepcopy(profile)
            return copy.deepcopy(data["profiles"][profile_id])

        return self.mutate(_mutate)

    def get_binding(self, purpose: str) -> str | None:
        data = self.read()
        return self._normalize_binding(data["bindings"].get(purpose))

    def set_binding(self, purpose: str, profile_id: str) -> str:
        if purpose not in PURPOSES:
            raise ValueError(f"未知 binding purpose: {purpose}")

        def _mutate(data: dict[str, Any]) -> str:
            if profile_id not in data["profiles"]:
                raise ValueError(f"auth profile 不存在: {profile_id}")
            data["bindings"][purpose] = profile_id
            return profile_id

        return self.mutate(_mutate)

    def clear_binding(self, purpose: str) -> bool:
        if purpose not in PURPOSES:
            raise ValueError(f"未知 binding purpose: {purpose}")

        def _mutate(data: dict[str, Any]) -> bool:
            if purpose not in data["bindings"]:
                return False
            data["bindings"].pop(purpose, None)
            return True

        return self.mutate(_mutate)

    def ordered_profiles(
        self,
        provider: str,
        *,
        preferred_profile_id: str | None = None,
        include_unavailable: bool = True,
    ) -> list[str]:
        data = self.read()
        candidates = [
            profile_id
            for profile_id, profile in data["profiles"].items()
            if profile.get("provider") == provider
        ]
        explicit_order = list(data["order"].get(provider) or [])

        def _sort_key(profile_id: str) -> tuple[int, int, int, int, str]:
            usage = data["usageStats"].get(profile_id) or {}
            status = self.profile_status(data, profile_id)
            profile = data["profiles"].get(profile_id) or {}
            explicit_index = explicit_order.index(profile_id) if profile_id in explicit_order else len(explicit_order) + 1
            type_rank = 0 if profile.get("type") == "oauth" else 1
            unavailable_rank = 1 if status.status in {"cooldown", "disabled"} else 0
            expiry = usage.get("cooldownUntil") or usage.get("disabledUntil") or 0
            last_used = usage.get("lastUsedAt") or 0
            return (explicit_index, unavailable_rank, type_rank, last_used if status.status != "cooldown" else expiry, profile_id)

        ordered = sorted(candidates, key=_sort_key)
        if preferred_profile_id and preferred_profile_id in ordered:
            ordered.remove(preferred_profile_id)
            ordered.insert(0, preferred_profile_id)

        if include_unavailable:
            return ordered
        return [profile_id for profile_id in ordered if self.profile_status(data, profile_id).status == "active"]

    def mark_success(self, profile_id: str) -> None:
        now_ms = _now_ms()

        def _mutate(data: dict[str, Any]) -> None:
            usage = data["usageStats"].setdefault(profile_id, {})
            usage["lastUsedAt"] = now_ms
            usage["errorCount"] = 0
            usage.pop("cooldownUntil", None)
            usage.pop("disabledUntil", None)
            usage.pop("disabledReason", None)

        self.mutate(_mutate)

    def mark_failure(self, profile_id: str, kind: FailureKind) -> None:
        if kind == "other":
            return

        now_ms = _now_ms()

        def _mutate(data: dict[str, Any]) -> None:
            usage = data["usageStats"].setdefault(profile_id, {})
            error_count = int(usage.get("errorCount") or 0) + 1
            usage["errorCount"] = error_count
            if kind == "billing":
                backoff = _BILLING_STEPS[min(error_count - 1, len(_BILLING_STEPS) - 1)]
                usage["disabledUntil"] = now_ms + backoff * 1000
                usage["disabledReason"] = "billing"
                return
            if kind in {"auth", "rate_limit", "timeout"}:
                backoff = _COOLDOWN_STEPS[min(error_count - 1, len(_COOLDOWN_STEPS) - 1)]
                usage["cooldownUntil"] = now_ms + backoff * 1000

        self.mutate(_mutate)

    def profile_status(self, data: dict[str, Any], profile_id: str) -> ProfileStatus:
        profile = data["profiles"].get(profile_id)
        if profile is None:
            return ProfileStatus(status="missing", reason_code="missing")

        usage = data["usageStats"].get(profile_id) or {}
        now_ms = _now_ms()
        disabled_until = _coerce_int(usage.get("disabledUntil"))
        if disabled_until and disabled_until > now_ms:
            return ProfileStatus(status="disabled", reason_code=str(usage.get("disabledReason") or "disabled"))

        cooldown_until = _coerce_int(usage.get("cooldownUntil"))
        if cooldown_until and cooldown_until > now_ms:
            return ProfileStatus(status="cooldown", reason_code="cooldown")

        expires_at = profile.get("expiresAt")
        if profile.get("type") == "oauth" and _is_expired(expires_at):
            return ProfileStatus(status="expired", reason_code="expired")

        return ProfileStatus(status="active", reason_code=None)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_locked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_store()
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return self._empty_store()
        data = json.loads(raw)
        return self._normalize_store(data)

    def _write_locked(self, data: dict[str, Any]) -> None:
        normalized = self._normalize_store(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        tmp_path.write_text(payload, encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.path)
        os.chmod(self.path, 0o600)

    def _empty_store(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profiles": {},
            "bindings": {},
            "order": {},
            "usageStats": {},
            "meta": {
                "unsupported": {
                    "multiHostSharedCache": True,
                }
            },
        }

    def _normalize_store(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = self._empty_store()
        normalized["version"] = int(data.get("version") or 1)
        normalized["profiles"] = dict(data.get("profiles") or {})
        normalized["bindings"] = {
            purpose: profile_id
            for purpose, profile_id in dict(data.get("bindings") or {}).items()
            if purpose in PURPOSES and isinstance(profile_id, str) and profile_id.strip()
        }
        normalized["order"] = {
            str(provider): [str(item) for item in value if isinstance(item, str) and item.strip()]
            for provider, value in dict(data.get("order") or {}).items()
            if isinstance(value, list)
        }
        normalized["usageStats"] = {
            str(profile_id): dict(value)
            for profile_id, value in dict(data.get("usageStats") or {}).items()
            if isinstance(value, dict)
        }
        normalized["meta"] = dict(data.get("meta") or {})
        return normalized

    @staticmethod
    def _normalize_binding(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)




def _is_expired(value: Any) -> bool:
    dt = parse_iso_datetime(value)
    if dt is None:
        return False
    return dt <= datetime.now(timezone.utc)
