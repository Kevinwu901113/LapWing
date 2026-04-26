from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.attention import AttentionManager
from src.core.focus_archiver import EpisodicArchiver, FocusArchiver
from src.core.focus_manager import FocusManager, FocusStatus
from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog
from src.memory.vector_store import VectorHit
from src.tools.focus_tools import (
    close_focus_executor,
    recall_focus_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _VectorStore:
    def __init__(self) -> None:
        self.hits: list[VectorHit] = []
        self.raise_query = False
        self.upserts: list[dict] = []

    async def upsert_collection(self, **kwargs):
        self.upserts.append(kwargs)

    async def query_collection(self, **kwargs):
        if self.raise_query:
            raise RuntimeError("vector down")
        return list(self.hits)


class _Archiver:
    def __init__(self) -> None:
        self.archived: list[dict] = []

    async def archive(self, entries, metadata):
        self.archived.append({"entries": entries, "metadata": metadata})
        return "archive_ref"

    async def retrieve(self, query, n=3):
        return []


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    yield log
    await log.close()


@pytest.fixture
async def trajectory(tmp_path, mutation_log):
    store = TrajectoryStore(tmp_path / "lapwing.db", mutation_log)
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def focus_env(tmp_path, mutation_log, trajectory):
    attention = AttentionManager(mutation_log)
    await attention.initialize()
    router = MagicMock()
    router.complete = AsyncMock(return_value='代码排错|["代码","测试","排错"]')
    vector = _VectorStore()
    extractor = MagicMock()
    extractor.extract_from_entries = AsyncMock(return_value=True)
    archiver = _Archiver()
    manager = FocusManager(
        db_path=tmp_path / "lapwing.db",
        trajectory_store=trajectory,
        attention_manager=attention,
        llm_router=router,
        vector_store=vector,
        archiver=archiver,
        episodic_extractor=extractor,
        mutation_log=mutation_log,
    )
    await manager.init_db()
    yield manager, trajectory, router, vector, extractor, attention, archiver
    await manager.close_db()


async def _append_turns(trajectory, manager, focus_id: str, chat_id: str, n: int) -> None:
    for idx in range(n):
        await trajectory.append(
            TrajectoryEntryType.USER_MESSAGE,
            chat_id,
            "user",
            {"text": f"msg {idx}"},
            focus_id=focus_id,
        )
        await manager.accumulate(focus_id)


def _make_context(focus_id=None, services=None):
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        focus_id=focus_id,
        services=services or {},
    )


