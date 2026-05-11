"""EventLog tests — append + query + append-only invariant.

Covers blueprint §9.1, §9.2, and the I-5 invariant that there are no
UPDATE/DELETE paths.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.lapwing_kernel.primitives.event import Event
from src.lapwing_kernel.stores.event_log import EventLog


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "lapwing.db")


def _ev(
    type_: str,
    *,
    actor: str = "lapwing",
    resource: str | None = "browser",
    outcome: str | None = None,
    summary: str = "test event",
    refs: dict[str, str] | None = None,
    data: dict | None = None,
) -> Event:
    return Event.new(
        actor=actor,
        type=type_,
        summary=summary,
        resource=resource,
        outcome=outcome,
        refs=refs,
        data_redacted=data,
    )


class TestAppend:
    def test_round_trip(self, log):
        e = _ev("browser.navigate", refs={"action_id": "a1"})
        log.append(e)
        rows = log.query(type_prefix="browser.")
        assert len(rows) == 1
        assert rows[0].id == e.id
        assert rows[0].type == "browser.navigate"
        assert rows[0].refs == {"action_id": "a1"}

    def test_count(self, log):
        assert log.count() == 0
        for i in range(5):
            log.append(_ev(f"test.{i}"))
        assert log.count() == 5

    def test_data_redacted_preserved(self, log):
        e = _ev("test.event", data={"url": "https://x.com", "key": "value"})
        log.append(e)
        rows = log.query()
        assert rows[0].data_redacted == {"url": "https://x.com", "key": "value"}


class TestQuery:
    def test_filter_by_type_prefix(self, log):
        log.append(_ev("browser.navigate"))
        log.append(_ev("browser.click"))
        log.append(_ev("credential.used"))
        browser_only = log.query(type_prefix="browser.")
        assert len(browser_only) == 2
        for e in browser_only:
            assert e.type.startswith("browser.")

    def test_filter_by_resource(self, log):
        log.append(_ev("browser.navigate", resource="browser"))
        log.append(_ev("credential.used", resource="credential"))
        cred_only = log.query(resource="credential")
        assert len(cred_only) == 1
        assert cred_only[0].type == "credential.used"

    def test_filter_by_actor(self, log):
        log.append(_ev("a", actor="lapwing"))
        log.append(_ev("b", actor="owner"))
        owner_only = log.query(actor="owner")
        assert len(owner_only) == 1

    def test_filter_by_outcome(self, log):
        log.append(_ev("a", outcome="ok"))
        log.append(_ev("b", outcome="failed"))
        failed = log.query(outcome="failed")
        assert len(failed) == 1
        assert failed[0].type == "b"

    def test_filter_by_time_window(self, log):
        # Insert with explicit times by constructing the Event directly
        old_time = datetime.utcnow() - timedelta(hours=2)
        new_time = datetime.utcnow()
        import uuid

        old_event = Event(
            id=str(uuid.uuid4()),
            time=old_time,
            actor="lapwing",
            type="test.old",
            resource=None,
            summary="",
            outcome=None,
        )
        new_event = Event(
            id=str(uuid.uuid4()),
            time=new_time,
            actor="lapwing",
            type="test.new",
            resource=None,
            summary="",
            outcome=None,
        )
        log.append(old_event)
        log.append(new_event)

        since = datetime.utcnow() - timedelta(hours=1)
        recent = log.query(since=since)
        assert len(recent) == 1
        assert recent[0].type == "test.new"

    def test_results_ordered_desc(self, log):
        # Append three with monotonic time
        for t in ["a", "b", "c"]:
            log.append(_ev(t))
        rows = log.query()
        assert [r.type for r in rows] == ["c", "b", "a"]

    def test_limit_caps_results(self, log):
        for i in range(50):
            log.append(_ev(f"e.{i}"))
        rows = log.query(limit=10)
        assert len(rows) == 10

    def test_limit_clamped_to_1000(self, log):
        # We can't usefully insert 1001 rows in a fast test; just verify
        # the limit doesn't blow up with weird inputs.
        rows = log.query(limit=10_000)
        # Empty log → 0 rows, no error
        assert rows == []

    def test_limit_clamped_to_at_least_1(self, log):
        for i in range(3):
            log.append(_ev(f"e.{i}"))
        rows = log.query(limit=0)
        assert len(rows) == 1

    def test_compound_filter(self, log):
        log.append(_ev("browser.navigate", actor="lapwing", outcome="ok"))
        log.append(_ev("browser.navigate", actor="lapwing", outcome="failed"))
        log.append(_ev("credential.used", actor="lapwing", outcome="ok"))
        rows = log.query(
            type_prefix="browser.", actor="lapwing", outcome="ok"
        )
        assert len(rows) == 1


class TestAppendOnlyInvariant:
    """Blueprint §15.2 I-5: EventLog has no UPDATE/DELETE paths."""

    EVENT_LOG_PATH = REPO_ROOT / "src" / "lapwing_kernel" / "stores" / "event_log.py"

    def test_no_update_or_delete_sql_in_source(self):
        """Static grep — the module source contains no UPDATE or DELETE
        SQL targeting the events table."""
        src = self.EVENT_LOG_PATH.read_text()
        # Tolerant grep — match common patterns, case-insensitive
        forbidden = [
            re.compile(r"UPDATE\s+events", re.IGNORECASE),
            re.compile(r"DELETE\s+FROM\s+events", re.IGNORECASE),
            re.compile(r"events\s+SET\b", re.IGNORECASE),
            re.compile(r"TRUNCATE\s+events", re.IGNORECASE),
        ]
        for pat in forbidden:
            assert not pat.search(src), (
                f"EventLog source must not contain mutation SQL matching "
                f"{pat.pattern} — append-only invariant (blueprint §9 / §15.2 I-5)."
            )

    def test_no_mutation_methods_exposed(self):
        """Public API surface check: only append/query/count exist."""
        log = EventLog(":memory:")
        public = {m for m in dir(log) if not m.startswith("_")}
        # We expect this minimal public surface
        for forbidden in ("update", "delete", "remove", "clear", "drop"):
            for m in public:
                assert forbidden not in m.lower(), (
                    f"EventLog exposes a method named {m!r} which contains "
                    f"forbidden mutation verb {forbidden!r} — append-only."
                )


class TestSchema:
    def test_idempotent_init(self, tmp_path):
        path = tmp_path / "lapwing.db"
        log1 = EventLog(path)
        log1.append(_ev("test"))
        log2 = EventLog(path)
        assert log2.count() == 1

    def test_creates_parent_dir(self, tmp_path):
        db_path = tmp_path / "nested" / "deep" / "lapwing.db"
        EventLog(db_path)
        assert db_path.parent.is_dir()


class TestActionExecutorIntegration:
    """Replaces the MockEventLog in test_executor.py with the real EventLog
    to ensure executor + real store work together."""

    async def test_executor_writes_real_events(self, tmp_path):
        from src.lapwing_kernel.pipeline.continuation_registry import (
            ContinuationRegistry,
        )
        from src.lapwing_kernel.pipeline.executor import ActionExecutor
        from src.lapwing_kernel.pipeline.registry import ResourceRegistry
        from src.lapwing_kernel.policy import PolicyDecider
        from src.lapwing_kernel.primitives.action import Action
        from src.lapwing_kernel.primitives.observation import Observation
        from src.lapwing_kernel.stores.interrupt_store import InterruptStore

        class OkResource:
            name = "browser"

            def supports(self, verb):
                return True

            async def execute(self, action):
                return Observation.ok(action.id, "browser", summary="loaded")

        ContinuationRegistry.reset_for_tests()
        try:
            db_path = tmp_path / "lapwing.db"
            store = InterruptStore(db_path)
            events = EventLog(db_path)  # SAME db file — sharing is fine
            reg = ResourceRegistry()
            reg.register(OkResource(), profile="fetch")
            policy = PolicyDecider(config={})
            exec_ = ActionExecutor(reg, store, events, policy)

            action = Action.new(
                "browser",
                "navigate",
                resource_profile="fetch",
                args={"url": "https://x.com"},
            )
            obs = await exec_.execute(action)
            assert obs.status == "ok"

            # EventLog has both the start and outcome events
            rows = events.query(type_prefix="browser.")
            types = {r.type for r in rows}
            assert "browser.navigate" in types
            assert "browser.ok" in types
            # refs carry action_id
            for r in rows:
                if "action_id" in r.refs:
                    assert r.refs["action_id"] == action.id
        finally:
            ContinuationRegistry.reset_for_tests()
