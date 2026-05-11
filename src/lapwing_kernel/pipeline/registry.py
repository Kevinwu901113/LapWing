"""ResourceRegistry — (name, profile) → Resource lookup.

See docs/architecture/lapwing_v1_blueprint.md §4.2.
"""
from __future__ import annotations

from ..primitives.resource import Resource


class ResourceRegistry:
    """Keys: (resource.name, profile_or_None) → Resource instance.

    BrowserAdapter(profile="fetch") and BrowserAdapter(profile="personal") share
    the same `name="browser"` but register under different keys.
    """

    def __init__(self) -> None:
        self._resources: dict[tuple[str, str | None], Resource] = {}

    def register(self, resource: Resource, *, profile: str | None = None) -> None:
        key = (resource.name, profile)
        if key in self._resources:
            raise ValueError(f"Resource {key} already registered")
        self._resources[key] = resource

    def get(self, name: str, profile: str | None = None) -> Resource:
        key = (name, profile)
        if key not in self._resources:
            raise KeyError(f"Resource {key} not registered")
        return self._resources[key]

    def list_names(self) -> list[str]:
        return sorted({name for (name, _) in self._resources.keys()})
