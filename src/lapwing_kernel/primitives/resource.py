"""Resource Protocol — anything that produces side-effects.

See docs/architecture/lapwing_v1_blueprint.md §3.5.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .action import Action
from .observation import Observation


@runtime_checkable
class Resource(Protocol):
    """Anything that produces side-effects or talks to the outside world.

    NOT a Resource (these are fact sources, not side-effecting):
      - TrajectoryStore
      - Wiki
      - EventLog
      - CredentialVault (lower-level secret store; accessed via CredentialAdapter)

    Profile is an adapter construction parameter, not a Protocol field.
    BrowserAdapter(profile="fetch") and BrowserAdapter(profile="personal") share
    `name="browser"` but register under different (name, profile) keys in
    ResourceRegistry.
    """

    name: str

    async def execute(self, action: Action) -> Observation: ...

    def supports(self, verb: str) -> bool: ...