# ─── Blueprint §16 Test 1 ──────────────────────────────────────────
class TestFocusLifecycle:
    async def test_create_first_focus(self, focus_env):
        manager, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "我们看一下代码")
        assert focus.status == FocusStatus.ACTIVE
        assert focus.primary_chat_id == "chat1"
        assert await manager.get_active_focus("chat1") == focus

    # §16 Test 2
    async def test_timeout_creates_new(self, focus_env):
        manager, trajectory, _router, vector, extractor, *_ = focus_env
        old = await manager.resolve_focus("chat1", "上午看代码")
        await _append_turns(trajectory, manager, old.id, "chat1", 4)
        await manager._update_focus(old.id, last_active_at=time.time() - 3600)

        new = await manager.resolve_focus("chat1", "下午查天气")

        assert new.id != old.id
        dormant = await manager.list_focuses(FocusStatus.DORMANT)
        assert [f.id for f in dormant] == [old.id]
        assert vector.upserts
        extractor.extract_from_entries.assert_awaited_once()

    # §16 Test 3
    async def test_continuous_same_focus(self, focus_env):
        manager, _trajectory, router, *_ = focus_env
        first = await manager.resolve_focus("chat1", "上午看代码")
        second = await manager.resolve_focus("chat1", "继续")
        assert second.id == first.id
        router.complete.assert_not_awaited()

    # §16 Test 4
    async def test_topic_detect_same(self, focus_env):
        manager, trajectory, router, *_ = focus_env
        router.complete = AsyncMock(return_value="SAME")
        focus = await manager.resolve_focus("chat1", "看代码")
        await _append_turns(trajectory, manager, focus.id, "chat1", 2)
        await manager._update_focus(focus.id, last_active_at=time.time() - 120)

        same = await manager.resolve_focus("chat1", "那个函数还有问题吗")
        assert same.id == focus.id

    # §16 Test 5
    async def test_topic_detect_new(self, focus_env):
        manager, trajectory, router, *_ = focus_env
        router.complete = AsyncMock(side_effect=[
            "NEW",
            '代码排错|["代码","测试","排错"]',
        ])
        old = await manager.resolve_focus("chat1", "看测试")
        await _append_turns(trajectory, manager, old.id, "chat1", 4)
        await manager._update_focus(old.id, last_active_at=time.time() - 120)

        new = await manager.resolve_focus("chat1", "晚上吃什么")
        assert new.id != old.id
        dormant = await manager.list_focuses(FocusStatus.DORMANT)
        assert [f.id for f in dormant] == [old.id]

    # §16 Test 6
    async def test_short_focus_closed_not_dormant(self, focus_env):
        manager, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "短对话")
        await manager.accumulate(focus.id)

        await manager.deactivate(focus.id)

        assert await manager.list_focuses(FocusStatus.DORMANT) == []
        closed = await manager.list_focuses(FocusStatus.CLOSED)
        assert [item.id for item in closed] == [focus.id]

    # §16 Test 7
    async def test_dormant_reactivate_embedding(self, focus_env):
        manager, trajectory, _router, vector, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "道奇比赛")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        await manager.deactivate(focus.id)
        vector.hits = [
            VectorHit(
                doc_id=focus.id,
                text="道奇比赛",
                score=0.9,
                metadata={"focus_id": focus.id},
            )
        ]

        revived = await manager.resolve_focus("chat2", "道奇结果呢")

        assert revived.id == focus.id
        assert revived.status == FocusStatus.ACTIVE
        assert revived.primary_chat_id == "chat2"

    # §16 Test 8
    async def test_dormant_reactivate_llm_fallback(self, focus_env):
        manager, trajectory, router, vector, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "聊足球")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        router.complete = AsyncMock(side_effect=[
            '足球话题|["足球","世界杯"]',
            "1",
        ])
        await manager.deactivate(focus.id)
        vector.raise_query = True

        revived = await manager.resolve_focus("chat2", "足球比赛结果")

        assert revived.id == focus.id
        assert revived.status == FocusStatus.ACTIVE

    # §16 Test 9
    async def test_dormant_match_none(self, focus_env):
        manager, trajectory, router, vector, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "聊代码")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        await manager.deactivate(focus.id)
        vector.hits = []

        new = await manager.resolve_focus("chat2", "完全不相关的话题")
        assert new.id != focus.id
        assert new.status == FocusStatus.ACTIVE

    # §16 Test 10
    async def test_dormant_max_eviction(self, focus_env):
        manager, trajectory, router, *_ = focus_env
        router.complete = AsyncMock(return_value='话题|["关键词"]')

        focus_ids = []
        for i in range(12):
            f = await manager.resolve_focus(f"chat{i}", f"话题{i}")
            await _append_turns(trajectory, manager, f.id, f"chat{i}", 5)
            await manager.deactivate(f.id)
            focus_ids.append(f.id)

        dormant = await manager.list_focuses(FocusStatus.DORMANT)
        assert len(dormant) <= 10
        closed = await manager.list_focuses(FocusStatus.CLOSED)
        assert len(closed) >= 2

    # §16 Test 11
    async def test_reap_expired_dormant(self, focus_env):
        manager, trajectory, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "老话题")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        await manager.deactivate(focus.id)
        await manager._update_focus(
            focus.id, last_active_at=time.time() - 25 * 3600,
        )

        count = await manager.reap_expired()

        assert count >= 1
        closed = await manager.list_focuses(FocusStatus.CLOSED)
        assert any(f.id == focus.id for f in closed)

    # §16 Test 12
    async def test_reap_expired_closed(self, focus_env):
        manager, *rest = focus_env
        archiver = rest[-1]
        focus = await manager.resolve_focus("chat1", "旧话题")
        await manager.close(focus.id)
        await manager._update_focus(
            focus.id, closed_at=time.time() - 80 * 3600,
        )

        count = await manager.reap_expired()

        assert count == 1
        archived = await manager.list_focuses(FocusStatus.ARCHIVED)
        assert archived[0].archive_ref_id == "archive_ref"


