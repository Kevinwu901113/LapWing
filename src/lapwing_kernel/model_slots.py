"""ModelSlotResolver — tier-list model selection.

Resolves a slot name to an actual model_id by trying tiers in fixed order.

Hard rules (blueprint §10):
  - empty candidates → ConfigError at construct time
  - tier order is fixed; no dynamic performance routing in v1
  - probe results may be cached; order is config-determined
  - fallback transitions emit EventLog model.fallback

See docs/architecture/lapwing_v1_blueprint.md §10.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .primitives.event import Event


class EventLogProtocol(Protocol):
    def append(self, event: Event) -> None: ...


class ConfigError(Exception):
    """Raised at construction time when model_slots config is invalid."""


@dataclass(frozen=True)
class SlotTier:
    name: str
    candidates: list[str]


@dataclass(frozen=True)
class SlotConfig:
    slot_name: str
    tiers: list[SlotTier]
    selection_strategy: str
    capability_requirements: list[str]
    fallback_trigger: list[str]
    empty_candidates_policy: str


# Default trigger reasons that mark a candidate unavailable for the slot's
# in-memory probe cache.
DEFAULT_FALLBACK_TRIGGERS = (
    "timeout",
    "5xx",
    "rate_limit",
    "provider_unavailable",
)


class ModelSlotResolver:
    """Resolves slot name → model_id via tier-list with first-available strategy.

    Probe cache lives in-memory only (blueprint §10.4):
      - process restart resets cache (re-optimistic primary)
      - report_failure(slot, model_id, reason) marks the candidate unavailable
        if reason matches the slot's fallback_trigger list
      - tests can call clear_cache() to reset between tests
    """

    def __init__(
        self,
        slots: dict[str, SlotConfig],
        event_log: EventLogProtocol | None = None,
    ):
        self._slots = slots
        self._event_log = event_log
        # slot_name → {model_id: alive}
        self._probe_cache: dict[str, dict[str, bool]] = {}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "ModelSlotResolver":
        """Construct from a {slot_name: slot_dict} mapping.

        Each slot_dict has:
          tiers: [{name, candidates}, ...]
          selection_strategy: str (default "first_available")
          capability_requirements: [str, ...]  (advisory; not enforced here)
          fallback_trigger: [str, ...]  (default DEFAULT_FALLBACK_TRIGGERS)
          empty_candidates_policy: "config_error" | str  (default config_error)

        Raises:
          ConfigError if any tier has empty candidates and policy is config_error.
        """
        slots: dict[str, SlotConfig] = {}
        for name, slot_cfg in cfg.items():
            raw_tiers = slot_cfg.get("tiers") or []
            policy = slot_cfg.get("empty_candidates_policy", "config_error")
            tiers: list[SlotTier] = []
            for t in raw_tiers:
                candidates = list(t.get("candidates") or [])
                tier = SlotTier(name=t["name"], candidates=candidates)
                if not candidates and policy == "config_error":
                    raise ConfigError(
                        f"Slot {name!r} tier {tier.name!r} has empty "
                        f"candidates; see blueprint §10.1."
                    )
                tiers.append(tier)
            if not tiers and policy == "config_error":
                raise ConfigError(
                    f"Slot {name!r} has no tiers configured; see blueprint §10.1."
                )
            slots[name] = SlotConfig(
                slot_name=name,
                tiers=tiers,
                selection_strategy=slot_cfg.get(
                    "selection_strategy", "first_available"
                ),
                capability_requirements=list(
                    slot_cfg.get("capability_requirements") or []
                ),
                fallback_trigger=list(
                    slot_cfg.get("fallback_trigger") or DEFAULT_FALLBACK_TRIGGERS
                ),
                empty_candidates_policy=policy,
            )
        return cls(slots)

    def resolve(self, slot_name: str) -> str:
        """Return the current selected model_id for slot.

        Walks tiers in fixed order, picking the first candidate not marked
        unavailable in the probe cache.
        """
        if slot_name not in self._slots:
            raise KeyError(f"Slot {slot_name!r} not configured")
        slot = self._slots[slot_name]
        for tier in slot.tiers:
            for candidate in tier.candidates:
                if self._is_alive(slot_name, candidate):
                    return candidate
        raise RuntimeError(f"All tiers exhausted for slot {slot_name!r}")

    def report_failure(self, slot_name: str, model_id: str, reason: str) -> None:
        """LLMRouter / adapter notifies that a model failed in a fallback-triggering way."""
        if slot_name not in self._slots:
            return
        slot = self._slots[slot_name]
        if reason not in slot.fallback_trigger:
            return
        self._probe_cache.setdefault(slot_name, {})[model_id] = False
        if self._event_log is not None:
            self._event_log.append(
                Event.new(
                    actor="system",
                    type="model.fallback",
                    resource=None,
                    summary=(
                        f"slot {slot_name!r} candidate {model_id!r} marked "
                        f"unavailable: {reason}"
                    ),
                )
            )

    def clear_cache(self, slot_name: str | None = None) -> None:
        """Reset probe cache. With no arg, clears all slots."""
        if slot_name is None:
            self._probe_cache.clear()
        else:
            self._probe_cache.pop(slot_name, None)

    def list_slots(self) -> list[str]:
        return sorted(self._slots.keys())

    def get_slot_config(self, slot_name: str) -> SlotConfig:
        return self._slots[slot_name]

    def _is_alive(self, slot_name: str, model_id: str) -> bool:
        return self._probe_cache.get(slot_name, {}).get(model_id, True)
