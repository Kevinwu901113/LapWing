"""Smoke test for the production Kernel composition path.

Verifies that the helper used by AppContainer._init_kernel produces a
working Kernel without raising. Does not exercise full e2e (that's V-A3 in
test_resident_operator_e2e.py).
"""
from __future__ import annotations

from pathlib import Path


def test_kernel_can_be_composed(tmp_path: Path):
    """Build a Kernel with the same components production wires (sans the
    legacy BrowserManager — we pass None so the BrowserAdapter has its
    legacy backend as None, mirroring the BROWSER_ENABLED=false path)."""
    from src.lapwing_kernel.adapters.browser import BrowserAdapter
    from src.lapwing_kernel.adapters.credential_use_state import (
        CredentialUseState,
    )
    from src.lapwing_kernel.identity import ResidentIdentity
    from src.lapwing_kernel.kernel import Kernel
    from src.lapwing_kernel.pipeline.registry import ResourceRegistry
    from src.lapwing_kernel.policy import PolicyDecider
    from src.lapwing_kernel.redactor import SecretRedactor
    from src.lapwing_kernel.stores.event_log import EventLog
    from src.lapwing_kernel.stores.interrupt_store import InterruptStore

    db = tmp_path / "kernel.db"
    interrupt_store = InterruptStore(db)
    event_log = EventLog(db)
    use_state = CredentialUseState(db)
    redactor = SecretRedactor()
    policy = PolicyDecider(config={}, use_state=use_state)

    identity = ResidentIdentity(
        agent_name="Lapwing",
        owner_name="Kevin",
        home_server_name="test-host",
        linux_user="test",
        home_dir=tmp_path,
        personal_browser_profile=tmp_path / "personal",
    )

    registry = ResourceRegistry()
    registry.register(
        BrowserAdapter(
            profile="fetch",
            legacy_browser_manager=None,
            interrupt_store=interrupt_store,
            redactor=redactor,
        ),
        profile="fetch",
    )
    registry.register(
        BrowserAdapter(
            profile="personal",
            legacy_browser_manager=None,
            interrupt_store=interrupt_store,
            redactor=redactor,
        ),
        profile="personal",
    )

    kernel = Kernel(
        identity=identity,
        resource_registry=registry,
        interrupt_store=interrupt_store,
        event_log=event_log,
        policy=policy,
        redactor=redactor,
        model_slots=None,
    )

    assert kernel.interrupts is interrupt_store
    assert kernel.events is event_log
    assert kernel.resources.get("browser", profile="fetch") is not None
    assert kernel.resources.get("browser", profile="personal") is not None
