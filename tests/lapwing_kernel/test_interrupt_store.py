"""InterruptStore tests — persist + state-machine transitions + queries.

Covers blueprint §8.2 and §8.5 state machine.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.lapwing_kernel.primitives.interrupt import (
    DEFAULT_INTERRUPT_EXPIRY,
    Interrupt,
)
from src.lapwing_kernel.stores.interrupt_store import InterruptStore


@pytest.fixture
def store(tmp_path: Path) -> InterruptStore:
    return InterruptStore(tmp_path / "lapwing.db")


def _make_interrupt(
    *,
    kind: str = "browser.captcha",
    actor: str = "owner",
    resource: str = "browser",
    continuation_ref: str | None = "cont-1",
    non_resumable: bool = False,
    summary: str = "captcha required",
    expires_in: timedelta | None = None,
) -> Interrupt:
    return Interrupt.new(
        kind=kind,
        actor_required=actor,
        resource=resource,
        continuation_ref=continuation_ref,
        non_resumable=non_resumable,
        summary=summary,
        expires_in=expires_in,
    )


# ── persist + get ───────────────────────────────────────────────────────────


class TestPersistAndGet:
    def test_round_trip(self, store):
        i = _make_interrupt()
        store.persist(i)
        loaded = store.get(i.id)
        assert loaded is not None
        assert loaded.id == i.id
        assert loaded.kind == "browser.captcha"
        assert loaded.status == "pending"
        assert loaded.continuation_ref == "cont-1"
        assert loaded.non_resumable is False

    def test_missing_returns_none(self, store):
        assert store.get("nonexistent-id") is None

    def test_persists_expires_at(self, store):
        i = _make_interrupt(expires_in=DEFAULT_INTERRUPT_EXPIRY["browser.captcha"])
        store.persist(i)
        loaded = store.get(i.id)
        assert loaded.expires_at is not None
        assert loaded.expires_at > loaded.created_at

    def test_non_resumable_round_trip(self, store):
        i = _make_interrupt(
            continuation_ref=None,
            non_resumable=True,
        )
        store.persist(i)
        loaded = store.get(i.id)
        assert loaded.non_resumable is True
        assert loaded.continuation_ref is None

    def test_payload_redacted_round_trip(self, store):
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-1",
            payload_redacted={"url": "https://x.com", "profile": "personal"},
        )
        store.persist(i)
        loaded = store.get(i.id)
        assert loaded.payload_redacted == {"url": "https://x.com", "profile": "personal"}


# ── list_pending ────────────────────────────────────────────────────────────


class TestListPending:
    def test_empty_returns_empty_list(self, store):
        assert store.list_pending() == []

    def test_lists_only_pending(self, store):
        a = _make_interrupt(kind="browser.captcha")
        b = _make_interrupt(kind="browser.login_required")
        store.persist(a)
        store.persist(b)
        store.resolve(a.id, {"ok": True})
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].id == b.id

    def test_filter_by_actor(self, store):
        a = _make_interrupt(actor="owner")
        b = _make_interrupt(actor="system")
        store.persist(a)
        store.persist(b)
        owner_only = store.list_pending(actor="owner")
        assert len(owner_only) == 1
        assert owner_only[0].id == a.id

    def test_ordered_by_created_desc(self, store):
        a = _make_interrupt(summary="first")
        b = _make_interrupt(summary="second")
        store.persist(a)
        store.persist(b)
        pending = store.list_pending()
        # Most-recently-created first
        assert pending[0].id == b.id
        assert pending[1].id == a.id


# ── state-machine transitions (blueprint §8.5) ──────────────────────────────


class TestStateMachine:
    def test_pending_to_resolved(self, store):
        i = _make_interrupt()
        store.persist(i)
        store.resolve(i.id, {"approved": True})
        loaded = store.get(i.id)
        assert loaded.status == "resolved"
        assert loaded.resolved_payload == {"approved": True}

    def test_pending_to_denied(self, store):
        i = _make_interrupt()
        store.persist(i)
        store.deny(i.id, reason="user_rejected")
        loaded = store.get(i.id)
        assert loaded.status == "denied"
        assert loaded.resolved_payload == {"reason": "user_rejected"}

    def test_pending_to_cancelled_with_reason(self, store):
        """cancel() captures reason — critical for distinguishing
        continuation_lost_after_restart from task-side cancel."""
        i = _make_interrupt()
        store.persist(i)
        store.cancel(i.id, reason="continuation_lost_after_restart")
        loaded = store.get(i.id)
        assert loaded.status == "cancelled"
        assert loaded.resolved_payload == {
            "reason": "continuation_lost_after_restart"
        }

    def test_resolve_already_resolved_is_noop(self, store):
        """Status guard: UPDATE only fires if status='pending'."""
        i = _make_interrupt()
        store.persist(i)
        store.resolve(i.id, {"first": True})
        # Second resolve attempt — should NOT overwrite
        store.resolve(i.id, {"second": True})
        loaded = store.get(i.id)
        assert loaded.resolved_payload == {"first": True}

    def test_deny_after_resolve_is_noop(self, store):
        i = _make_interrupt()
        store.persist(i)
        store.resolve(i.id, {"ok": True})
        store.deny(i.id, reason="too_late")
        loaded = store.get(i.id)
        assert loaded.status == "resolved"

    def test_cancel_after_resolve_is_noop(self, store):
        """Cancelling a resolved interrupt must NOT transition it back."""
        i = _make_interrupt()
        store.persist(i)
        store.resolve(i.id, {"ok": True})
        store.cancel(i.id, reason="continuation_lost_after_restart")
        loaded = store.get(i.id)
        assert loaded.status == "resolved"


# ── expire_overdue ──────────────────────────────────────────────────────────


class TestExpire:
    def test_overdue_pending_becomes_expired(self, store, monkeypatch):
        """An interrupt whose expires_at is in the past becomes 'expired'
        after expire_overdue()."""
        past = datetime.utcnow() - timedelta(hours=1)
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-x",
        )
        # Build a copy with expires_at in the past (frozen dataclass)
        from dataclasses import replace

        i_past = replace(i, expires_at=past)
        store.persist(i_past)
        n = store.expire_overdue()
        assert n == 1
        loaded = store.get(i.id)
        assert loaded.status == "expired"

    def test_future_pending_unchanged(self, store):
        i = _make_interrupt(expires_in=timedelta(hours=24))
        store.persist(i)
        store.expire_overdue()
        loaded = store.get(i.id)
        assert loaded.status == "pending"

    def test_already_resolved_not_re_expired(self, store):
        from dataclasses import replace

        past = datetime.utcnow() - timedelta(hours=1)
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-y",
        )
        i_past = replace(i, expires_at=past)
        store.persist(i_past)
        store.resolve(i.id, {"ok": True})
        store.expire_overdue()
        loaded = store.get(i.id)
        # Stayed resolved
        assert loaded.status == "resolved"

    def test_expired_with_no_expiry_set_unchanged(self, store):
        i = _make_interrupt(expires_in=None)
        store.persist(i)
        store.expire_overdue()
        loaded = store.get(i.id)
        assert loaded.status == "pending"


# ── list_expired_continuations ──────────────────────────────────────────────


class TestListExpiredContinuations:
    def test_returns_continuation_refs_of_expired(self, store):
        from dataclasses import replace

        past = datetime.utcnow() - timedelta(hours=1)
        i = Interrupt.new(
            kind="browser.captcha",
            actor_required="owner",
            resource="browser",
            continuation_ref="cont-abc",
        )
        i_past = replace(i, expires_at=past)
        store.persist(i_past)
        store.expire_overdue()
        refs = store.list_expired_continuations()
        assert "cont-abc" in refs

    def test_skips_non_resumable_expired(self, store):
        """Expired interrupts without continuation_ref (non_resumable)
        are not surfaced."""
        from dataclasses import replace

        past = datetime.utcnow() - timedelta(hours=1)
        i = Interrupt.new(
            kind="browser.waf",
            actor_required="owner",
            resource="browser",
            non_resumable=True,
            non_resumable_reason="fetch profile",
        )
        i_past = replace(i, expires_at=past)
        store.persist(i_past)
        store.expire_overdue()
        refs = store.list_expired_continuations()
        assert refs == []


# ── schema reuse (idempotent init) ──────────────────────────────────────────


class TestSchema:
    def test_init_idempotent(self, tmp_path):
        """Constructing the store twice over the same db must not error."""
        path = tmp_path / "lapwing.db"
        store1 = InterruptStore(path)
        i = _make_interrupt()
        store1.persist(i)
        # Fresh instance over the same DB
        store2 = InterruptStore(path)
        assert store2.get(i.id) is not None

    def test_creates_parent_dir(self, tmp_path):
        """Store must create its DB's parent dir if missing."""
        db_path = tmp_path / "subdir" / "nested" / "lapwing.db"
        InterruptStore(db_path)
        assert db_path.parent.is_dir()


