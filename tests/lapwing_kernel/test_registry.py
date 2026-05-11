"""ResourceRegistry tests — (name, profile) keyed registration."""
from __future__ import annotations

import pytest

from src.lapwing_kernel.pipeline.registry import ResourceRegistry
from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.observation import Observation


class FakeResource:
    """Minimal Resource Protocol impl for registry testing."""

    def __init__(self, name: str):
        self.name = name

    def supports(self, verb: str) -> bool:
        return True

    async def execute(self, action: Action) -> Observation:
        return Observation.ok(action.id, self.name)


def test_register_and_get():
    reg = ResourceRegistry()
    r = FakeResource("browser")
    reg.register(r, profile="fetch")
    assert reg.get("browser", profile="fetch") is r


def test_same_name_different_profiles_coexist():
    """BrowserAdapter(profile=fetch) and (profile=personal) must register
    under the same name but different keys (blueprint §3.5 / §4.2)."""
    reg = ResourceRegistry()
    fetch = FakeResource("browser")
    personal = FakeResource("browser")
    reg.register(fetch, profile="fetch")
    reg.register(personal, profile="personal")
    assert reg.get("browser", profile="fetch") is fetch
    assert reg.get("browser", profile="personal") is personal


def test_duplicate_registration_rejected():
    reg = ResourceRegistry()
    reg.register(FakeResource("browser"), profile="fetch")
    with pytest.raises(ValueError, match="already registered"):
        reg.register(FakeResource("browser"), profile="fetch")


def test_missing_lookup_raises_keyerror():
    reg = ResourceRegistry()
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_list_names_dedups_profiles():
    reg = ResourceRegistry()
    reg.register(FakeResource("browser"), profile="fetch")
    reg.register(FakeResource("browser"), profile="personal")
    reg.register(FakeResource("credential"))
    assert reg.list_names() == ["browser", "credential"]


def test_none_profile_distinct_from_named_profile():
    reg = ResourceRegistry()
    no_profile = FakeResource("credential")
    reg.register(no_profile)
    with pytest.raises(KeyError):
        reg.get("credential", profile="fetch")
    assert reg.get("credential") is no_profile
