import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult
from src.skills.skill_store import SkillStore
from src.skills.skill_executor import SkillResult


def _make_ctx(*, services: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        adapter="desktop",
        user_id="kevin",
        auth_level=3,
        chat_id="chat-test",
    )


def _make_req(name: str, args: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=args)


@pytest.fixture
def skill_store(tmp_path):
    return SkillStore(skills_dir=tmp_path / "skills")


@pytest.fixture
def mock_executor():
    return MagicMock()


class TestCreateSkill:
    async def test_create_success(self, skill_store):
        from src.tools.skill_tools import create_skill_executor
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("create_skill", {
            "skill_id": "skill_test",
            "name": "测试技能",
            "description": "测试用",
            "code": 'def run():\n    return {"ok": True}',
        })
        result = await create_skill_executor(req, ctx)
        assert result.success is True
        assert result.payload["skill_id"] == "skill_test"

    async def test_create_missing_fields(self, skill_store):
        from src.tools.skill_tools import create_skill_executor
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("create_skill", {"skill_id": "skill_test"})
        result = await create_skill_executor(req, ctx)
        assert result.success is False

    async def test_create_no_store(self):
        from src.tools.skill_tools import create_skill_executor
        ctx = _make_ctx(services={})
        req = _make_req("create_skill", {
            "skill_id": "x", "name": "x", "description": "x", "code": "def run(): pass",
        })
        result = await create_skill_executor(req, ctx)
        assert result.success is False


class TestRunSkill:
    async def test_run_success(self, skill_store):
        from src.tools.skill_tools import run_skill_executor
        skill_store.create("skill_run", "运行测试", "测试", 'def run():\n    return {"x": 1}')
        mock_exec = MagicMock()
        mock_exec.execute = AsyncMock(return_value=SkillResult(
            success=True, output='{"x": 1}', error="", exit_code=0,
        ))
        ctx = _make_ctx(services={"skill_store": skill_store, "skill_executor": mock_exec})
        req = _make_req("run_skill", {"skill_id": "skill_run"})
        result = await run_skill_executor(req, ctx)
        assert result.success is True
        assert "x" in result.payload["output"]

    async def test_run_nonexistent(self, skill_store):
        from src.tools.skill_tools import run_skill_executor
        mock_exec = MagicMock()
        mock_exec.execute = AsyncMock(return_value=SkillResult(
            success=False, output="", error="技能 skill_nope 不存在", exit_code=-1,
        ))
        ctx = _make_ctx(services={"skill_store": skill_store, "skill_executor": mock_exec})
        req = _make_req("run_skill", {"skill_id": "skill_nope"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False


class TestEditSkill:
    async def test_edit_success(self, skill_store):
        from src.tools.skill_tools import edit_skill_executor
        skill_store.create("skill_ed", "编辑测试", "测试", 'def run():\n    return 1')
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("edit_skill", {
            "skill_id": "skill_ed",
            "code": 'def run():\n    return 2',
        })
        result = await edit_skill_executor(req, ctx)
        assert result.success is True
        skill = skill_store.read("skill_ed")
        assert "return 2" in skill["code"]


class TestListSkills:
    async def test_list_empty(self, skill_store):
        from src.tools.skill_tools import list_skills_executor
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("list_skills", {})
        result = await list_skills_executor(req, ctx)
        assert result.success is True
        assert result.payload["skills"] == []


class TestPromoteSkill:
    async def test_promote_success(self, skill_store):
        from src.tools.skill_tools import promote_skill_executor
        skill_store.create("skill_pro", "升级测试", "测试", 'def run():\n    return 1')
        skill_store.update_meta("skill_pro", maturity="testing")
        ctx = _make_ctx(services={"skill_store": skill_store, "tool_registry": MagicMock()})
        req = _make_req("promote_skill", {"skill_id": "skill_pro"})
        result = await promote_skill_executor(req, ctx)
        assert result.success is True
        skill = skill_store.read("skill_pro")
        assert skill["meta"]["maturity"] == "stable"

    async def test_promote_draft_fails(self, skill_store):
        from src.tools.skill_tools import promote_skill_executor
        skill_store.create("skill_draft", "草稿", "不能直接升级", 'def run():\n    return 1')
        ctx = _make_ctx(services={"skill_store": skill_store, "tool_registry": MagicMock()})
        req = _make_req("promote_skill", {"skill_id": "skill_draft"})
        result = await promote_skill_executor(req, ctx)
        assert result.success is False


class TestDeleteSkill:
    async def test_delete_success(self, skill_store):
        from src.tools.skill_tools import delete_skill_executor
        skill_store.create("skill_del", "删除测试", "测试", 'def run():\n    return 1')
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("delete_skill", {"skill_id": "skill_del"})
        result = await delete_skill_executor(req, ctx)
        assert result.success is True
        assert skill_store.read("skill_del") is None
