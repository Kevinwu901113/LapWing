"""Unit tests for scripts/migrate_to_trajectory.py.

Covers Blueprint v2.0 Step 2 §5 and §8.1:
  1. _map_row — all four mapping branches
  2. _parse_legacy_timestamp — valid / invalid / None
  3. scan() against a synthetic legacy fixture DB — audit counts + invariants
  4. write_staged() end-to-end + post_execute_verify equality
  5. double-run guard: second --execute refuses without --force
  6. anomaly discards: empty content / bad role / malformed ts → discards
  7. __consciousness__ rows map to INNER_THOUGHT / __inner__
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

# import target module
import scripts.migrate_to_trajectory as mig
from src.core.trajectory_store import (
    TrajectoryEntryType,
    TrajectoryStore,
)
from src.logging.state_mutation_log import StateMutationLog


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
async def legacy_db(tmp_path: Path) -> Path:
    """Build a synthetic ``conversations`` table that mimics the real schema."""
    db_path = tmp_path / "lapwing.db"
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE conversations (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   TEXT NOT NULL,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        await db.commit()
    finally:
        await db.close()
    return db_path


async def _insert(db_path: Path, rows: list[tuple]) -> None:
    db = await aiosqlite.connect(db_path)
    try:
        await db.executemany(
            "INSERT INTO conversations (chat_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        await db.commit()
    finally:
        await db.close()


async def _init_trajectory_table(db_path: Path) -> None:
    log = StateMutationLog(Path(db_path).parent / "mlog.db")
    await log.init()
    store = TrajectoryStore(db_path, log)
    await store.init()
    await store.close()
    await log.close()


# ── _map_row ──────────────────────────────────────────────────────────

class TestMapRow:
    def test_user_message(self):
        et, src, actor, content = mig._map_row(
            1, "919231551", "user", "hi", "2026-04-10T12:00:00+00:00"
        )
        assert et == TrajectoryEntryType.USER_MESSAGE.value
        assert src == "919231551"
        assert actor == "user"
        assert content == {"text": "hi"}

    def test_assistant_text(self):
        et, src, actor, content = mig._map_row(
            1, "919231551", "assistant", "hello", "2026-04-10T12:00:00+00:00"
        )
        assert et == TrajectoryEntryType.ASSISTANT_TEXT.value
        assert src == "919231551"
        assert actor == "lapwing"
        assert content == {"text": "hello"}

    def test_consciousness_assistant_to_inner_thought_lapwing(self):
        et, src, actor, content = mig._map_row(
            1, "__consciousness__", "assistant", "I should check...", "2026-04-10T12:00:00+00:00"
        )
        assert et == TrajectoryEntryType.INNER_THOUGHT.value
        assert src == "__inner__"
        assert actor == "lapwing"
        assert content["text"] == "I should check..."
        assert content["trigger_type"] == "legacy_migrated"

    def test_consciousness_user_to_inner_thought_system(self):
        et, src, actor, content = mig._map_row(
            1, "__consciousness__", "user", "timer tick", "2026-04-10T12:00:00+00:00"
        )
        assert et == TrajectoryEntryType.INNER_THOUGHT.value
        assert src == "__inner__"
        assert actor == "system"

    def test_bad_role_returns_none_tuple(self):
        et, src, actor, content = mig._map_row(
            1, "x", "tool", "?", "2026-04-10T12:00:00+00:00"
        )
        assert et is None and src is None and actor is None and content is None

    def test_empty_content_returns_none_tuple(self):
        et, src, actor, content = mig._map_row(
            1, "x", "user", "", "2026-04-10T12:00:00+00:00"
        )
        assert et is None

    def test_empty_chat_id_returns_none_tuple(self):
        et, src, actor, content = mig._map_row(
            1, "", "user", "x", "2026-04-10T12:00:00+00:00"
        )
        assert et is None


class TestParseTimestamp:
    def test_iso_with_tz(self):
        t = mig._parse_legacy_timestamp("2026-04-10T12:00:00+00:00")
        assert t is not None
        assert t == datetime(2026, 4, 10, 12, tzinfo=timezone.utc).timestamp()

    def test_iso_with_z(self):
        t = mig._parse_legacy_timestamp("2026-04-10T12:00:00Z")
        assert t is not None

    def test_malformed_returns_none(self):
        assert mig._parse_legacy_timestamp("not a date") is None

    def test_none_returns_none(self):
        assert mig._parse_legacy_timestamp(None) is None

    def test_empty_returns_none(self):
        assert mig._parse_legacy_timestamp("") is None


# ── scan() ────────────────────────────────────────────────────────────

class TestScan:
    async def test_all_rows_migrate_on_clean_fixture(self, legacy_db):
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
            ("chat_a", "assistant", "hello", "2026-04-10T12:00:01+00:00"),
            ("__consciousness__", "assistant", "thinking", "2026-04-10T12:00:02+00:00"),
        ])
        audit, staged = await mig.scan(legacy_db)
        assert audit.total == 3
        assert audit.migrated == 3
        assert audit.discarded == 0
        assert len(staged) == 3
        assert audit.by_entry_type == {
            "user_message": 1,
            "assistant_text": 1,
            "inner_thought": 1,
        }
        assert audit.by_source_chat == {"chat_a": 2, "__inner__": 1}

    async def test_invariant_holds_with_mixed_anomalies(self, legacy_db):
        await _insert(legacy_db, [
            ("chat_a", "user", "ok", "2026-04-10T12:00:00+00:00"),
            ("chat_a", "user", "", "2026-04-10T12:00:01+00:00"),  # empty
            ("chat_a", "tool", "x", "2026-04-10T12:00:02+00:00"),  # bad role
            ("chat_a", "assistant", "reply", "badtimestamp"),       # imputed ts
        ])
        audit, staged = await mig.scan(legacy_db)
        assert audit.total == 4
        assert audit.migrated == 2
        assert audit.discarded == 2
        assert audit.ts_imputed == 1
        reasons = sorted(d["reason"] for d in audit.discards)
        assert reasons == ["bad_role", "empty_content"]
        # Imputed row carries the marker
        imputed_entry = [s for s in staged if s[4].get("ts_imputed")]
        assert len(imputed_entry) == 1

    async def test_consciousness_rows_map_correctly(self, legacy_db):
        await _insert(legacy_db, [
            ("__consciousness__", "assistant", "ponder", "2026-04-10T12:00:00+00:00"),
            ("__consciousness__", "user", "tick", "2026-04-10T12:00:01+00:00"),
        ])
        audit, staged = await mig.scan(legacy_db)
        assert audit.by_entry_type == {"inner_thought": 2}
        assert audit.by_source_chat == {"__inner__": 2}
        actors = sorted(row[3] for row in staged)
        assert actors == ["lapwing", "system"]

    async def test_histogram_present(self, legacy_db):
        await _insert(legacy_db, [
            ("chat_a", "user", "1", "2026-04-10T12:00:00+00:00"),
            ("chat_a", "user", "2", "2026-04-10T12:00:01+00:00"),
            ("chat_b", "user", "3", "2026-04-10T12:00:02+00:00"),
        ])
        audit, _ = await mig.scan(legacy_db)
        assert audit.chat_histogram_legacy[0] == ("chat_a", 2)
        assert audit.chat_histogram_legacy[1] == ("chat_b", 1)


# ── write_staged() + post_execute_verify ──────────────────────────────

class TestWriteAndVerify:
    async def test_end_to_end_migration_invariants(self, legacy_db):
        await _init_trajectory_table(legacy_db)
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
            ("chat_a", "assistant", "hello", "2026-04-10T12:00:01+00:00"),
            ("__consciousness__", "assistant", "ponder", "2026-04-10T12:00:02+00:00"),
            ("chat_a", "assistant", "", "2026-04-10T12:00:03+00:00"),  # discard
        ])
        audit, staged = await mig.scan(legacy_db)
        assert audit.total == 4
        assert audit.migrated == 3
        assert audit.discarded == 1

        await mig.write_staged(legacy_db, staged)
        rows, ok = await mig.post_execute_verify(legacy_db, audit.migrated)
        assert ok
        assert rows == 3

    async def test_second_execute_without_force_refuses(self, legacy_db):
        await _init_trajectory_table(legacy_db)
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
        ])
        _, staged = await mig.scan(legacy_db)
        await mig.write_staged(legacy_db, staged)
        with pytest.raises(RuntimeError, match="refusing to double-migrate"):
            await mig.write_staged(legacy_db, staged)

    async def test_force_allows_rewrite(self, legacy_db):
        await _init_trajectory_table(legacy_db)
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
        ])
        _, staged = await mig.scan(legacy_db)
        await mig.write_staged(legacy_db, staged)
        await mig.write_staged(legacy_db, staged, force=True)
        rows, _ = await mig.post_execute_verify(legacy_db, 2)
        assert rows == 2

    async def test_missing_trajectory_table_raises(self, legacy_db):
        # legacy_db fixture has no trajectory table yet
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
        ])
        _, staged = await mig.scan(legacy_db)
        with pytest.raises(RuntimeError, match="trajectory table does not exist"):
            await mig.write_staged(legacy_db, staged)

    async def test_content_json_preserved(self, legacy_db):
        await _init_trajectory_table(legacy_db)
        await _insert(legacy_db, [
            ("chat_a", "user", "测试中文 emoji 🎉", "2026-04-10T12:00:00+00:00"),
        ])
        _, staged = await mig.scan(legacy_db)
        await mig.write_staged(legacy_db, staged)
        db = await aiosqlite.connect(legacy_db)
        try:
            async with db.execute(
                "SELECT content_json FROM trajectory WHERE entry_type = ?",
                (TrajectoryEntryType.USER_MESSAGE.value,),
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        assert row is not None
        content = json.loads(row[0])
        assert content["text"] == "测试中文 emoji 🎉"


class TestAuditPrint:
    async def test_print_report_does_not_raise(self, legacy_db, capsys):
        await _insert(legacy_db, [
            ("chat_a", "user", "hi", "2026-04-10T12:00:00+00:00"),
        ])
        audit, _ = await mig.scan(legacy_db)
        audit.print_report("DRY-RUN")
        out = capsys.readouterr().out
        assert "conversations rows read:" in out
        assert "invariant" in out
