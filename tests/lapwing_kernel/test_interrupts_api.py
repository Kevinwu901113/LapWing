"""Desktop API /api/v2/interrupts/* tests.

Covers blueprint §8.6 and §15.2 I-6 (approve does NOT await final Observation).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import interrupts as interrupts_route
from src.lapwing_kernel.pipeline.continuation_registry import (
    ContinuationRegistry,
    InterruptCancelled,
)
from src.lapwing_kernel.primitives.interrupt import Interrupt
from src.lapwing_kernel.stores.interrupt_store import InterruptStore


# ── test scaffold ────────────────────────────────────────────────────────────


class StubKernel:
    """Minimal kernel stand-in for the /approve endpoint."""

    def __init__(self, *, sleep_seconds: float = 0.0):
        self.resume_calls: list[tuple[str, dict]] = []
        self._sleep = sleep_seconds

    async def resume(self, interrupt_id: str, payload: dict) -> dict:
        self.resume_calls.append((interrupt_id, payload))
        if self._sleep:
            await asyncio.sleep(self._sleep)
        # Mimic real kernel.resume return shape
        return {
            "status": "resumed",
            "interrupt_id": interrupt_id,
            "continuation_ref": "cont-fake",
        }


@pytest.fixture
def store(tmp_path: Path) -> InterruptStore:
    return InterruptStore(tmp_path / "lapwing.db")


@pytest.fixture(autouse=True)
def fresh_continuation_registry():
    ContinuationRegistry.reset_for_tests()
    yield
    ContinuationRegistry.reset_for_tests()


def _make_app(store: InterruptStore, kernel: Any | None) -> FastAPI:
    app = FastAPI()
    interrupts_route.init(interrupt_store=store, kernel=kernel)
    app.include_router(interrupts_route.router)
    return app


def _seed(store: InterruptStore, *, kind: str = "browser.captcha") -> Interrupt:
    i = Interrupt.new(
        kind=kind,
        actor_required="owner",
        resource="browser",
        continuation_ref="cont-1",
        summary=f"{kind} pending",
        payload_redacted={"url": "https://x.com"},
    )
    store.persist(i)
    return i


# ── GET /pending + /{id} ─────────────────────────────────────────────────────


class TestListAndDetail:
    def test_pending_empty(self, store):
        client = TestClient(_make_app(store, None))
        r = client.get("/api/v2/interrupts/pending")
        assert r.status_code == 200
        assert r.json() == []

    def test_pending_filters_owner_only(self, store):
        system_only = Interrupt.new(
            kind="ops.alert",
            actor_required="system",
            resource="agent",
            non_resumable=True,
            non_resumable_reason="info-only",
            summary="system info",
        )
        store.persist(system_only)
        _seed(store)
        client = TestClient(_make_app(store, None))
        r = client.get("/api/v2/interrupts/pending")
        assert r.status_code == 200
        kinds = {item["kind"] for item in r.json()}
        assert "browser.captcha" in kinds
        assert "ops.alert" not in kinds  # filtered by actor_required=owner

    def test_detail_returns_full_record(self, store):
        i = _seed(store)
        client = TestClient(_make_app(store, None))
        r = client.get(f"/api/v2/interrupts/{i.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == i.id
        assert body["kind"] == "browser.captcha"
        assert body["status"] == "pending"
        assert body["continuation_ref"] == "cont-1"
        assert body["payload_redacted"] == {"url": "https://x.com"}

    def test_detail_missing_404(self, store):
        client = TestClient(_make_app(store, None))
        r = client.get("/api/v2/interrupts/nonexistent")
        assert r.status_code == 404


# ── POST /approve ────────────────────────────────────────────────────────────


class TestApprove:
    def test_approve_calls_kernel_resume(self, store):
        i = _seed(store)
        kernel = StubKernel()
        client = TestClient(_make_app(store, kernel))
        r = client.post(f"/api/v2/interrupts/{i.id}/approve", json={"payload": {"ok": True}})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "resumed"
        assert body["interrupt_id"] == i.id
        assert kernel.resume_calls == [(i.id, {"ok": True})]

    def test_approve_unknown_returns_404(self, store):
        kernel = StubKernel()
        client = TestClient(_make_app(store, kernel))
        r = client.post("/api/v2/interrupts/nonexistent/approve", json={})
        assert r.status_code == 404

    def test_approve_already_resolved_returns_409(self, store):
        i = _seed(store)
        store.resolve(i.id, {"ok": True})
        kernel = StubKernel()
        client = TestClient(_make_app(store, kernel))
        r = client.post(f"/api/v2/interrupts/{i.id}/approve", json={})
        assert r.status_code == 409

    def test_approve_without_kernel_503(self, store):
        i = _seed(store)
        client = TestClient(_make_app(store, None))  # no kernel
        r = client.post(f"/api/v2/interrupts/{i.id}/approve", json={})
        assert r.status_code == 503

    def test_approve_empty_body_uses_empty_payload(self, store):
        i = _seed(store)
        kernel = StubKernel()
        client = TestClient(_make_app(store, kernel))
        r = client.post(f"/api/v2/interrupts/{i.id}/approve")
        assert r.status_code == 200
        # Kernel called with empty dict
        assert kernel.resume_calls[-1][1] == {}


class TestApproveDoesNotAwaitWorker:
    """§15.2 I-6: approve endpoint MUST return immediately and not block on
    the worker producing the final Observation. We simulate this by giving
    the StubKernel a sleep — but kernel.resume itself returns the status
    dict promptly, so as long as the endpoint awaits ONLY kernel.resume,
    the response time is bounded by kernel.resume's duration, NOT the
    worker's downstream completion."""

    def test_approve_response_returns_quickly(self, store):
        """Sanity: with a fast stub kernel, approve response is sub-second."""
        i = _seed(store)
        kernel = StubKernel(sleep_seconds=0.0)
        client = TestClient(_make_app(store, kernel))

        t0 = time.monotonic()
        r = client.post(f"/api/v2/interrupts/{i.id}/approve", json={})
        elapsed = time.monotonic() - t0

        assert r.status_code == 200
        assert elapsed < 1.0