# ─── Blueprint §16 Tests 13-18 ─────────────────────────────────────
class TestFocusReadPathAndCleanup:
    # §16 Test 13
    async def test_load_history_by_focus(self, focus_env):
        manager, trajectory, *_ = focus_env
        one = await manager.resolve_focus("chat1", "A")
        await _append_turns(trajectory, manager, one.id, "chat1", 1)
        await manager.close(one.id)
        two = await manager.resolve_focus("chat1", "B")
        await _append_turns(trajectory, manager, two.id, "chat1", 1)

        rows = await trajectory.entries_by_focus(two.id, n=10)

        assert len(rows) == 1
        assert rows[0].focus_id == two.id
        assert rows[0].content["text"] == "msg 0"

    # §16 Test 14
    async def test_load_history_fallback(self, focus_env):
        manager, trajectory, *_ = focus_env
        await trajectory.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "old data"},
        )
        rows = await trajectory.relevant_to_chat("chat1", n=10)
        assert any(r.content["text"] == "old data" for r in rows)
        assert all(r.focus_id is None for r in rows)

    # §16 Test 15 — focus_id propagation through accumulate + trajectory
    async def test_focus_id_propagation(self, focus_env):
        manager, trajectory, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "测试传递")
        await trajectory.append(
            TrajectoryEntryType.USER_MESSAGE, "chat1", "user",
            {"text": "hello"},
            focus_id=focus.id,
        )
        await manager.accumulate(focus.id)

        rows = await trajectory.entries_by_focus(focus.id, n=10)
        assert len(rows) == 1
        assert rows[0].focus_id == focus.id

    # §16 Test 16
    async def test_accumulate_updates_count(self, focus_env):
        manager, trajectory, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "计数")
        assert focus.entry_count == 0

        await _append_turns(trajectory, manager, focus.id, "chat1", 3)

        updated = await manager.get_active_focus("chat1")
        assert updated is not None
        assert updated.entry_count == 3

    # §16 Test 17
    async def test_concurrent_chats(self, focus_env):
        manager, trajectory, *_ = focus_env
        f1 = await manager.resolve_focus("qq_kevin", "聊代码")
        f2 = await manager.resolve_focus("desktop_kevin", "查天气")

        assert f1.id != f2.id
        assert f1.primary_chat_id == "qq_kevin"
        assert f2.primary_chat_id == "desktop_kevin"
        assert (await manager.get_active_focus("qq_kevin")).id == f1.id
        assert (await manager.get_active_focus("desktop_kevin")).id == f2.id

    # §16 Test 18
    async def test_feature_flag_off(self, tmp_path, mutation_log, trajectory):
        attention = AttentionManager(mutation_log)
        await attention.initialize()
        router = MagicMock()
        router.complete = AsyncMock(return_value="NEW")
        manager = FocusManager(
            db_path=tmp_path / "lapwing.db",
            trajectory_store=trajectory,
            attention_manager=attention,
            llm_router=router,
            vector_store=_VectorStore(),
            archiver=_Archiver(),
            enabled=False,
            mutation_log=mutation_log,
        )
        await manager.init_db()

        focus = await manager.resolve_focus("chat1", "消息")
        assert focus.status == FocusStatus.ACTIVE

        second = await manager.resolve_focus("chat1", "完全不同话题")
        assert second.id != focus.id
        router.complete.assert_not_awaited()

        await manager.startup_load()
        assert manager._dormant_focuses == []

        await manager.close_db()

    def test_compactor_removed(self):
        assert not Path("src/memory/compactor.py").exists()
        import importlib.util
        assert importlib.util.find_spec("src.memory.compactor") is None


