"""ResidentIdentity — kernel's immutable identity facts.

See docs/architecture/lapwing_v1_blueprint.md §3.6.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResidentIdentity:
    """Immutable identity facts read at kernel start.

    NOT Lapwing's "self-representation" — just the boot-time facts the kernel
    needs to wire its side of the world.
    """

    agent_name: str
    owner_name: str
    home_server_name: str
    linux_user: str
    home_dir: Path
    personal_browser_profile: Path
    email_address: str | None = None
    phone_number_ref: str | None = None
