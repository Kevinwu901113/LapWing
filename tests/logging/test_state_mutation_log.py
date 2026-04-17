"""Unit tests for StateMutationLog.

Covers Blueprint v2.0 Step 1 §6 test requirements:
  1. Basic CRUD.
  2. MutationType enum completeness (only enum members accepted).
  3. LLM_REQUEST large payload — no truncation.
  4. iteration_id correlation via query_by_iteration.
  5. Concurrent writes (multiple coroutines) don't deadlock or interleave rows.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_chat_id,
    current_iteration_id,
    iteration_context,
    new_iteration_id,
    new_request_id,
)


@pytest.fixture
async def log(tmp_path):
    store = StateMutationLog(tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs")
    await store.init()
    yield store
    await store.close()


class TestBasicCRUD:
    async def test_record_returns_monotonic_ids(self, log):
        id1 = await log.record(MutationType.SYSTEM_STARTED, {"pid": 1})
        id2 = await log.record(MutationType.SYSTEM_STARTED, {"pid": 2})
        assert id1 > 0
        assert id2 == id1 + 1

    async def test_query_by_type_round_trips_payload(self, log):
        payload = {"pid": 123, "version": "v2.0-step1", "reason": "normal_start"}
        await log.record(MutationType.SYSTEM_STARTED, payload)
        rows = await log.query_by_type(MutationType.SYSTEM_STARTED)
        assert len(rows) == 1
        assert rows[0].payload == payload
        assert rows[0].event_type == "system.started"

    async def test_query_by_window_filters_outside(self, log):
        import time

        t0 = time.time()
        await log.record(MutationType.TOOL_CALLED, {"tool": "a"})
        await log.record(MutationType.TOOL_CALLED, {"tool": "b"})
        t1 = time.time() + 1

        rows = await log.query_by_window(t0, t1)
        assert len(rows) == 2
        # window in far future returns empty
        rows_future = await log.query_by_window(t1 + 100, t1 + 200)
        assert rows_future == []

    async def test_jsonl_mirror_written(self, log, tmp_path):
        await log.record(MutationType.ITERATION_STARTED, {"iteration_id": "abc"})
        from datetime import date

        today = date.today().isoformat()
        jsonl = tmp_path / "logs" / f"mutations_{today}.log"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "iteration.started"
        assert entry["payload"]["iteration_id"] == "abc"


class TestEnumDiscipline:
    async def test_record_rejects_string_event_type(self, log):
        with pytest.raises(TypeError, match="MutationType"):
            await log.record("llm.request", {"x": 1})  # type: ignore[arg-type]

    async def test_record_rejects_unknown_enum(self, log):
        class Fake:
            value = "bogus.type"

        with pytest.raises(TypeError, match="MutationType"):
            await log.record(Fake(), {"x": 1})  # type: ignore[arg-type]

    def test_enum_covers_all_step1_event_types(self):
        required_step1 = {
            "iteration.started",
            "iteration.ended",
            "llm.request",
            "llm.response",
            "tool.called",
            "tool.result",
            "system.started",
            "system.stopped",
            "llm.hallucination_suspected",
        }
        actual = {member.value for member in MutationType}
        missing = required_step1 - actual
        assert not missing, f"missing Step 1 event types: {missing}"

    def test_enum_defines_future_types(self):
        required_future = {
            "trajectory.appended",
            "attention.changed",
            "commitment.created",
            "commitment.status_changed",
            "identity.edited",
            "memory.raptor_updated",
            "memory.file_edited",
        }
        actual = {member.value for member in MutationType}
        missing = required_future - actual
        assert not missing, f"missing future event types: {missing}"


class TestLargePayloadNoTruncation:
    async def test_llm_request_messages_stored_verbatim(self, log):
        request_id = new_request_id()
        # Simulate a large conversation — 50 messages × ~2KB each ≈ 100KB payload.
        big_messages = [
            {"role": "user", "content": "x" * 2000 + f" msg-{i}"} for i in range(50)
        ]
        big_system = "SYSTEM-PROMPT: " + ("y" * 4000)
        big_tools = [
            {
                "type": "function",
                "function": {
                    "name": f"tool_{i}",
                    "description": "z" * 500,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for i in range(10)
        ]
        payload = {
            "request_id": request_id,
            "model_slot": "chat",
            "model_name": "MiniMax-M2.7",
            "base_url": "https://api.minimaxi.com/anthropic",
            "protocol": "anthropic",
            "purpose": "main_conversation",
            "messages": big_messages,
            "system": big_system,
            "tools": big_tools,
            "max_tokens": 4096,
            "temperature": None,
        }
        mid = await log.record(MutationType.LLM_REQUEST, payload)
        assert mid > 0

        # Round-trip the exact payload
        found = await log.query_llm_request(request_id)
        assert found is not None
        assert found.payload["messages"] == big_messages  # byte-for-byte
        assert found.payload["system"] == big_system
        assert found.payload["tools"] == big_tools
        # Payload size recorded for later diagnostics
        assert found.payload_size > 100_000


class TestIterationCorrelation:
    async def test_query_by_iteration_returns_all_events_in_order(self, log):
        iid = new_iteration_id()
        request_id = new_request_id()

        await log.record(
            MutationType.ITERATION_STARTED,
            {"iteration_id": iid, "trigger_type": "user_message"},
            iteration_id=iid,
        )
        await log.record(
            MutationType.LLM_REQUEST,
            {"request_id": request_id, "messages": [], "system": ""},
            iteration_id=iid,
        )
        await log.record(
            MutationType.LLM_RESPONSE,
            {"request_id": request_id, "latency_ms": 100, "stop_reason": "end_turn"},
            iteration_id=iid,
        )
        await log.record(
            MutationType.ITERATION_ENDED,
            {"iteration_id": iid, "duration_ms": 200, "end_reason": "completed"},
            iteration_id=iid,
        )
        # An event from a different iteration must NOT appear in results
        await log.record(
            MutationType.ITERATION_STARTED,
            {"iteration_id": "other"},
            iteration_id="other",
        )

        rows = await log.query_by_iteration(iid)
        assert [r.event_type for r in rows] == [
            "iteration.started",
            "llm.request",
            "llm.response",
            "iteration.ended",
        ]
        for r in rows:
            assert r.iteration_id == iid


class TestConcurrentWrites:
    async def test_many_coroutines_record_without_loss(self, log):
        N = 40
        iid = new_iteration_id()

        async def worker(idx: int) -> int:
            return await log.record(
                MutationType.TOOL_CALLED,
                {"tool": "parallel", "idx": idx},
                iteration_id=iid,
            )

        results = await asyncio.gather(*[worker(i) for i in range(N)])
        assert len(set(results)) == N  # every record got a unique id
        rows = await log.query_by_iteration(iid)
        # All N events present
        assert len(rows) == N
        # Every idx accounted for
        seen_idx = {row.payload["idx"] for row in rows}
        assert seen_idx == set(range(N))


class TestContextVars:
    async def test_defaults_are_none(self):
        assert current_iteration_id() is None
        assert current_chat_id() is None

    async def test_iteration_context_binds_and_unbinds(self):
        assert current_iteration_id() is None
        with iteration_context("iter-1", chat_id="chat-99"):
            assert current_iteration_id() == "iter-1"
            assert current_chat_id() == "chat-99"
        assert current_iteration_id() is None
        assert current_chat_id() is None

    async def test_context_propagates_through_await(self):
        captured = {}

        async def inner():
            await asyncio.sleep(0)
            captured["iid"] = current_iteration_id()
            captured["cid"] = current_chat_id()

        with iteration_context("iter-x", chat_id="chat-y"):
            await inner()

        assert captured == {"iid": "iter-x", "cid": "chat-y"}

    async def test_nested_contexts_restore(self):
        with iteration_context("outer"):
            assert current_iteration_id() == "outer"
            with iteration_context("inner"):
                assert current_iteration_id() == "inner"
            assert current_iteration_id() == "outer"


class TestInvalidState:
    async def test_record_before_init_returns_minus_one(self, tmp_path):
        store = StateMutationLog(tmp_path / "x.db", logs_dir=tmp_path / "logs")
        # deliberately skip init()
        rid = await store.record(MutationType.SYSTEM_STARTED, {"x": 1})
        assert rid == -1

    async def test_query_before_init_returns_empty(self, tmp_path):
        store = StateMutationLog(tmp_path / "x.db", logs_dir=tmp_path / "logs")
        assert await store.query_by_iteration("x") == []
        assert await store.query_by_type(MutationType.SYSTEM_STARTED) == []
        assert await store.query_llm_request("rid") is None