# ─── Blueprint §16 Tests 19-20 ─────────────────────────────────────
class TestStartupRecovery:
    # §16 Test 19
    async def test_startup_load(self, focus_env):
        manager, trajectory, *_ = focus_env
        f = await manager.resolve_focus("chat1", "持续话题")
        await _append_turns(trajectory, manager, f.id, "chat1", 4)

        manager._active_focuses.clear()
        manager._dormant_focuses.clear()

        await manager.startup_load()

        assert manager._active_focuses.get("chat1") == f.id

    # §16 Test 20
    async def test_startup_expired_active(self, focus_env):
        manager, trajectory, router, *_ = focus_env
        router.complete = AsyncMock(return_value='话题|["关键词"]')
        f = await manager.resolve_focus("chat1", "过期话题")
        await _append_turns(trajectory, manager, f.id, "chat1", 4)
        await manager._update_focus(f.id, last_active_at=time.time() - 7200)

        manager._active_focuses.clear()
        manager._dormant_focuses.clear()

        await manager.startup_load()

        assert "chat1" not in manager._active_focuses
        dormant = await manager.list_focuses(FocusStatus.DORMANT)
        assert any(d.id == f.id for d in dormant)


# ─── Blueprint §16 Tests 21-22 — Tool executors ────────────────────
class TestFocusTools:
    # §16 Test 21
    async def test_close_focus_tool(self, focus_env):
        manager, trajectory, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "要关闭的话题")
        await _append_turns(trajectory, manager, focus.id, "chat1", 5)

        ctx = _make_context(
            focus_id=focus.id,
            services={"focus_manager": manager},
        )
        req = ToolExecutionRequest(name="close_focus", arguments={})
        result = await close_focus_executor(req, ctx)

        assert result.success
        assert result.payload["closed"] is True
        assert await manager.get_active_focus("chat1") is None

    async def test_close_focus_no_active(self, focus_env):
        manager, *_ = focus_env
        ctx = _make_context(focus_id=None, services={"focus_manager": manager})
        req = ToolExecutionRequest(name="close_focus", arguments={})
        result = await close_focus_executor(req, ctx)
        assert result.success
        assert result.payload["closed"] is False

    # §16 Test 22
    async def test_recall_focus_tool(self, focus_env):
        manager, trajectory, _router, vector, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "道奇")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        await manager.deactivate(focus.id)
        vector.hits = [
            VectorHit(
                doc_id=focus.id,
                text="道奇",
                score=0.85,
                metadata={"focus_id": focus.id},
            ),
        ]

        ctx = _make_context(services={"focus_manager": manager})
        req = ToolExecutionRequest(name="recall_focus", arguments={"query": "道奇"})
        result = await recall_focus_executor(req, ctx)

        assert result.success
        assert len(result.payload["results"]) == 1
        assert result.payload["results"][0]["focus_id"] == focus.id

    async def test_recall_focus_empty_query(self, focus_env):
        manager, *_ = focus_env
        ctx = _make_context(services={"focus_manager": manager})
        req = ToolExecutionRequest(name="recall_focus", arguments={"query": ""})
        result = await recall_focus_executor(req, ctx)
        assert not result.success

    async def test_recall_focus_no_manager(self, focus_env):
        ctx = _make_context(services={})
        req = ToolExecutionRequest(name="recall_focus", arguments={"query": "test"})
        result = await recall_focus_executor(req, ctx)
        assert not result.success


