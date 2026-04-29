"""Step 5 M3 — overdue 承诺通过 StateViewBuilder 注入 inner tick prompt。

集成端到端：CommitmentStore 写入 → StateViewBuilder.build_for_inner →
StateView.commitments_active 标记 is_overdue → state_serializer 渲染
⚠️ 警告段。

确保 inner tick 看到的 prompt 必然包含 overdue 信息——不是过滤、是
结构性可见。
"""
from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.commitments import CommitmentStore
from src.core.state_serializer import serialize
from src.core.state_view_builder import StateViewBuilder
from src.logging.state_mutation_log import StateMutationLog


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


def _builder(commitment_store, tmp_path: Path) -> StateViewBuilder:
    """构造一个最小可用的 StateViewBuilder：只关心 commitments，identity
    用空文件，trajectory 用 mock 返回空。"""
    soul = tmp_path / "soul.md"
    constitution = tmp_path / "constitution.md"
    soul.write_text("SOUL", encoding="utf-8")
    constitution.write_text("CONST", encoding="utf-8")

    attention_manager = MagicMock()
    attention_manager.snapshot.return_value = MagicMock(
        current_conversation=None, mode="idle"
    )

    builder = StateViewBuilder(
        soul_path=soul,
        constitution_path=constitution,
        voice_prompt_name="lapwing_voice",
        attention_manager=attention_manager,
        trajectory_store=None,
        commitment_store=commitment_store,
        task_store=None,
        reminder_source=None,
    )
    return builder


@pytest.mark.asyncio
class TestOverdueSurfacing:
    async def test_overdue_promise_surfaces_in_inner_view(self, stores, tmp_path):
        cs, _ = stores
        builder = _builder(cs, tmp_path)

        # 一个超时承诺
        cid_overdue = await cs.create(
            "chat-x", "查道奇比赛", source_trajectory_entry_id=0,
            deadline=time.time() - 30.0,
        )
        # 一个未到期承诺
        cid_active = await cs.create(
            "chat-x", "下周一帮 Kevin 整理日程", source_trajectory_entry_id=0,
            deadline=time.time() + 86400.0,
        )

        view = await builder.build_for_inner()

        # 两个都在 commitments_active 里
        ids = {c.id for c in view.commitments_active if c.kind == "promise"}
        assert cid_overdue in ids
        assert cid_active in ids

        # is_overdue 标记正确
        by_id = {c.id: c for c in view.commitments_active if c.kind == "promise"}
        assert by_id[cid_overdue].is_overdue is True
        assert by_id[cid_active].is_overdue is False

    async def test_overdue_renders_in_serialized_prompt(self, stores, tmp_path):
        cs, _ = stores
        builder = _builder(cs, tmp_path)

        await cs.create(
            "chat-x", "查比赛结果", source_trajectory_entry_id=0,
            deadline=time.time() - 60.0,
        )

        view = await builder.build_for_inner()
        out = serialize(view)

        assert "已超时的承诺" in out.system_prompt
        assert "超时未完成：查比赛结果" in out.system_prompt

    async def test_no_overdue_no_warning_section(self, stores, tmp_path):
        cs, _ = stores
        builder = _builder(cs, tmp_path)

        await cs.create(
            "chat-x", "未到期的事", source_trajectory_entry_id=0,
            deadline=time.time() + 600.0,
        )

        view = await builder.build_for_inner()
        out = serialize(view)

        # 注：voice.md 教学文本里也提到 "已超时的承诺" 这个状态名，所以
        # 不能简单 assert 子串不存在。改为检查序列化器生成的完整段落
        # 头部标识——只有有 overdue 时才会出现。
        assert "已超时的承诺（必须处理：" not in out.system_prompt
        assert "⚠️ 超时未完成：" not in out.system_prompt
        assert "我对用户的承诺" in out.system_prompt

    async def test_promise_without_deadline_never_overdue(self, stores, tmp_path):
        """deadline=NULL 的承诺永远不算超时。"""
        cs, _ = stores
        builder = _builder(cs, tmp_path)

        await cs.create(
            "chat-x", "无期限的事", source_trajectory_entry_id=0,
        )

        view = await builder.build_for_inner()
        promises = [c for c in view.commitments_active if c.kind == "promise"]
        assert len(promises) == 1
        assert promises[0].is_overdue is False

    async def test_overdue_filtered_when_status_changes(self, stores, tmp_path):
        """fulfill 后 overdue 不再出现。"""
        from src.core.commitments import CommitmentStatus
        cs, _ = stores
        builder = _builder(cs, tmp_path)

        cid = await cs.create(
            "chat-x", "已经做完了", source_trajectory_entry_id=0,
            deadline=time.time() - 10.0,
        )
        await cs.set_status(cid, CommitmentStatus.FULFILLED.value)

        view = await builder.build_for_inner()
        promises = [c for c in view.commitments_active if c.kind == "promise"]
        # fulfilled → list_open 不返回 → 不出现在 view 里
        assert promises == []
