"""V-A3: full resident_operator → kernel.execute → interrupt → approve → resume.

Post-v1 A §5 V-A3 acceptance test. Drives a real ResidentOperator worker
through the production composition path (AgentFactory.create from a
builtin spec) with a fake CaptchaToggleBrowserManager that simulates the
first-visit-CAPTCHA-then-OK pattern. Verifies:

  - delegate path: AgentCatalog → AgentFactory._create_builtin →
    ResidentOperator instance with services["kernel"] populated
  - kernel.execute is reached by the worker (not bypassed via direct
    BrowserManager call)
  - first call produces Observation(status="captcha_required",
    interrupt_id=...) with a pending Interrupt in InterruptStore
  - worker awaits ContinuationRegistry.wait_for_resume on the
    continuation_ref
  - POST /api/v2/interrupts/{id}/approve releases the worker sub-second
  - worker retries the same Action; second pass returns Observation(ok)
  - EventLog records the canonical sequence
  - ContinuationRegistry is empty after the worker's finally block

Mirrors the §15.1 closed-loop test but invokes via the ResidentOperator
worker class instead of an inline coroutine, exercising the §2.1/§2.2
wiring that Post-v1 A introduces.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents.builtin_specs import builtin_resident_operator_spec
from src.agents.factory import AgentFactory
from src.agents.types import AgentMessage
from src.api.routes import interrupts as interrupts_route
from src.lapwing_kernel.adapters.browser import BrowserAdapter
from src.lapwing_kernel.adapters.credential_lease_store import CredentialLeaseStore
from src.lapwing_kernel.identity import ResidentIdentity
from src.lapwing_kernel.kernel import Kernel
from src.lapwing_kernel.pipeline.continuation_registry import (
    ContinuationRegistry,
)
from src.lapwing_kernel.pipeline.registry import ResourceRegistry
from src.lapwing_kernel.policy import PolicyDecider
from src.lapwing_kernel.redactor import SecretRedactor
from src.lapwing_kernel.stores.event_log import EventLog
from src.lapwing_kernel.stores.interrupt_store import InterruptStore


# ── fake BrowserManager: first-visit CAPTCHA, OK after resolve ───────────────


@dataclass
class _FakePageState:
    url: str
    title: str
    elements: list
    text_summary: str
    tab_id: str = "tab-1"


class CaptchaToggleBrowserManager:
    CAPTCHA_TEXT = "please complete the captcha to continue verify you are human"
    REAL_TEXT = "Welcome — protected content"

    def __init__(self):
        self._solved: set[str] = set()
        self.navigate_calls = 0

    def resolve_captcha(self, url: str) -> None:
        self._solved.add(url)

    async def navigate(self, url: str, tab_id: str | None = None):
        self.navigate_calls += 1
        if url in self._solved:
            return _FakePageState(
                url=url,
                title="Protected Page",
                elements=[],
                text_summary=self.REAL_TEXT,
            )
        return _FakePageState(
            url=url,
            title="Verify You Are Human",
            elements=[],
            text_summary=self.CAPTCHA_TEXT,
        )

    async def get_page_text(self, tab_id=None):
        return None


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_singletons():
    ContinuationRegistry.reset_for_tests()
    CredentialLeaseStore.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()
    CredentialLeaseStore.reset_for_tests()


def _build_kernel(tmp_path: Path, browser_mgr: CaptchaToggleBrowserManager):
    db_path = tmp_path / "kernel.db"
    store = InterruptStore(db_path)
    events = EventLog(db_path)
    policy = PolicyDecider(config={})

    registry = ResourceRegistry()
    registry.register(
        BrowserAdapter(
            profile="personal",
            legacy_browser_manager=browser_mgr,
            interrupt_store=store,
        ),
        profile="personal",
    )

    identity = ResidentIdentity(
        agent_name="Lapwing",
        owner_name="Kevin",
        home_server_name="test",
        linux_user="test",
        home_dir=tmp_path,
        personal_browser_profile=tmp_path / "personal",
    )
    kernel = Kernel(
        identity=identity,
        resource_registry=registry,
        interrupt_store=store,
        event_log=events,
        policy=policy,
        redactor=SecretRedactor(),
        model_slots=None,
    )
    return kernel, store, events


def _build_app(kernel: Kernel) -> FastAPI:
    app = FastAPI()
    interrupts_route.init(interrupt_store=kernel.interrupts, kernel=kernel)
    app.include_router(interrupts_route.router)
    return app


def _build_resident_operator(kernel: Kernel):
    """Drive the production AgentFactory path: builtin spec → factory.create
    with services_override containing the kernel."""
    spec = builtin_resident_operator_spec()
    factory = AgentFactory(
        llm_router=None,  # ResidentOperator.execute doesn't use the router
        tool_registry=None,
        mutation_log=None,
    )
    return factory.create(spec, services_override={"kernel": kernel})


# ── V-A3 ─────────────────────────────────────────────────────────────────────


async def test_resident_operator_produces_observation_interrupt_eventlog(
    tmp_path: Path,
):
    """V-A3 canonical flow — see module docstring."""
    URL = "https://test.local/protected"
    browser_mgr = CaptchaToggleBrowserManager()
    kernel, store, events = _build_kernel(tmp_path, browser_mgr)
    app = _build_app(kernel)
    client = TestClient(app)

    worker = _build_resident_operator(kernel)

    # Start the worker via the public Agent execute path. The task is
    # 'navigate <url>' — the deterministic mini-format Post-v1 A §2.2
    # specifies as the v1 dispatch shape.
    message = AgentMessage(
        from_agent="lapwing",
        to_agent="resident_operator",
        task_id="task-1",
        content=f"navigate {URL}",
        context_digest="",
        message_type="request",
    )
    worker_task = asyncio.create_task(worker.execute(message))

    # Wait for the worker to reach the interrupt suspension point.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if store.list_pending(actor="owner"):
            break

    pending_via_api = client.get("/api/v2/interrupts/pending").json()
    assert len(pending_via_api) == 1, (
        f"expected one pending interrupt, got {pending_via_api}"
    )
    interrupt_id = pending_via_api[0]["id"]
    assert pending_via_api[0]["kind"] == "browser.captcha"

    # Worker is awaiting on the continuation
    interrupt_row = store.get(interrupt_id)
    assert interrupt_row is not None
    assert interrupt_row.continuation_ref is not None
    assert ContinuationRegistry.instance().has(interrupt_row.continuation_ref)

    # EventLog has interrupt.created (browser.navigate emitted by adapter
    # captcha-path may come labelled as browser.captcha_required — assert
    # both interrupt creation and the trigger event).
    types_so_far = [e.type for e in events.query(limit=100)]
    assert "interrupt.created" in types_so_far
    assert any(
        t.startswith("browser.") for t in types_so_far
    ), f"expected at least one browser.* event, got {types_so_far}"

    # Worker not yet done
    assert not worker_task.done()

    # Owner resolves the CAPTCHA out-of-band (Kevin clicks through via VNC)
    browser_mgr.resolve_captcha(URL)

    # Approve via the Desktop API. Must return sub-second.
    t0 = time.monotonic()
    r = client.post(
        f"/api/v2/interrupts/{interrupt_id}/approve",
        json={"payload": {"ok": True}},
    )
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert r.json()["status"] == "resumed"
    assert elapsed < 1.0, f"/approve took {elapsed:.3f}s — must be sub-second"

    # Worker resumes and completes
    result = await asyncio.wait_for(worker_task, timeout=2.0)
    assert result.status == "done", (
        f"worker failed: status={result.status!r} reason={result.reason!r}"
    )

    # InterruptStore row transitioned to resolved
    assert store.get(interrupt_id).status == "resolved"

    # Browser was hit twice — once for the CAPTCHA page, once for the retry
    assert browser_mgr.navigate_calls == 2

    # ContinuationRegistry empty (worker's finally cleanup did its job)
    assert not ContinuationRegistry.instance().has(interrupt_row.continuation_ref)

    # EventLog records the canonical sequence
    types_asc = list(reversed([e.type for e in events.query(limit=200)]))

    def _idx(t: str) -> int:
        try:
            return types_asc.index(t)
        except ValueError:
            return -1

    assert _idx("interrupt.created") >= 0
    assert _idx("interrupt.resolved") >= 0
    assert _idx("interrupt.resolved") > _idx("interrupt.created")
    assert _idx("browser.ok") > _idx("interrupt.resolved")


async def test_resident_operator_without_kernel_fails_predictably():
    """When services has no kernel, the worker reports the wiring gap
    rather than crashing with AttributeError. Guards against silent
    regression of the PR-13 brain wiring."""
    spec = builtin_resident_operator_spec()
    factory = AgentFactory(llm_router=None, tool_registry=None, mutation_log=None)
    worker = factory.create(spec, services_override={})

    msg = AgentMessage(
        from_agent="lapwing",
        to_agent="resident_operator",
        task_id="t",
        content="navigate https://x.test",
        context_digest="",
        message_type="request",
    )
    result = await worker.execute(msg)
    assert result.status == "failed"
    assert "services['kernel']" in (result.reason or "")


async def test_resident_operator_rejects_unsupported_task(tmp_path: Path):
    """v1 mini-parser only knows 'navigate <url>'. Anything else fails
    deterministically so cognition can correct its prompt."""
    browser_mgr = CaptchaToggleBrowserManager()
    kernel, _store, _events = _build_kernel(tmp_path, browser_mgr)
    worker = _build_resident_operator(kernel)

    for content in ("", "do something interesting", "open https://x.test"):
        msg = AgentMessage(
            from_agent="lapwing",
            to_agent="resident_operator",
            task_id="t",
            content=content,
            context_digest="",
            message_type="request",
        )
        result = await worker.execute(msg)
        assert result.status == "failed"
        assert "navigate <url>" in (result.reason or "")
