import pytest
from unittest.mock import MagicMock, AsyncMock
from src.skills.skill_store import SkillStore
from src.skills.skill_executor import SkillExecutor
from src.tools.skill_tools import register_skill_tools, _register_skill_as_tool
from src.tools.registry import ToolRegistry


@pytest.fixture
def skill_store(tmp_path):
    return SkillStore(skills_dir=tmp_path / "skills")


@pytest.fixture
def registry():
    return ToolRegistry()


class TestBootRegistration:
    def test_stable_skills_registered_at_boot(self, skill_store, registry):
        skill_store.create("skill_boot", "启动注册", "测试启动注册", 'def run():\n    return {}')
        skill_store.update_meta("skill_boot", maturity="stable")
        executor = SkillExecutor(skill_store=skill_store)

        for stable in skill_store.get_stable_skills():
            _register_skill_as_tool(registry, skill_store, executor, stable["meta"]["id"])

        tool = registry.get("skill_boot")
        assert tool is not None
        assert tool.capability == "skill"

    def test_non_stable_not_registered(self, skill_store, registry):
        skill_store.create("skill_draft", "草稿", "不注册", 'def run():\n    return {}')
        executor = SkillExecutor(skill_store=skill_store)

        for stable in skill_store.get_stable_skills():
            _register_skill_as_tool(registry, skill_store, executor, stable["meta"]["id"])

        assert registry.get("skill_draft") is None


class TestHotRegistration:
    def test_promote_registers_tool(self, skill_store, registry):
        skill_store.create("skill_hot", "热注册", "测试热注册", 'def run():\n    return {}')
        skill_store.update_meta("skill_hot", maturity="testing")
        executor = SkillExecutor(skill_store=skill_store)

        assert registry.get("skill_hot") is None

        skill_store.update_meta("skill_hot", maturity="stable")
        _register_skill_as_tool(registry, skill_store, executor, "skill_hot")

        tool = registry.get("skill_hot")
        assert tool is not None
        assert tool.name == "skill_hot"


class TestManagementToolsRegistered:
    def test_six_tools_registered(self, registry):
        register_skill_tools(registry)
        for name in ["create_skill", "run_skill", "edit_skill",
                      "list_skills", "promote_skill", "delete_skill"]:
            assert registry.get(name) is not None, f"{name} not registered"
