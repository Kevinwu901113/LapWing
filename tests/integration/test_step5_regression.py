"""Step 5 M4 — 回归测试，覆盖 Step 2 捕获的真实 failure cases。

这些 case 来自 Step 2 的行为审计（conv#1907 等真实对话失败片段）。
Step 5 的目标就是把这类失败从口头文化（voice.md 描述）变成结构性
不可能（架构契约）。

每个 case 用 mock LLM 返回预设 tool_calls 序列，验证 tell_user /
commitment / brain 的协作产生正确结果——不依赖真实 MiniMax 行为。
真实 LLM 行为的端到端验证在 scripts/smoke_test_step5.py（手动跑）。
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.core.commitments import CommitmentStatus, CommitmentStore
from src.logging.state_mutation_log import StateMutationLog
from src.tools.commitments import (
    abandon_promise_executor,
    commit_promise_executor,
    fulfill_promise_executor,
)
from src.tools.tell_user import tell_user_executor
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
)


@pytest.fixture
async def stores(tmp_path: Path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    cs = CommitmentStore(tmp_path / "lapwing.db", log)
    await cs.init()
    yield cs, log
    await cs.close()
    await log.close()


def _make_ctx(
    *, send_fn, services: dict, chat_id: str = "chat-test",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        adapter="qq",
        user_id="user1",
        auth_level=2,
        chat_id=chat_id,
        send_fn=send_fn,
    )


# ── Case 1: Ghost task — "等我查一下" 必须伴随 commit_promise + 工具调用 ──

@pytest.mark.asyncio
class TestCase1GhostTask:
    """conv#1907 "等我查一下" → 不查 → 默默没下文。

    Step 5 契约：tell_user("等我查") 单独存在不构成 ghost task——只有
    LLM 同时调 commit_promise 和真实搜索工具，承诺才被登记+执行。
    本测试模拟 LLM 正确遵守契约的场景。
    """

    async def test_search_promise_creates_commitment_and_runs_tool(self, stores):
        cs, _ = stores
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        services = {"commitment_store": cs, "tell_user_buffer": []}
        ctx = _make_ctx(send_fn=send_fn, services=services)

        # 1. tell_user("等我查一下")
        await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user", arguments={"text": "等我查一下"},
            ),
            ctx,
        )
        # 2. commit_promise（注册要做的事 + deadline）
        cp = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={"description": "查杭钢股份股价", "deadline_minutes": 5},
            ),
            ctx,
        )
        promise_id = cp.payload["promise_id"]

        # 3. （省略真实工具调用，假设 web_search 已执行）
        # 4. 报告结果 + fulfill_promise
        await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user", arguments={"text": "杭钢股份今天涨了 2%"},
            ),
            ctx,
        )
        await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={
                    "promise_id": promise_id,
                    "result_summary": "杭钢股份 +2%",
                },
            ),
            ctx,
        )

        # 验证：
        # - 用户收到了 "等我查一下" 和结果，没有第三方文本溢出
        assert sent == ["等我查一下", "杭钢股份今天涨了 2%"]
        # - 承诺被记录且已 fulfilled
        row = await cs.get(promise_id)
        assert row is not None
        assert row.status == CommitmentStatus.FULFILLED.value
        assert row.closing_note == "杭钢股份 +2%"

    async def test_search_failure_path_abandons_with_user_facing_reason(self, stores):
        """搜索失败时，tell_user 通知用户 + abandon_promise 关闭。"""
        cs, _ = stores
        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        services = {"commitment_store": cs, "tell_user_buffer": []}
        ctx = _make_ctx(send_fn=send_fn, services=services)

        await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user", arguments={"text": "等我查一下"},
            ),
            ctx,
        )
        cp = await commit_promise_executor(
            ToolExecutionRequest(
                name="commit_promise",
                arguments={"description": "查冷门数据"},
            ),
            ctx,
        )
        # 搜索失败的告知（先告诉用户，再 abandon）
        await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user", arguments={"text": "查了一下没查到"},
            ),
            ctx,
        )
        await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user", arguments={"text": "等会再试试"},
            ),
            ctx,
        )
        await abandon_promise_executor(
            ToolExecutionRequest(
                name="abandon_promise",
                arguments={
                    "promise_id": cp.payload["promise_id"],
                    "reason": "搜索失败，已告知用户",
                },
            ),
            ctx,
        )

        # 用户被告知，且承诺被显式放弃（不是 ghost）
        assert "等我查一下" in sent
        assert "查了一下没查到" in sent
        row = await cs.get(cp.payload["promise_id"])
        assert row is not None
        assert row.status == CommitmentStatus.ABANDONED.value
        assert row.closing_note == "搜索失败，已告知用户"


# ── Case 2: Hallucination "走神了" — 裸文本不会发出 ───────────────────

@pytest.mark.asyncio
class TestCase2HallucinationFiltered:
    """LLM 输出 "走神了" 这种 meta 文本是 inner_monologue，不能发给用户。

    Step 5 结构性保证：bare text 在 brain.think_conversational 里走
    on_inner_monologue 写入 trajectory，永远不到 send_fn。
    本测试覆盖 brain 这一层的契约——见 tests/core/test_brain_split.py。
    """

    async def test_bare_text_never_reaches_send_fn_via_tell_user_only(self, stores):
        """如果模型完全没调 tell_user（只输出裸文本），用户什么都收不到。"""
        from unittest.mock import patch

        from src.core.brain import _ThinkCtx, LapwingBrain

        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"):
            brain = LapwingBrain(db_path=Path("test.db"))
        brain.fact_extractor = AsyncMock()
        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.append = AsyncMock(return_value=1)

        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply=None,
        )

        # 模拟 LLM 返回 "走神了" 这种 meta 文本，但**没调 tell_user**
        async def fake_complete_chat(chat_id, messages, user_msg, **kwargs):
            on_inner = kwargs.get("on_interim_text")
            if on_inner:
                await on_inner("走神了")
            return "走神了"

        sent: list[str] = []

        async def send_fn(text: str) -> None:
            sent.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete_chat):
            result = await brain.think_conversational(
                "chat-x", "hi", send_fn=send_fn,
            )

        # 用户从没被告知 "走神了"——它是 inner monologue，写 trajectory
        assert sent == []
        assert result == ""  # tell_user_buffer 空 → memory_text 空


# ── Case 3: Silent tick — inner tick 返回空字符串不污染状态 ──────────

@pytest.mark.asyncio
class TestCase3SilentTick:
    """inner tick 返回 ""（无 tell_user 调用）应该是合法的"什么都不做"。

    Step 5：tell_user 在 inner tick 上下文中 send_fn=None，会失败软。
    inner tick 不会因为 LLM 没说话而崩。
    """

    async def test_tell_user_in_inner_tick_fails_gracefully(self):
        # inner tick 上下文：send_fn=None
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={},
            adapter="",
            user_id="",
            auth_level=2,
            chat_id="_inner_tick",
            send_fn=None,
        )
        result = await tell_user_executor(
            ToolExecutionRequest(
                name="tell_user",
                arguments={"text": "想发但没用户通道"},
            ),
            ctx,
        )
        # 工具返回 success=False 但不抛异常
        assert result.success is False
        assert result.payload["delivered"] is False
        assert "用户通道" in result.payload["reason"] or "send_fn" in result.payload["reason"]


# ── Case 4: Commitment hallucination — 不能凭空声称承诺 ───────────────

@pytest.mark.asyncio
class TestCase4CommitmentTrustWorthy:
    """Step 3 §9 预测的失败模式：LLM 在 prompt 里看到 commitments 列表，
    可能虚构没真发生过的承诺。

    Step 5 不直接拦截"声称"——但 commitment_store 是唯一真相来源：
    只要工具不被调用，commit_promise mutation 就不存在，回看时一目了然。
    本测试验证：如果 LLM 试图 fulfill 一个不存在的 promise_id，工具会失败。
    """

    async def test_fulfill_with_invented_id_fails(self, stores):
        cs, _ = stores
        services = {"commitment_store": cs}
        ctx = _make_ctx(send_fn=AsyncMock(), services=services)

        # 模型瞎编一个 promise_id
        result = await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={
                    "promise_id": "definitely_made_up_id",
                    "result_summary": "假装我做完了",
                },
            ),
            ctx,
        )
        assert result.success is False
        assert "找不到" in result.payload["reason"]

    async def test_abandon_with_invented_id_fails(self, stores):
        cs, _ = stores
        services = {"commitment_store": cs}
        ctx = _make_ctx(send_fn=AsyncMock(), services=services)

        result = await abandon_promise_executor(
            ToolExecutionRequest(
                name="abandon_promise",
                arguments={
                    "promise_id": "fictitious",
                    "reason": "假装放弃",
                },
            ),
            ctx,
        )
        assert result.success is False
        assert "找不到" in result.payload["reason"]


# ── Case 5: 虚假状态汇报 — overdue 承诺暴露 "在看了" 的谎 ──────────────

@pytest.mark.asyncio
class TestCase5OverdueExposesFalseStatus:
    """场景：用户催促一个已经超时的任务。如果 Lapwing 说 "在看了" 但
    commitment 还在 open + overdue，prompt 里的 ⚠️ 让她（在下次 inner
    tick）必然看到这个谎话。

    Step 5：list_overdue 是事实层；状态序列化层把它强制注入 prompt。
    不能假装超时承诺不存在。
    """

    async def test_overdue_promise_visible_via_list_overdue(self, stores):
        cs, _ = stores

        cid = await cs.create(
            "chat-x", "查比赛结果", source_trajectory_entry_id=0,
            deadline=time.time() - 120.0,  # 2 分钟前到期
        )

        overdue = await cs.list_overdue(time.time())
        assert any(c.id == cid for c in overdue)

        # 模拟 Lapwing 看到 overdue 决定 abandon
        await abandon_promise_executor(
            ToolExecutionRequest(
                name="abandon_promise",
                arguments={
                    "promise_id": cid,
                    "reason": "查不到，告诉用户搜索失败",
                },
            ),
            _make_ctx(send_fn=AsyncMock(), services={"commitment_store": cs}),
        )

        # 关闭后不再 overdue
        overdue_after = await cs.list_overdue(time.time())
        assert all(c.id != cid for c in overdue_after)

    async def test_pretending_to_fulfill_without_doing_it_is_recorded(self, stores):
        """如果 Lapwing 调 fulfill 但实际什么都没做（虚假完成），
        mutation log 至少留下了"在 X 时刻她声称做完了"的痕迹——
        审计可查。这不是阻止 hallucination，而是让它可观测。"""
        cs, log = stores

        cid = await cs.create(
            "chat-x", "查一个东西", source_trajectory_entry_id=0,
        )
        await fulfill_promise_executor(
            ToolExecutionRequest(
                name="fulfill_promise",
                arguments={"promise_id": cid, "result_summary": "假装做完了"},
            ),
            _make_ctx(send_fn=AsyncMock(), services={"commitment_store": cs}),
        )

        from src.logging.state_mutation_log import MutationType
        muts = await log.query_by_type(MutationType.COMMITMENT_STATUS_CHANGED)
        assert len(muts) >= 1
        # closing_note 在 mutation payload 里
        assert any(
            m.payload.get("closing_note") == "假装做完了" for m in muts
        )
