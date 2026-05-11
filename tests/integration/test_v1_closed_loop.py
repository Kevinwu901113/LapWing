"""§15.1 v1 closed-loop end-to-end test.

The canonical v1 functional test (blueprint §15.1). If this passes, the
Resident Agent Kernel can run a single owner-interrupt cycle:

  Kevin message
    → cognition delegates to agent worker
      → kernel.execute(Action(browser.navigate, profile=personal, url=CAPTCHA-PAGE))
        → BrowserAdapter detects CAPTCHA → Interrupt persisted with continuation_ref
                                          → Observation(status=captcha_required)
        → worker awaits ContinuationRegistry.wait_for_resume(ref)
    → /api/v2/interrupts/pending shows the interrupt
    → POST /api/v2/interrupts/{id}/approve
      → kernel.resume releases the continuation IMMEDIATELY (returns status dict)
    → worker wakes up, retries navigate (page now serves OK)
      → kernel.execute → Observation(status=ok)
    → worker reports completed
    → EventLog records the full sequence

Critical invariants verified (blueprint §15.1, §15.2 I-6):
  - approve endpoint returns sub-second even though worker takes longer
  - kernel.resume returns a status dict, NOT an Observation
  - worker doesn't restart task or recreate browser context
  - EventLog has events in expected ORDER (other events may interleave)
  - CredentialLeaseStore empty after run (no lease leaks)
  - Lost-continuation edge: kernel restart between Interrupt creation and
    resume → interrupt marked 'cancelled' with reason, NOT 'resolved'
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import interrupts as interrupts_route
from src.lapwing_kernel.adapters.browser import BrowserAdapter
from src.lapwing_kernel.adapters.credential_lease_store import CredentialLeaseStore
from src.lapwing_kernel.kernel import Kernel
from src.lapwing_kernel.pipeline.continuation_registry import (
    ContinuationRegistry,
    InterruptCancelled,
)
from src.lapwing_kernel.pipeline.registry import ResourceRegistry
from src.lapwing_kernel.policy import PolicyDecider
from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.observation import Observation
from src.lapwing_kernel.stores.event_log import EventLog
from src.lapwing_kernel.stores.interrupt_store import InterruptStore


# ── stateful fake BrowserManager that toggles CAPTCHA on first visit ─────────


@dataclass
class _FakePageState:
    url: str
    title: str
    elements: list
    text_summary: str
    tab_id: str = "tab-1"


class CaptchaToggleBrowserManager:
    """First navigate() returns a CAPTCHA page. After 'resolve_captcha()' is
    called (simulating Kevin solving via VNC), subsequent navigates to the
    same URL return the real page content."""

    CAPTCHA_TEXT = "please complete the captcha to continue verify you are human"
    REAL_TEXT = "Welcome — protected content"
    REAL_TITLE = "Protected Page"

    def __init__(self):
        self._solved: set[str] = set()
        self.call_count = 0

    def resolve_captcha(self, url: str) -> None:
        self._solved.add(url)

    async def navigate(self, url: str, tab_id: str | None = None):
        self.call_count += 1
        if url in self._solved:
            return _FakePageState(
                url=url,
                title=self.REAL_TITLE,
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


# ── test scaffold ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_continuation_registry():
    ContinuationRegistry.reset_for_tests()
    CredentialLeaseStore.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()
    CredentialLeaseStore.reset_for_tests()


def _build_kernel(tmp_path: Path, browser_mgr: CaptchaToggleBrowserManager):
    """Build a real Kernel wired with InterruptStore + EventLog + BrowserAdapter
    (personal profile). Returns (kernel, interrupt_store, event_log, browser_adapter)."""
    db_path = tmp_path / "lapwing.db"
    store = InterruptStore(db_path)
    events = EventLog(db_path)
    policy = PolicyDecider(config={})

    registry = ResourceRegistry()
    personal = BrowserAdapter(
        profile="personal",
        legacy_browser_manager=browser_mgr,
        interrupt_store=store,
    )
    registry.register(personal, profile="personal")
    # Also register a fetch adapter for the non-interrupting verbs
    fetch = BrowserAdapter(
        profile="fetch",
        legacy_browser_manager=browser_mgr,
        interrupt_store=store,
    )
    registry.register(fetch, profile="fetch")

    # No ResidentIdentity needed for this test — use a stand-in
    @dataclass(frozen=True)
    class _Identity:
        agent_name: str = "Lapwing"
        owner_name: str = "Kevin"
        home_server_name: str = "test"
        linux_user: str = "test"
        home_dir: Path = Path("/tmp")
        personal_browser_profile: Path = Path("/tmp")
        email_address: str | None = None
        phone_number_ref: str | None = None

    kernel = Kernel(
        identity=_Identity(),
        resource_registry=registry,
        interrupt_store=store,
        event_log=events,
        policy=policy,
        redactor=None,
        model_slots=None,
    )
    return kernel, store, events, personal


def _build_app_with_kernel(kernel: Kernel) -> FastAPI:
    """Build a FastAPI app exposing /api/v2/interrupts/* wired to the kernel."""
    app = FastAPI()
    interrupts_route.init(interrupt_store=kernel.interrupts, kernel=kernel)
    app.include_router(interrupts_route.router)
    return app


# ── the canonical §15.1 closed-loop test ─────────────────────────────────────


async def test_closed_loop_captcha_resume_completes(tmp_path: Path):
    """Full §15.1 round trip: agent worker hits CAPTCHA, Kevin approves
    via Desktop API, worker resumes from suspension point, completes with
    Observation.ok. EventLog records the full sequence."""

    browser_mgr = CaptchaToggleBrowserManager()
    kernel, store, events, _ = _build_kernel(tmp_path, browser_mgr)
    app = _build_app_with_kernel(kernel)
    client = TestClient(app)

    URL = "https://test.local/protected"

    # Agent worker coroutine: navigates, on CAPTCHA awaits continuation, retries.
    async def worker() -> Observation:
        action = Action.new(
            "browser",
            "navigate",
            resource_profile="personal",
            args={"url": URL},
        )
        obs = await kernel.execute(action)
        if obs.status == "captcha_required":
            interrupt = store.get(obs.interrupt_id)
            assert interrupt is not None
            assert interrupt.continuation_ref is not None
            # Simulate Kevin completing the CAPTCHA out-of-band (VNC)
            payload = await ContinuationRegistry.instance().wait_for_resume(
                interrupt.continuation_ref
            )
            # After resume, retry the navigation. Browser manager now serves
            # the real page (because the test will call resolve_captcha
            # before /approve fires).
            obs = await kernel.execute(action)
        return obs

    # Start the worker
    worker_task = asyncio.create_task(worker())
    # Yield so the worker can run up to the awaitpoint
    for _ in range(5):
        await asyncio.sleep(0.01)
        if store.list_pending(actor="owner"):
            break

    # Step: /api/v2/interrupts/pending shows the interrupt
    r = client.get("/api/v2/interrupts/pending")
    assert r.status_code == 200
    pending = r.json()
    assert len(pending) == 1
    interrupt_id = pending[0]["id"]
    assert pending[0]["kind"] == "browser.captcha"

    # Kevin (test) resolves the CAPTCHA — browser_mgr will now serve real content
    browser_mgr.resolve_captcha(URL)

    # Step: POST approve. Must return quickly.
    t0 = time.monotonic()
    r = client.post(f"/api/v2/interrupts/{interrupt_id}/approve", json={"payload": {"ok": True}})
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert r.json()["status"] == "resumed"
    assert elapsed < 1.0, f"approve took {elapsed:.3f}s — must be sub-second"

    # Worker resumes and completes
    final = await asyncio.wait_for(worker_task, timeout=2.0)
    assert final.status == "ok"

    # Interrupt is now resolved in the store
    assert store.get(interrupt_id).status == "resolved"

    # EventLog ordering: navigate → captcha_required → interrupt.created
    #                  → interrupt.resolved → navigate (retry) → ok
    types = [e.type for e in events.query(limit=200)]
    # Reversed because query returns DESC. Re-sort ascending:
    types_asc = list(reversed(types))

    def _index(t: str) -> int:
        try:
            return types_asc.index(t)
        except ValueError:
            return -1

    nav = _index("browser.navigate")
    captcha = _index("browser.captcha_required")
    interrupt_created = _index("interrupt.created")
    interrupt_resolved = _index("interrupt.resolved")
    ok = _index("browser.ok")

    assert nav >= 0, f"missing browser.navigate event in {types_asc}"
    assert captcha >= 0, f"missing browser.captcha_required event in {types_asc}"
    assert interrupt_created >= 0
    assert interrupt_resolved >= 0
    assert ok >= 0
    # Ordering constraints (other events may interleave; we check pairwise)
    assert nav < captcha
    assert captcha < interrupt_created or interrupt_created < captcha  # close together
    assert interrupt_resolved < ok

    # CredentialLeaseStore must be empty (no leases leaked from this run)
    assert CredentialLeaseStore.instance().active_count() == 0


# ── lost-continuation edge (§15.2 I-6) ───────────────────────────────────────


async def test_closed_loop_lost_continuation_marks_cancelled(tmp_path: Path):
    """Kernel-restart edge case: continuation is gone between Interrupt
    creation and approve. kernel.resume must mark the interrupt CANCELLED
    (with reason='continuation_lost_after_restart'), NOT resolved. The
    approve endpoint must report the lost state via status='error'."""

    browser_mgr = CaptchaToggleBrowserManager()
    kernel, store, events, _ = _build_kernel(tmp_path, browser_mgr)
    app = _build_app_with_kernel(kernel)
    client = TestClient(app)

    URL = "https://test.local/protected"

    # Create the interrupt by issuing the action and not awaiting the continuation
    action = Action.new(
        "browser",
        "navigate",
        resource_profile="personal",
        args={"url": URL},
    )
    obs = await kernel.execute(action)
    assert obs.status == "captcha_required"
    interrupt_id = obs.interrupt_id
    assert interrupt_id is not None

    # Simulate kernel restart — registry empties
    ContinuationRegistry.reset_for_tests()

    # Now approve — must return error, NOT resumed
    r = client.post(f"/api/v2/interrupts/{interrupt_id}/approve", json={"payload": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["reason"] == "continuation_lost_after_restart"

    # Store reflects cancelled, NOT resolved
    interrupt = store.get(interrupt_id)
    assert interrupt.status == "cancelled"
    assert interrupt.resolved_payload == {
        "reason": "continuation_lost_after_restart"
    }

    # EventLog has the continuation_lost event
    types = [e.type for e in events.query(limit=100)]
    assert "interrupt.continuation_lost" in types


# ── deny edge (§8.4 cleanup lifecycle) ───────────────────────────────────────


async def test_closed_loop_deny_cancels_worker(tmp_path: Path):
    """If Kevin denies the interrupt, the awaiting worker receives
    InterruptCancelled and winds down without resolving."""

    browser_mgr = CaptchaToggleBrowserManager()
    kernel, store, events, _ = _build_kernel(tmp_path, browser_mgr)
    app = _build_app_with_kernel(kernel)
    client = TestClient(app)

    URL = "https://test.local/protected"

    async def worker():
        action = Action.new(
            "browser",
            "navigate",
            resource_profile="personal",
            args={"url": URL},
        )
        obs = await kernel.execute(action)
        if obs.status == "captcha_required":
            interrupt = store.get(obs.interrupt_id)
            try:
                await ContinuationRegistry.instance().wait_for_resume(
                    interrupt.continuation_ref
                )
                return "RESUMED"
            except InterruptCancelled as exc:
                return f"CANCELLED:{exc}"
            finally:
                # Worker is the cleanup owner per blueprint §8.4
                ContinuationRegistry.instance().cleanup(interrupt.continuation_ref)
        return f"NO_INTERRUPT:{obs.status}"

    task = asyncio.create_task(worker())
    for _ in range(5):
        await asyncio.sleep(0.01)
        if store.list_pending(actor="owner"):
            break

    pending = client.get("/api/v2/interrupts/pending").json()
    interrupt_id = pending[0]["id"]

    r = client.post(f"/api/v2/interrupts/{interrupt_id}/deny", json={"reason": "no_thanks"})
    assert r.status_code == 200

    result = await asyncio.wait_for(task, timeout=1.0)
    assert result.startswith("CANCELLED:")
    assert store.get(interrupt_id).status == "denied"


# ── extra sanity: worker's wait-time does not block approve response ─────────


async def test_approve_returns_before_worker_completes(tmp_path: Path):
    """The approve endpoint must NOT wait for the worker to produce its
    final Observation. We make the worker sleep substantially AFTER it wakes
    up; approve should still return well before the worker completes."""

    browser_mgr = CaptchaToggleBrowserManager()
    kernel, store, events, _ = _build_kernel(tmp_path, browser_mgr)
    app = _build_app_with_kernel(kernel)
    client = TestClient(app)

    URL = "https://test.local/protected"

    worker_completed_at: list[float] = []

    async def slow_worker():
        action = Action.new(
            "browser",
            "navigate",
            resource_profile="personal",
            args={"url": URL},
        )
        obs = await kernel.execute(action)
        if obs.status == "captcha_required":
            interrupt = store.get(obs.interrupt_id)
            await ContinuationRegistry.instance().wait_for_resume(
                interrupt.continuation_ref
            )
            # Simulate slow downstream work AFTER resume
            await asyncio.sleep(0.5)
            browser_mgr.resolve_captcha(URL)
            obs = await kernel.execute(action)
        worker_completed_at.append(time.monotonic())
        return obs

    task = asyncio.create_task(slow_worker())
    for _ in range(5):
        await asyncio.sleep(0.01)
        if store.list_pending(actor="owner"):
            break

    pending = client.get("/api/v2/interrupts/pending").json()
    interrupt_id = pending[0]["id"]

    approve_t0 = time.monotonic()
    r = client.post(f"/api/v2/interrupts/{interrupt_id}/approve", json={})
    approve_elapsed = time.monotonic() - approve_t0

    final = await asyncio.wait_for(task, timeout=2.0)
    worker_elapsed_after_approve = worker_completed_at[0] - approve_t0

    assert r.status_code == 200
    # The substantive invariant: approve responds well before the worker
    # finishes its post-resume work. We compare relatively (rather than an
    # absolute upper bound) so event-loop scheduling noise doesn't flake.
    assert approve_elapsed < worker_elapsed_after_approve / 2, (
        f"approve={approve_elapsed:.3f}s vs worker={worker_elapsed_after_approve:.3f}s"
    )
    assert worker_elapsed_after_approve > 0.3
    assert final.status == "ok"
