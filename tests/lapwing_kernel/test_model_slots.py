"""ModelSlotResolver tests — tier-list resolution + fail-fast config + fallback.

Covers blueprint §10.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.lapwing_kernel.model_slots import (
    ConfigError,
    ModelSlotResolver,
    SlotConfig,
    SlotTier,
)
from src.lapwing_kernel.primitives.event import Event


REPO_ROOT = Path(__file__).resolve().parents[2]


class MockEventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def append(self, event: Event) -> None:
        self.events.append(event)


# ── from_config + fail-fast ─────────────────────────────────────────────────


class TestConfigConstruction:
    def test_basic_two_tier(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        assert r.list_slots() == ["main"]
        slot = r.get_slot_config("main")
        assert len(slot.tiers) == 2
        assert slot.tiers[0].candidates == ["model-a"]

    def test_empty_candidates_raises_config_error(self):
        with pytest.raises(ConfigError, match="empty candidates"):
            ModelSlotResolver.from_config(
                {"main": {"tiers": [{"name": "primary", "candidates": []}]}}
            )

    def test_no_tiers_raises_config_error(self):
        with pytest.raises(ConfigError, match="no tiers configured"):
            ModelSlotResolver.from_config({"main": {"tiers": []}})

    def test_empty_candidates_with_policy_override_allowed(self):
        """A slot can opt out of config_error policy and accept empty tiers."""
        r = ModelSlotResolver.from_config(
            {
                "browser_vision": {
                    "tiers": [{"name": "primary", "candidates": []}],
                    "empty_candidates_policy": "allow_empty",
                }
            }
        )
        assert "browser_vision" in r.list_slots()

    def test_default_strategy_first_available(self):
        r = ModelSlotResolver.from_config(
            {"main": {"tiers": [{"name": "primary", "candidates": ["m"]}]}}
        )
        assert r.get_slot_config("main").selection_strategy == "first_available"

    def test_default_fallback_triggers(self):
        r = ModelSlotResolver.from_config(
            {"main": {"tiers": [{"name": "primary", "candidates": ["m"]}]}}
        )
        triggers = r.get_slot_config("main").fallback_trigger
        for expected in ("timeout", "5xx", "rate_limit", "provider_unavailable"):
            assert expected in triggers


# ── resolve() — tier walk in fixed order ────────────────────────────────────


class TestResolve:
    def test_returns_primary_when_alive(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        assert r.resolve("main") == "model-a"

    def test_returns_fallback_after_primary_failure(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        r.report_failure("main", "model-a", "timeout")
        assert r.resolve("main") == "model-b"

    def test_unknown_slot_raises_keyerror(self):
        r = ModelSlotResolver.from_config(
            {"main": {"tiers": [{"name": "primary", "candidates": ["m"]}]}}
        )
        with pytest.raises(KeyError):
            r.resolve("nonexistent")

    def test_all_tiers_exhausted_raises(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        r.report_failure("main", "model-a", "timeout")
        r.report_failure("main", "model-b", "timeout")
        with pytest.raises(RuntimeError, match="All tiers exhausted"):
            r.resolve("main")

    def test_fixed_order_no_dynamic_routing(self):
        """Blueprint §10: tier order is fixed. Even after many lookups
        primary is tried first."""
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        for _ in range(50):
            assert r.resolve("main") == "model-a"

    def test_multiple_candidates_in_tier(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a", "model-b"]},
                    ]
                }
            }
        )
        assert r.resolve("main") == "model-a"
        r.report_failure("main", "model-a", "timeout")
        assert r.resolve("main") == "model-b"


# ── report_failure — fallback_trigger filtering ─────────────────────────────


class TestFallbackTrigger:
    def test_failure_outside_trigger_list_ignored(self):
        """Only reasons in fallback_trigger mark a candidate unavailable."""
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ],
                    "fallback_trigger": ["timeout"],
                }
            }
        )
        r.report_failure("main", "model-a", "user_cancelled")
        assert r.resolve("main") == "model-a"

    def test_failure_in_trigger_list_marks_unavailable(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ],
                    "fallback_trigger": ["timeout"],
                }
            }
        )
        r.report_failure("main", "model-a", "timeout")
        assert r.resolve("main") == "model-b"

    def test_failure_emits_model_fallback_event(self):
        log = MockEventLog()
        slots = {
            "main": SlotConfig(
                slot_name="main",
                tiers=[
                    SlotTier("primary", ["model-a"]),
                    SlotTier("fallback", ["model-b"]),
                ],
                selection_strategy="first_available",
                capability_requirements=[],
                fallback_trigger=["timeout"],
                empty_candidates_policy="config_error",
            )
        }
        r = ModelSlotResolver(slots, event_log=log)
        r.report_failure("main", "model-a", "timeout")
        events = [e for e in log.events if e.type == "model.fallback"]
        assert len(events) == 1
        assert "model-a" in events[0].summary
        assert "timeout" in events[0].summary

    def test_unknown_slot_failure_noop(self):
        r = ModelSlotResolver.from_config(
            {"main": {"tiers": [{"name": "primary", "candidates": ["model-a"]}]}}
        )
        r.report_failure("nonexistent", "model-a", "timeout")  # must not raise


# ── cache management ────────────────────────────────────────────────────────


class TestCache:
    def test_clear_cache_restores_primary(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        r.report_failure("main", "model-a", "timeout")
        assert r.resolve("main") == "model-b"
        r.clear_cache("main")
        assert r.resolve("main") == "model-a"

    def test_clear_all(self):
        r = ModelSlotResolver.from_config(
            {
                "main": {
                    "tiers": [
                        {"name": "primary", "candidates": ["model-a"]},
                        {"name": "fallback", "candidates": ["model-b"]},
                    ]
                }
            }
        )
        r.report_failure("main", "model-a", "timeout")
        r.clear_cache()
        assert r.resolve("main") == "model-a"


# ── live config file validates against schema ───────────────────────────────


class TestCanonicalExampleSchema:
    """The canonical model_slots schema example under docs/architecture/
    must parse without ConfigError. This is the §10.3 startup-validation
    surrogate that is versionable across machines (data/config/ is
    gitignored as it contains per-developer API key state)."""

    EXAMPLE_PATH = REPO_ROOT / "docs" / "architecture" / "model_slots_example.json"

    def test_canonical_example_parses(self):
        if not self.EXAMPLE_PATH.exists():
            pytest.skip(f"{self.EXAMPLE_PATH} not present in this checkout")
        cfg = json.loads(self.EXAMPLE_PATH.read_text())
        slots_block = {k: v for k, v in cfg.items() if not k.startswith("_")}
        resolver = ModelSlotResolver.from_config(slots_block)
        assert "main_conversation" in resolver.list_slots()

    def test_canonical_example_main_conversation_resolves(self):
        if not self.EXAMPLE_PATH.exists():
            pytest.skip(f"{self.EXAMPLE_PATH} not present in this checkout")
        cfg = json.loads(self.EXAMPLE_PATH.read_text())
        slots_block = {k: v for k, v in cfg.items() if not k.startswith("_")}
        resolver = ModelSlotResolver.from_config(slots_block)
        resolved = resolver.resolve("main_conversation")
        assert resolved
        assert isinstance(resolved, str)


class TestLiveConfigFile:
    """If the developer's local data/config/model_routing.json has a
    model_slots block, it should also parse without ConfigError. Skipped
    when the file or block is absent (e.g. fresh checkout, CI)."""

    LOCAL_PATH = REPO_ROOT / "data" / "config" / "model_routing.json"

    def test_local_model_slots_block_valid_if_present(self):
        if not self.LOCAL_PATH.exists():
            pytest.skip("data/config/model_routing.json not present (gitignored)")
        cfg = json.loads(self.LOCAL_PATH.read_text())
        slots_block = cfg.get("model_slots")
        if slots_block is None:
            pytest.skip("model_slots block not yet in local config")
        slots_block = {k: v for k, v in slots_block.items() if not k.startswith("_")}
        resolver = ModelSlotResolver.from_config(slots_block)
        assert "main_conversation" in resolver.list_slots()
