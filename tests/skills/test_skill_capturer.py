"""tests/skills/test_skill_capturer.py — SkillCapturer 自动技能捕获测试。"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
from src.skills.skill_capturer import (
    SkillCapturer,
    _fingerprint,
    _parse_skill_response,
)


def _mk_entry(
    id_: int,
    entry_type: TrajectoryEntryType,
    content: dict,
    *,
    iteration_id: str | None = "iter_1",
    source_chat_id: str = "chat1",
    actor: str = "lapwing",
):
    return TrajectoryEntry(
        id=id_,
        timestamp=time.time() - 100 + id_,
        entry_type=entry_type.value,
        source_chat_id=source_chat_id,
        actor=actor,
        content=content,
        related_commitment_id=None,
        related_iteration_id=iteration_id,
        related_tool_call_id=None,
    )


def _make_tool_chain(
    iteration_id: str = "iter_1",
    tool_names: list[str] | None = None,
) -> list[TrajectoryEntry]:
    names = tool_names or ["execute_shell", "write_file", "execute_shell"]
    entries: list[TrajectoryEntry] = []
    entries.append(_mk_entry(
        0, TrajectoryEntryType.USER_MESSAGE,
        {"text": "帮我写个脚本"},
        iteration_id=iteration_id,
        actor="user",
    ))
    for i, name in enumerate(names):
        entries.append(_mk_entry(
            i * 2 + 1, TrajectoryEntryType.TOOL_CALL,
            {"tool_name": name, "arguments": {"command": f"step {i}"}},
            iteration_id=iteration_id,
        ))
        entries.append(_mk_entry(
            i * 2 + 2, TrajectoryEntryType.TOOL_RESULT,
            {"success": True, "output": f"ok {i}"},
            iteration_id=iteration_id,
        ))
    return entries


@pytest.fixture
def capturer():
    return SkillCapturer()


@pytest.fixture
def mock_trajectory():
    traj = AsyncMock()
    traj.in_window = AsyncMock(return_value=[])
    return traj


@pytest.fixture
def mock_skill_store(tmp_path):
    from src.skills.skill_store import SkillStore
    return SkillStore(skills_dir=tmp_path / "skills")


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.complete = AsyncMock(return_value="SKIP")
    return router


class TestExtractCandidates:
    def test_3_plus_tool_calls_identified(self, capturer):
        entries = _make_tool_chain(tool_names=["a", "b", "c"])
        candidates = capturer._extract_candidates(entries, TrajectoryEntryType)
        assert len(candidates) == 1
        assert candidates[0]["tool_count"] == 3

    def test_fewer_than_3_ignored(self, capturer):
        entries = _make_tool_chain(tool_names=["a", "b"])
        candidates = capturer._extract_candidates(entries, TrajectoryEntryType)
        assert len(candidates) == 0

    def test_no_iteration_id_ignored(self, capturer):
        entries = _make_tool_chain()
        for e in entries:
            object.__setattr__(e, "related_iteration_id", None)
        candidates = capturer._extract_candidates(entries, TrajectoryEntryType)
        assert len(candidates) == 0

    def test_multiple_iterations(self, capturer):
        chain1 = _make_tool_chain("iter_a", ["a", "b", "c"])
        chain2 = _make_tool_chain("iter_b", ["x", "y", "z", "w"])
        candidates = capturer._extract_candidates(
            chain1 + chain2, TrajectoryEntryType,
        )
        assert len(candidates) == 2
        assert candidates[0]["tool_count"] == 4


class TestFingerprint:
    def test_same_tools_same_fp(self):
        assert _fingerprint(["a", "b"]) == _fingerprint(["a", "b"])

    def test_different_tools_different_fp(self):
        assert _fingerprint(["a", "b"]) != _fingerprint(["b", "a"])


class TestDuplicateSkip:
    async def test_existing_fingerprint_skipped(
        self, capturer, mock_trajectory, mock_skill_store, mock_router,
    ):
        entries = _make_tool_chain(tool_names=["a", "b", "c"])
        mock_trajectory.in_window = AsyncMock(return_value=entries)

        fp = _fingerprint(["a", "b", "c"])
        mock_skill_store.create(
            skill_id="existing_skill",
            name="existing",
            description="test",
            code="# test",
            origin="captured",
        )
        SkillCapturer._patch_fingerprint(mock_skill_store, "existing_skill", fp)

        result = await capturer.maybe_capture_skills(
            mock_trajectory, mock_skill_store, mock_router,
        )
        assert result == []
        mock_router.complete.assert_not_awaited()


class TestLLMSkipResponse:
    async def test_skip_does_not_create(
        self, capturer, mock_trajectory, mock_skill_store, mock_router,
    ):
        entries = _make_tool_chain(tool_names=["a", "b", "c"])
        mock_trajectory.in_window = AsyncMock(return_value=entries)
        mock_router.complete = AsyncMock(return_value="SKIP")

        result = await capturer.maybe_capture_skills(
            mock_trajectory, mock_skill_store, mock_router,
        )
        assert result == []
        assert mock_skill_store.list_skills() == []


class TestSuccessfulCapture:
    async def test_creates_skill_from_llm_response(
        self, capturer, mock_trajectory, mock_skill_store, mock_router,
    ):
        entries = _make_tool_chain(tool_names=["shell", "write", "shell"])
        mock_trajectory.in_window = AsyncMock(return_value=entries)

        mock_router.complete = AsyncMock(return_value="""\
---
name: deploy-script
description: 部署脚本的标准流程
category: devops
---
## 适用场景
需要部署时

## 步骤
1. 执行 shell 检查
2. 写入配置
3. 执行部署

## 容易出错的地方
忘记检查环境

## 验证方法
curl 健康检查
""")

        result = await capturer.maybe_capture_skills(
            mock_trajectory, mock_skill_store, mock_router,
        )
        assert len(result) == 1
        skill_id = result[0]
        skill = mock_skill_store.read(skill_id)
        assert skill is not None
        assert skill["meta"]["maturity"] == "draft"
        assert skill["meta"]["origin"] == "captured"

    async def test_max_captures_per_run(
        self, capturer, mock_trajectory, mock_skill_store, mock_router,
    ):
        chains = []
        for i in range(5):
            chains.extend(
                _make_tool_chain(f"iter_{i}", [f"t{i}_{j}" for j in range(4)])
            )
        mock_trajectory.in_window = AsyncMock(return_value=chains)
        mock_router.complete = AsyncMock(return_value="""\
---
name: test-skill
description: test
category: general
---
## 步骤
do stuff
""")

        result = await capturer.maybe_capture_skills(
            mock_trajectory, mock_skill_store, mock_router,
        )
        assert len(result) <= 3


class TestParseSkillResponse:
    def test_skip(self):
        assert _parse_skill_response("SKIP") is None
        assert _parse_skill_response("  skip  ") is None

    def test_valid(self):
        resp = """\
---
name: test
description: a test
category: general
---
## 步骤
do stuff"""
        parsed = _parse_skill_response(resp)
        assert parsed is not None
        assert parsed["name"] == "test"
        assert "步骤" in parsed["body"]

    def test_no_frontmatter(self):
        assert _parse_skill_response("just text without frontmatter") is None

    def test_missing_name(self):
        resp = "---\ndescription: no name\n---\nbody"
        assert _parse_skill_response(resp) is None