# ─── Blueprint §16 Tests 23-24 — Episodic integration ──────────────
class TestEpisodicIntegration:
    # §16 Test 23
    async def test_episodic_triggered_on_dormant(self, focus_env):
        manager, trajectory, _router, _vector, extractor, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "话题")
        await _append_turns(trajectory, manager, focus.id, "chat1", 5)

        await manager.deactivate(focus.id)

        extractor.extract_from_entries.assert_awaited_once()
        call_args = extractor.extract_from_entries.call_args
        entries_arg = call_args[0][0] if call_args[0] else call_args[1].get("entries", [])
        assert len(entries_arg) > 0

    # §16 Test 24 — session end no longer calls episodic
    async def test_session_end_no_episodic(self, focus_env):
        _manager, _trajectory, _router, _vector, extractor, attention, *_ = focus_env
        extractor.extract_from_entries.reset_mock()

        await attention.update(current_conversation="chat1", mode="conversing")
        await attention.end_session()
        state = attention.get()
        assert state.mode == "idle"
        extractor.extract_from_entries.assert_not_awaited()


# ─── Blueprint §16 Tests 25-26 — Proactive/Inner tick focus ────────
class TestFocusOwnership:
    # §16 Test 25 — proactive message inherits active focus
    async def test_proactive_inherits_focus(self, focus_env):
        manager, trajectory, *_ = focus_env
        focus = await manager.resolve_focus("chat1", "正在聊的话题")

        await trajectory.append(
            TrajectoryEntryType.ASSISTANT_TEXT,
            "chat1",
            "lapwing",
            {"text": "主动消息"},
            focus_id=focus.id,
        )

        rows = await trajectory.entries_by_focus(focus.id, n=10)
        assert any(
            r.entry_type == TrajectoryEntryType.ASSISTANT_TEXT.value
            and r.content["text"] == "主动消息"
            for r in rows
        )

    # §16 Test 26 — inner tick entry has focus_id=NULL
    async def test_inner_tick_null_focus(self, focus_env):
        _manager, trajectory, *_ = focus_env
        await trajectory.append(
            TrajectoryEntryType.INNER_THOUGHT,
            None,
            "lapwing",
            {"text": "自由思考"},
            focus_id=None,
        )
        rows = await trajectory.relevant_to_chat(None, n=10, include_inner=True)
        inner = [r for r in rows if r.entry_type == TrajectoryEntryType.INNER_THOUGHT.value]
        assert inner
        assert inner[0].focus_id is None


# ─── Blueprint §16 Test 27 — Archiver protocol ─────────────────────
class TestArchiverProtocol:
    # §16 Test 27 — structural protocol conformance
    def test_episodic_archiver_satisfies_protocol(self):
        store = MagicMock()
        router = MagicMock()
        archiver = EpisodicArchiver(store, router)
        assert callable(getattr(archiver, "archive", None))
        assert callable(getattr(archiver, "retrieve", None))

    async def test_archiver_archive_call(self, focus_env):
        manager, trajectory, *rest = focus_env
        archiver = rest[-1]
        focus = await manager.resolve_focus("chat1", "归档话题")
        await _append_turns(trajectory, manager, focus.id, "chat1", 4)
        await manager.close(focus.id)

        await manager.archive(focus.id)

        assert archiver.archived
        assert archiver.archived[0]["metadata"]["focus_id"] == focus.id
        archived = await manager.list_focuses(FocusStatus.ARCHIVED)
        assert any(a.id == focus.id for a in archived)


# ─── Blueprint §16 Test 28 — Compactor fully removed ───────────────
class TestCompactorRemoved:
    # §16 Test 28
    def test_compactor_class_absent(self):
        assert not Path("src/memory/compactor.py").exists()

    def test_no_compactor_import_in_brain(self):
        brain_src = Path("src/core/brain.py").read_text()
        assert "compactor" not in brain_src.lower()

    def test_no_compactor_import_in_container(self):
        container_src = Path("src/app/container.py").read_text()
        assert "compactor" not in container_src.lower()

    def test_no_compactor_config(self):
        import tomllib
        with open("config.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert "compaction" not in cfg

    def test_no_compactor_test(self):
        assert not Path("tests/memory/test_compactor.py").exists()
