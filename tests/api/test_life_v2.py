"""/api/v2/life/* endpoint tests (Phase 5 — Life v2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    mock_auth = MagicMock()
    mock_auth.api_sessions.cookie_name = "lapwing_session"
    mock_auth.validate_api_session = MagicMock(return_value=True)
    mock_auth.bootstrap_token = MagicMock(return_value="test-token")
    brain.auth_manager = mock_auth

    # Trajectory store — async list method returns [] by default
    trajectory = MagicMock()
    trajectory.list_for_timeline = AsyncMock(return_value=[])
    trajectory.recent = AsyncMock(return_value=[])
    brain.trajectory_store = trajectory

    # SoulManager — snapshot dir points somewhere that does not exist by default
    soul = MagicMock()
    soul.SNAPSHOT_DIR = MagicMock()
    soul.SNAPSHOT_DIR.exists = MagicMock(return_value=False)
    soul.SNAPSHOT_DIR.iterdir = MagicMock(return_value=[])
    brain._soul_manager_ref = soul

    # DurableScheduler — list fired reminders returns []
    scheduler = MagicMock()
    scheduler.list_fired = AsyncMock(return_value=[])
    brain._durable_scheduler_ref = scheduler

    # LLM router — unused in scaffold smoke test
    brain.router = MagicMock()

    return brain


@pytest.fixture
def client(mock_brain, tmp_path, monkeypatch):
    # Empty summaries dir so the timeline source is empty
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    monkeypatch.setattr(
        "src.api.routes.life_v2._summaries_dir_override",
        summaries,
        raising=False,
    )

    app = create_app(mock_brain, DesktopEventBus())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestLifeV2Scaffold:
    async def test_router_mounted(self, client):
        async with client:
            resp = await client.get("/api/v2/life/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


from src.core.trajectory_store import TrajectoryEntry


def _make_entry(**overrides) -> TrajectoryEntry:
    defaults = dict(
        id=1,
        timestamp=1_776_498_000.0,
        entry_type="assistant_text",
        source_chat_id="desktop:kevin",
        actor="lapwing",
        content={"text": "等我看一下"},
        related_commitment_id=None,
        related_iteration_id="iter_abc",
        related_tool_call_id=None,
    )
    defaults.update(overrides)
    return TrajectoryEntry(**defaults)


@pytest.mark.asyncio
class TestLifeV2Trajectory:
    async def test_returns_items_with_metadata(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=2, timestamp=2000.0, entry_type="inner_thought", content={"text": "想 Kevin"}),
            _make_entry(id=1, timestamp=1000.0, entry_type="user_message", content={"text": "hi"}, actor="user"),
        ])

        async with client:
            resp = await client.get("/api/v2/life/trajectory")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["kind"] == "inner_thought"
        assert data["items"][0]["id"] == "traj_2"
        assert data["items"][0]["timestamp"] == 2000.0
        assert data["items"][0]["content"] == "想 Kevin"
        assert data["items"][0]["metadata"]["actor"] == "lapwing"
        assert data["items"][0]["metadata"]["source_chat_id"] == "desktop:kevin"
        assert data["items"][0]["metadata"]["related_iteration_id"] == "iter_abc"

    async def test_limit_forwarded(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get("/api/v2/life/trajectory", params={"limit": 25})

        kwargs = spy.call_args.kwargs
        assert kwargs["limit"] == 25

    async def test_limit_capped(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            resp = await client.get("/api/v2/life/trajectory", params={"limit": 9999})

        assert resp.status_code == 422  # pydantic le=500

    async def test_entry_types_filter_parsed(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get(
                "/api/v2/life/trajectory",
                params={"entry_types": "assistant_text,user_message"},
            )

        types = spy.call_args.kwargs["entry_types"]
        assert [t.value for t in types] == ["assistant_text", "user_message"]

    async def test_bad_entry_type_returns_400(self, client, mock_brain):
        async with client:
            resp = await client.get(
                "/api/v2/life/trajectory",
                params={"entry_types": "not_a_real_type"},
            )
        assert resp.status_code == 400

    async def test_store_unavailable_returns_empty(self, client, mock_brain):
        mock_brain.trajectory_store = None
        # Re-init the route module to pick up the None store.
        from src.api.routes import life_v2
        life_v2.init(trajectory_store=None)

        async with client:
            resp = await client.get("/api/v2/life/trajectory")

        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_before_ts": None}


from src.core.trajectory_store import TrajectoryEntryType


@pytest.mark.asyncio
class TestLifeV2TimelineTrajectory:
    async def test_basic_shape(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=3, timestamp=3000.0, entry_type="assistant_text", content={"text": "ok"}),
            _make_entry(id=2, timestamp=2000.0, entry_type="user_message", actor="user", content={"text": "hi"}),
            _make_entry(id=1, timestamp=1000.0, entry_type="inner_thought", content={"text": "想他"}),
        ])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["items"][0]["timestamp"] == 3000.0  # DESC order
        assert data["total_in_window"] == 3

    async def test_include_inner_thought_false_excludes_from_trajectory_call(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get(
                "/api/v2/life/timeline",
                params={"include_inner_thought": "false"},
            )

        types = spy.call_args.kwargs["entry_types"]
        assert types is not None
        assert TrajectoryEntryType.INNER_THOUGHT not in types

    async def test_entry_types_param_passthrough(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get(
                "/api/v2/life/timeline",
                params={"entry_types": "assistant_text"},
            )

        types = spy.call_args.kwargs["entry_types"]
        assert types == [TrajectoryEntryType.ASSISTANT_TEXT]

    async def test_next_before_ts_when_more_pages(self, client, mock_brain):
        rows = [
            _make_entry(id=i, timestamp=float(100 - i), entry_type="user_message", actor="user")
            for i in range(50)
        ]
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=rows)

        async with client:
            resp = await client.get("/api/v2/life/timeline", params={"limit": 50})

        data = resp.json()
        # Trajectory returned exactly limit rows → we assume more pages.
        assert data["next_before_ts"] == data["items"][-1]["timestamp"]

    async def test_next_before_ts_null_when_fewer(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=1, timestamp=500.0),
        ])

        async with client:
            resp = await client.get("/api/v2/life/timeline", params={"limit": 50})

        assert resp.json()["next_before_ts"] is None


from datetime import datetime, timezone


def _write_summary(dir_path, name: str, body: str = "abc"):
    p = dir_path / name
    p.write_text(f"# 对话摘要 x\n\n{body}\n", encoding="utf-8")
    return p


@pytest.mark.asyncio
class TestLifeV2TimelineSummaries:
    async def test_summary_is_merged(self, client, mock_brain, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        _write_summary(summaries, "2026-04-18_074024.md", body="aaa")

        # Point route module at our temp dir (fixture already did, but re-apply for clarity)
        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        items = resp.json()["items"]
        summary_items = [i for i in items if i["kind"] == "summary"]
        assert len(summary_items) == 1
        expected_ts = datetime(2026, 4, 18, 7, 40, 24, tzinfo=timezone.utc).timestamp()
        assert summary_items[0]["timestamp"] == expected_ts
        assert summary_items[0]["id"].startswith("summary_")
        assert summary_items[0]["metadata"]["date"] == "2026-04-18"
        assert summary_items[0]["metadata"]["char_count"] == len("aaa\n") + len("# 对话摘要 x\n\n")

    async def test_bad_filename_skipped(self, client, mock_brain, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        _write_summary(summaries, "junk.md")
        _write_summary(summaries, "2026-04-18_074024.md")

        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        summary_items = [i for i in resp.json()["items"] if i["kind"] == "summary"]
        assert len(summary_items) == 1  # junk.md skipped, no exception

    async def test_summaries_merged_in_timestamp_order(self, client, mock_brain, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        _write_summary(summaries, "2026-04-18_074024.md")

        summary_ts = datetime(2026, 4, 18, 7, 40, 24, tzinfo=timezone.utc).timestamp()
        # Traj row immediately after the summary
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=1, timestamp=summary_ts + 10, content={"text": "after"}),
            _make_entry(id=2, timestamp=summary_ts - 10, content={"text": "before"}),
        ])

        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        kinds = [i["kind"] for i in resp.json()["items"]]
        assert kinds == ["assistant_text", "summary", "assistant_text"]


import json


def _write_snapshot(dir_path, stem: str, meta: dict):
    (dir_path / f"{stem}.md").write_text("# snapshot body", encoding="utf-8")
    (dir_path / f"{stem}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.mark.asyncio
class TestLifeV2TimelineSoulRevision:
    async def test_soul_snapshot_merged(self, client, mock_brain, tmp_path, monkeypatch):
        snap_dir = tmp_path / "soul_snapshots"
        snap_dir.mkdir()
        _write_snapshot(
            snap_dir,
            "soul_20260418_080000_000000",
            {
                "timestamp": "2026-04-18T08:00:00+00:00",
                "actor": "kevin",
                "trigger": "manual edit",
                "diff_summary": "+3 lines, -1 lines",
            },
        )

        # Point soul_manager at our tmp snapshot dir
        mock_brain._soul_manager_ref.SNAPSHOT_DIR = snap_dir
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        snap_items = [i for i in resp.json()["items"] if i["kind"] == "soul_revision"]
        assert len(snap_items) == 1
        assert snap_items[0]["metadata"]["actor"] == "kevin"
        assert snap_items[0]["metadata"]["trigger"] == "manual edit"
        assert snap_items[0]["metadata"]["diff_summary"] == "+3 lines, -1 lines"
        assert snap_items[0]["id"] == "snapshot_soul_20260418_080000_000000"

    async def test_snapshot_dir_missing_is_silent(self, client, mock_brain, tmp_path):
        mock_brain._soul_manager_ref.SNAPSHOT_DIR = tmp_path / "does_not_exist"
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        assert resp.status_code == 200  # no 500

    async def test_bad_meta_json_is_skipped(self, client, mock_brain, tmp_path):
        snap_dir = tmp_path / "soul_snapshots"
        snap_dir.mkdir()
        (snap_dir / "soul_bad.meta.json").write_text("not-valid-json", encoding="utf-8")
        _write_snapshot(
            snap_dir,
            "soul_20260418_080000_000000",
            {"timestamp": "2026-04-18T08:00:00+00:00", "actor": "kevin", "trigger": "x", "diff_summary": ""},
        )

        mock_brain._soul_manager_ref.SNAPSHOT_DIR = snap_dir
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        snap_items = [i for i in resp.json()["items"] if i["kind"] == "soul_revision"]
        assert len(snap_items) == 1


def _iso_utc_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@pytest.mark.asyncio
class TestLifeV2TimelineReminderFired:
    async def test_fired_reminder_merged(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])
        mock_brain._durable_scheduler_ref.list_fired = AsyncMock(return_value=[
            {
                "reminder_id": "rem_abc",
                "due_time": "2026-04-18T08:00:00+08:00",
                "content": "喝水",
                "execution_mode": "notify",
                "fired": 1,
            },
        ])

        async with client:
            resp = await client.get("/api/v2/life/timeline")

        fired_items = [i for i in resp.json()["items"] if i["kind"] == "reminder_fired"]
        assert len(fired_items) == 1
        assert fired_items[0]["id"] == "rem_abc"
        assert fired_items[0]["content"] == "喝水"
        assert fired_items[0]["metadata"]["execution_mode"] == "notify"
        # Converted to UTC float ts correctly (08:00+08:00 == 00:00 UTC)
        expected_ts = datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        assert fired_items[0]["timestamp"] == expected_ts

    async def test_merge_cutoff_total_sorts_desc(self, client, mock_brain):
        # 3 fired reminders + 3 traj rows interleaved → final 6 items must be sorted DESC
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=1, timestamp=100.0),
            _make_entry(id=2, timestamp=300.0),
            _make_entry(id=3, timestamp=500.0),
        ])
        mock_brain._durable_scheduler_ref.list_fired = AsyncMock(return_value=[
            {"reminder_id": "r1", "due_time": _iso_utc_from_ts(200.0), "content": "a", "execution_mode": "notify"},
            {"reminder_id": "r2", "due_time": _iso_utc_from_ts(400.0), "content": "b", "execution_mode": "notify"},
            {"reminder_id": "r3", "due_time": _iso_utc_from_ts(600.0), "content": "c", "execution_mode": "notify"},
        ])

        async with client:
            resp = await client.get("/api/v2/life/timeline", params={"limit": 10})

        timestamps = [i["timestamp"] for i in resp.json()["items"]]
        assert timestamps == sorted(timestamps, reverse=True)
        assert len(timestamps) == 6


import time as _time


@pytest.mark.asyncio
class TestLifeV2InnerState:
    async def test_returns_latest_inner_thought(self, client, mock_brain):
        now = _time.time()
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(
                id=99,
                timestamp=now - 4000,
                entry_type="inner_thought",
                content={"text": "想念 Kevin"},
                source_chat_id="__inner__",
            ),
        ])

        async with client:
            resp = await client.get("/api/v2/life/inner-state")

        data = resp.json()
        assert data["content"] == "想念 Kevin"
        assert data["timestamp"] == now - 4000
        assert 3990 < data["age_seconds"] < 4100
        assert data["has_recent"] is False  # > 1h old

    async def test_recent_flag_true_when_under_one_hour(self, client, mock_brain):
        now = _time.time()
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(
                id=99, timestamp=now - 60,
                entry_type="inner_thought", content={"text": "刚想的"},
                source_chat_id="__inner__",
            ),
        ])

        async with client:
            resp = await client.get("/api/v2/life/inner-state")
        assert resp.json()["has_recent"] is True

    async def test_no_inner_thought_returns_nulls(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/inner-state")

        assert resp.json() == {
            "content": None,
            "timestamp": None,
            "age_seconds": None,
            "has_recent": False,
        }

    async def test_store_unavailable(self, client, mock_brain):
        mock_brain.trajectory_store = None
        from src.api.routes import life_v2
        life_v2.init(trajectory_store=None)

        async with client:
            resp = await client.get("/api/v2/life/inner-state")
        assert resp.json()["has_recent"] is False


@pytest.mark.asyncio
class TestLifeV2SummariesEndpoint:
    async def test_basic_pagination(self, client, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        for name in ("2026-04-18_100000.md", "2026-04-17_090000.md", "2026-04-16_080000.md"):
            _write_summary(summaries, name, body="body")

        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        async with client:
            resp = await client.get("/api/v2/life/summaries", params={"limit": 2})

        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["items"][0]["date"] == "2026-04-18"
        assert data["items"][1]["date"] == "2026-04-17"
        assert data["next_before_date"] == "2026-04-17"

    async def test_before_date(self, client, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        for name in ("2026-04-18_100000.md", "2026-04-17_090000.md", "2026-04-16_080000.md"):
            _write_summary(summaries, name)
        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        async with client:
            resp = await client.get(
                "/api/v2/life/summaries",
                params={"limit": 5, "before_date": "2026-04-18"},
            )

        dates = [i["date"] for i in resp.json()["items"]]
        assert dates == ["2026-04-17", "2026-04-16"]

    async def test_empty_dir(self, client, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(exist_ok=True)
        from src.api.routes import life_v2
        monkeypatch.setattr(life_v2, "_summaries_dir_override", summaries)

        async with client:
            resp = await client.get("/api/v2/life/summaries")
        assert resp.json() == {"items": [], "next_before_date": None, "total": 0}


@pytest.mark.asyncio
class TestLifeV2TodayToneEmpty:
    async def test_no_thoughts_returns_null(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[])

        async with client:
            resp = await client.get("/api/v2/life/today-tone")

        assert resp.json() == {"tone": None, "generated_at": None, "based_on_count": 0}

    async def test_llm_router_unavailable_returns_null(self, client, mock_brain):
        # Even if there are thoughts, without a router we can't generate.
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=1, timestamp=_time.time(), entry_type="inner_thought",
                        content={"text": "想 Kevin"}, source_chat_id="__inner__"),
        ])
        mock_brain.router = None

        from src.api.routes import life_v2
        life_v2.init(
            trajectory_store=mock_brain.trajectory_store,
            llm_router=None,
            summaries_dir=None,
        )

        async with client:
            resp = await client.get("/api/v2/life/today-tone")

        assert resp.json()["tone"] is None
