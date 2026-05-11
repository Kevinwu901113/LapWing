"""state_facade unit tests — read_state / update_state / read_fact dispatch."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.lapwing_kernel.state_facade import (
    FACT_SCOPES,
    STATE_SCOPES,
    read_fact,
    read_state,
    update_state,
)


# ── read_state ───────────────────────────────────────────────────────────────


class TestReadState:
    async def test_datetime_always_wired(self):
        result = await read_state(scope="datetime")
        assert result["status"] == "ok"
        assert result["scope"] == "datetime"
        assert "iso" in result["value"]
        assert "unix" in result["value"]

    async def test_unknown_scope_returns_error(self):
        result = await read_state(scope="banana")
        assert result["status"] == "error"
        assert "unknown_scope" in result["error"]

    async def test_recognized_scope_without_service_not_routed(self):
        # reminder scope is recognized but no durable_scheduler injected
        result = await read_state(scope="reminder", services={})
        assert result["status"] == "not_yet_routed"
        assert result["scope"] == "reminder"

    async def test_reminder_with_scheduler_dispatches(self):
        sched = MagicMock()
        sched.list_reminders = AsyncMock(return_value=[{"id": "r1", "text": "x"}])
        result = await read_state(
            scope="reminder",
            services={"durable_scheduler": sched},
        )
        assert result["status"] == "ok"
        assert result["value"]["reminders"] == [{"id": "r1", "text": "x"}]
        sched.list_reminders.assert_awaited_once()

    async def test_identity_returns_non_sensitive_subset(self):
        identity = SimpleNamespace(
            agent_name="Lapwing",
            owner_name="Kevin",
            home_server_name="pve-home-01",
            email_address="kevin@example.com",  # SHOULD NOT leak
        )
        result = await read_state(
            scope="identity", services={"identity": identity}
        )
        assert result["status"] == "ok"
        assert result["value"]["agent_name"] == "Lapwing"
        assert result["value"]["owner_name"] == "Kevin"
        assert "email_address" not in result["value"]

    async def test_focus_with_manager_dispatches(self):
        focus_mgr = MagicMock()
        focus_mgr.current = MagicMock(return_value={"topic": "browser refactor"})
        result = await read_state(
            scope="focus", services={"focus_manager": focus_mgr}
        )
        assert result["status"] == "ok"
        assert result["value"]["current"] == {"topic": "browser refactor"}

    async def test_agents_with_catalog_dispatches(self):
        catalog = MagicMock()
        catalog.list = AsyncMock(
            return_value=[{"name": "researcher"}, {"name": "coder"}]
        )
        result = await read_state(scope="agents", services={"agent_catalog": catalog})
        assert result["status"] == "ok"
        assert len(result["value"]["agents"]) == 2


# ── update_state ─────────────────────────────────────────────────────────────


class TestUpdateState:
    async def test_unknown_scope_returns_error(self):
        result = await update_state(scope="banana", op="add", value={})
        assert result["status"] == "error"

    async def test_recognized_scope_without_service_not_routed(self):
        result = await update_state(
            scope="reminder", op="add", value={"text": "x"}, services={}
        )
        assert result["status"] == "not_yet_routed"

    async def test_focus_close_dispatches(self):
        focus_mgr = MagicMock()
        focus_mgr.close = AsyncMock(return_value=None)
        result = await update_state(
            scope="focus",
            op="close",
            value={"reason": "test"},
            services={"focus_manager": focus_mgr},
        )
        assert result["status"] == "ok"
        focus_mgr.close.assert_awaited_once_with(reason="test")

    async def test_reminder_add_dispatches(self):
        sched = MagicMock()
        sched.add_reminder = AsyncMock(return_value="r-123")
        result = await update_state(
            scope="reminder",
            op="add",
            value={"text": "buy milk", "due_at": "2026-01-01T00:00:00Z"},
            services={"durable_scheduler": sched},
        )
        assert result["status"] == "ok"
        assert result["id"] == "r-123"

    async def test_reminder_cancel_dispatches(self):
        sched = MagicMock()
        sched.cancel_reminder = AsyncMock(return_value=None)
        result = await update_state(
            scope="reminder",
            op="cancel",
            value={"id": "r-123"},
            services={"durable_scheduler": sched},
        )
        assert result["status"] == "ok"
        sched.cancel_reminder.assert_awaited_once_with("r-123")

    async def test_correction_add_dispatches(self):
        corr = MagicMock()
        corr.add = AsyncMock(return_value=None)
        result = await update_state(
            scope="correction",
            op="add",
            value={"text": "don't end with 嗯"},
            services={"correction_manager": corr},
        )
        assert result["status"] == "ok"


# ── read_fact ────────────────────────────────────────────────────────────────


class TestReadFact:
    async def test_unknown_scope_returns_error(self):
        result = await read_fact(scope="banana")
        assert result["status"] == "error"

    async def test_eventlog_dispatches_to_event_log_query(self):
        ev = MagicMock()
        # Mock returns a single Event-like object
        fake_event = SimpleNamespace(
            id="e1",
            time=SimpleNamespace(isoformat=lambda: "2026-05-11T00:00:00"),
            actor="lapwing",
            type="browser.navigate",
            resource="browser",
            summary="loaded x",
            outcome="ok",
        )
        ev.query = MagicMock(return_value=[fake_event])
        result = await read_fact(
            scope="eventlog",
            query={"type_prefix": "browser.", "limit": 10},
            services={"event_log": ev},
        )
        assert result["status"] == "ok"
        assert len(result["value"]["events"]) == 1
        assert result["value"]["events"][0]["id"] == "e1"
        ev.query.assert_called_once()

    async def test_eventlog_limit_capped(self):
        ev = MagicMock()
        ev.query = MagicMock(return_value=[])
        await read_fact(
            scope="eventlog", query={"limit": 99999}, services={"event_log": ev}
        )
        # query should be called with limit clamped to 500
        kwargs = ev.query.call_args.kwargs
        assert kwargs["limit"] == 500

    async def test_no_service_returns_not_routed(self):
        result = await read_fact(scope="eventlog", services={})
        assert result["status"] == "not_yet_routed"


# ── scope inventories are stable strings ────────────────────────────────────


class TestScopeInventories:
    def test_state_scopes_contains_core_set(self):
        for s in (
            "reminder",
            "focus",
            "correction",
            "note",
            "datetime",
            "identity",
        ):
            assert s in STATE_SCOPES

    def test_fact_scopes_contains_core_set(self):
        for s in ("wiki", "eventlog", "trajectory"):
            assert s in FACT_SCOPES