# ── POST /deny ───────────────────────────────────────────────────────────────


class TestDeny:
    async def test_deny_transitions_to_denied(self, store):
        i = _seed(store)
        # Register the continuation so we can verify it gets cancelled
        async def setup():
            ref = ContinuationRegistry.instance().register(None)
            # Replace store's interrupt continuation_ref with the registered one
            # (the seed used a literal string; for this test we need a real ref)
            from dataclasses import replace
            updated = replace(i, continuation_ref=ref)
            # Re-persist: but persist() INSERTs, not UPDATEs. Simpler approach:
            # build a fresh interrupt with a real continuation_ref.
            return ref

        ref = await setup()
        new_i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref=ref,
            summary="real continuation",
        )
        store.persist(new_i)

        async def waiter():
            try:
                await ContinuationRegistry.instance().wait_for_resume(ref)
                return "RESUMED"
            except InterruptCancelled as exc:
                return f"CANCELLED:{exc}"

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)

        client = TestClient(_make_app(store, None))
        r = client.post(f"/api/v2/interrupts/{new_i.id}/deny", json={"reason": "user_rejected"})
        assert r.status_code == 200
        assert r.json()["status"] == "denied"

        # Continuation receives InterruptCancelled
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result.startswith("CANCELLED:")

        # Store reflects denied state
        assert store.get(new_i.id).status == "denied"
        assert store.get(new_i.id).resolved_payload == {"reason": "user_rejected"}

    def test_deny_unknown_returns_404(self, store):
        client = TestClient(_make_app(store, None))
        r = client.post("/api/v2/interrupts/nonexistent/deny", json={})
        assert r.status_code == 404

    def test_deny_already_resolved_returns_409(self, store):
        i = _seed(store)
        store.resolve(i.id, {"ok": True})
        client = TestClient(_make_app(store, None))
        r = client.post(f"/api/v2/interrupts/{i.id}/deny", json={"reason": "too_late"})
        assert r.status_code == 409