# ── integration with ActionExecutor (real store, real registry) ─────────────


class TestActionExecutorIntegration:
    """Replaces the MockInterruptStore in test_executor.py with the real
    InterruptStore to ensure the executor's Protocol assumptions match
    the real implementation."""

    async def test_executor_persists_and_resolves_via_real_store(
        self, tmp_path
    ):
        from src.lapwing_kernel.pipeline.continuation_registry import (
            ContinuationRegistry,
        )
        from src.lapwing_kernel.pipeline.executor import ActionExecutor
        from src.lapwing_kernel.pipeline.registry import ResourceRegistry
        from src.lapwing_kernel.policy import PolicyDecider
        from src.lapwing_kernel.primitives.action import Action
        from src.lapwing_kernel.primitives.event import Event

        class MockEventLog:
            def __init__(self):
                self.events: list[Event] = []

            def append(self, event):
                self.events.append(event)

        ContinuationRegistry.reset_for_tests()
        try:
            db_path = tmp_path / "lapwing.db"
            store = InterruptStore(db_path)
            events = MockEventLog()
            reg = ResourceRegistry()
            policy = PolicyDecider(config={})  # use_state=None → INTERRUPT credential.use
            exec_ = ActionExecutor(reg, store, events, policy)

            action = Action.new("credential", "use", args={"service": "github"})
            obs = await exec_.execute(action)
            assert obs.status == "interrupted"

            # Real store has the interrupt
            persisted = store.get(obs.interrupt_id)
            assert persisted is not None
            assert persisted.status == "pending"

            # Resume via executor; real store transitions to resolved
            result = await exec_.resume(obs.interrupt_id, {"approved": True})
            assert result["status"] == "resumed"

            after = store.get(obs.interrupt_id)
            assert after.status == "resolved"
            assert after.resolved_payload == {"approved": True}
        finally:
            ContinuationRegistry.reset_for_tests()

    async def test_executor_real_store_lost_continuation_cancels(
        self, tmp_path
    ):
        """Same as the mock-based test in test_executor.py but with the real
        InterruptStore — verifies the cancel(reason=...) call lands correctly."""
        from src.lapwing_kernel.pipeline.continuation_registry import (
            ContinuationRegistry,
        )
        from src.lapwing_kernel.pipeline.executor import ActionExecutor
        from src.lapwing_kernel.pipeline.registry import ResourceRegistry
        from src.lapwing_kernel.policy import PolicyDecider
        from src.lapwing_kernel.primitives.action import Action
        from src.lapwing_kernel.primitives.event import Event

        class MockEventLog:
            def __init__(self):
                self.events: list[Event] = []

            def append(self, event):
                self.events.append(event)

        ContinuationRegistry.reset_for_tests()
        try:
            db_path = tmp_path / "lapwing.db"
            store = InterruptStore(db_path)
            events = MockEventLog()
            reg = ResourceRegistry()
            policy = PolicyDecider(config={})
            exec_ = ActionExecutor(reg, store, events, policy)

            action = Action.new("credential", "use", args={"service": "github"})
            obs = await exec_.execute(action)

            # Simulate kernel restart between create and resume
            ContinuationRegistry.reset_for_tests()

            result = await exec_.resume(obs.interrupt_id, {"approved": True})
            assert result["status"] == "error"
            assert result["reason"] == "continuation_lost_after_restart"

            after = store.get(obs.interrupt_id)
            assert after.status == "cancelled"
            assert after.resolved_payload == {
                "reason": "continuation_lost_after_restart"
            }
        finally:
            ContinuationRegistry.reset_for_tests()
