import pytest
from src.skills.skill_store import SkillStore


@pytest.fixture
def skill_store(tmp_path):
    return SkillStore(skills_dir=tmp_path / "skills")


class TestCreate:
    def test_create_returns_skill_id_and_path(self, skill_store):
        result = skill_store.create(
            skill_id="skill_test_hello",
            name="测试技能",
            description="简单测试",
            code='def run():\n    return {"msg": "hello"}',
        )
        assert result["skill_id"] == "skill_test_hello"
        assert result["file_path"].endswith(".md")

    def test_create_sets_draft_maturity(self, skill_store):
        skill_store.create(
            skill_id="skill_draft",
            name="草稿",
            description="测试草稿状态",
            code='def run():\n    return {}',
        )
        skill = skill_store.read("skill_draft")
        assert skill is not None
        assert skill["meta"]["maturity"] == "draft"
        assert skill["meta"]["usage_count"] == 0
        assert skill["meta"]["success_count"] == 0

    def test_create_with_dependencies_and_tags(self, skill_store):
        skill_store.create(
            skill_id="skill_with_deps",
            name="有依赖的技能",
            description="需要 requests",
            code='def run():\n    import requests',
            dependencies=["requests", "beautifulsoup4"],
            tags=["web_scraping"],
        )
        skill = skill_store.read("skill_with_deps")
        assert skill["meta"]["dependencies"] == ["requests", "beautifulsoup4"]
        assert skill["meta"]["tags"] == ["web_scraping"]

    def test_create_duplicate_id_overwrites(self, skill_store):
        skill_store.create(
            skill_id="skill_dup",
            name="原始",
            description="原始描述",
            code='def run():\n    return 1',
        )
        skill_store.create(
            skill_id="skill_dup",
            name="更新",
            description="新描述",
            code='def run():\n    return 2',
        )
        skill = skill_store.read("skill_dup")
        assert skill["meta"]["name"] == "更新"


class TestRead:
    def test_read_nonexistent_returns_none(self, skill_store):
        assert skill_store.read("skill_nope") is None

    def test_read_returns_meta_code_path(self, skill_store):
        skill_store.create(
            skill_id="skill_readable",
            name="可读技能",
            description="可以读",
            code='def run(x=1):\n    return {"x": x}',
        )
        result = skill_store.read("skill_readable")
        assert result is not None
        assert result["meta"]["id"] == "skill_readable"
        assert result["meta"]["name"] == "可读技能"
        assert "def run(x=1):" in result["code"]
        assert "file_path" in result


class TestUpdateCode:
    def test_update_code_resets_to_draft(self, skill_store):
        skill_store.create(
            skill_id="skill_upd",
            name="更新测试",
            description="会被更新",
            code='def run():\n    return 1',
        )
        skill_store.update_meta("skill_upd", maturity="testing")
        result = skill_store.update_code("skill_upd", 'def run():\n    return 2')
        assert result["success"] is True
        skill = skill_store.read("skill_upd")
        assert skill["meta"]["maturity"] == "draft"
        assert "return 2" in skill["code"]

    def test_update_code_nonexistent_fails(self, skill_store):
        result = skill_store.update_code("skill_nope", "code")
        assert result["success"] is False


class TestUpdateMeta:
    def test_update_meta_preserves_code(self, skill_store):
        skill_store.create(
            skill_id="skill_meta",
            name="元数据测试",
            description="原始描述",
            code='def run():\n    return 42',
        )
        skill_store.update_meta("skill_meta", description="新描述", tags=["test"])
        skill = skill_store.read("skill_meta")
        assert skill["meta"]["description"] == "新描述"
        assert skill["meta"]["tags"] == ["test"]
        assert "return 42" in skill["code"]

    def test_update_meta_nonexistent_fails(self, skill_store):
        result = skill_store.update_meta("skill_nope", name="x")
        assert result["success"] is False


class TestRecordExecution:
    def test_record_success_increments_counts(self, skill_store):
        skill_store.create(
            skill_id="skill_exec",
            name="执行测试",
            description="测试记录",
            code='def run():\n    return 1',
        )
        skill_store.record_execution("skill_exec", success=True)
        skill = skill_store.read("skill_exec")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 1
        assert skill["meta"]["last_error"] is None

    def test_record_failure_stores_error(self, skill_store):
        skill_store.create(
            skill_id="skill_fail",
            name="失败测试",
            description="测试失败记录",
            code='def run():\n    raise ValueError("boom")',
        )
        skill_store.record_execution("skill_fail", success=False, error="ValueError: boom")
        skill = skill_store.read("skill_fail")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 0
        assert skill["meta"]["last_error"] == "ValueError: boom"
        assert skill["meta"]["last_error_at"] is not None

    def test_record_first_success_promotes_to_testing(self, skill_store):
        skill_store.create(
            skill_id="skill_promote",
            name="升级测试",
            description="首次成功应升级",
            code='def run():\n    return 1',
        )
        result = skill_store.record_execution("skill_promote", success=True)
        assert result["meta"]["maturity"] == "testing"


class TestListSkills:
    def test_list_empty(self, skill_store):
        assert skill_store.list_skills() == []

    def test_list_all(self, skill_store):
        skill_store.create("skill_a", "A", "desc a", 'def run(): return 1')
        skill_store.create("skill_b", "B", "desc b", 'def run(): return 2')
        result = skill_store.list_skills()
        assert len(result) == 2
        ids = {s["id"] for s in result}
        assert ids == {"skill_a", "skill_b"}

    def test_list_filter_by_maturity(self, skill_store):
        skill_store.create("skill_d", "D", "draft", 'def run(): return 1')
        skill_store.create("skill_s", "S", "stable", 'def run(): return 2')
        skill_store.update_meta("skill_s", maturity="stable")
        result = skill_store.list_skills(maturity="stable")
        assert len(result) == 1
        assert result[0]["id"] == "skill_s"

    def test_list_filter_by_tag(self, skill_store):
        skill_store.create("skill_t1", "T1", "tagged", 'def run(): return 1', tags=["sports"])
        skill_store.create("skill_t2", "T2", "untagged", 'def run(): return 2')
        result = skill_store.list_skills(tag="sports")
        assert len(result) == 1
        assert result[0]["id"] == "skill_t1"


class TestGetStableSkills:
    def test_get_stable_empty(self, skill_store):
        skill_store.create("skill_d", "D", "draft", 'def run(): return 1')
        assert skill_store.get_stable_skills() == []

    def test_get_stable_returns_only_stable(self, skill_store):
        skill_store.create("skill_s", "S", "stable one", 'def run(): return 1')
        skill_store.update_meta("skill_s", maturity="stable")
        skill_store.create("skill_d", "D", "draft one", 'def run(): return 2')
        result = skill_store.get_stable_skills()
        assert len(result) == 1
        assert result[0]["meta"]["id"] == "skill_s"


class TestDelete:
    def test_delete_existing(self, skill_store):
        skill_store.create("skill_del", "Del", "deleteme", 'def run(): return 1')
        result = skill_store.delete("skill_del")
        assert result["success"] is True
        assert skill_store.read("skill_del") is None

    def test_delete_nonexistent(self, skill_store):
        result = skill_store.delete("skill_nope")
        assert result["success"] is False


class TestDirectoryFormat:
    def test_create_makes_directory_with_skill_md(self, skill_store):
        skill_store.create(
            skill_id="skill_dir_test",
            name="目录测试",
            description="测试目录格式",
            code='def run():\n    return {"ok": True}',
        )
        skill_dir = skill_store.skills_dir / "skill_dir_test"
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").exists()

    def test_create_makes_scripts_subdir(self, skill_store):
        skill_store.create(
            skill_id="skill_subdir",
            name="子目录测试",
            description="scripts 子目录",
            code='def run():\n    return {}',
        )
        assert (skill_store.skills_dir / "skill_subdir" / "scripts").is_dir()

    def test_read_from_directory(self, skill_store):
        skill_store.create(
            skill_id="skill_read_dir",
            name="读取测试",
            description="从目录读取",
            code='def run(x=1):\n    return {"x": x}',
        )
        result = skill_store.read("skill_read_dir")
        assert result is not None
        assert result["meta"]["id"] == "skill_read_dir"
        assert "def run(x=1):" in result["code"]

    def test_new_frontmatter_fields(self, skill_store):
        skill_store.create(
            skill_id="skill_fm",
            name="Frontmatter 测试",
            description="新字段",
            code='def run():\n    return {}',
            tags=["test"],
            category="utility",
        )
        meta = skill_store.read("skill_fm")["meta"]
        assert meta["version"] == "1.0.0"
        assert meta["origin"] == "self-created"
        assert meta["category"] == "utility"
        assert meta["trust_required"] == "guest"
        assert meta["source_url"] is None
        assert meta["evolution_history"] == []

    def test_delete_removes_directory(self, skill_store):
        skill_store.create("skill_del_dir", "删", "删除目录", 'def run(): return {}')
        result = skill_store.delete("skill_del_dir")
        assert result["success"] is True
        assert not (skill_store.skills_dir / "skill_del_dir").exists()

    def test_list_skills_from_directories(self, skill_store):
        skill_store.create("skill_list_a", "A", "a", 'def run(): return 1')
        skill_store.create("skill_list_b", "B", "b", 'def run(): return 2')
        result = skill_store.list_skills()
        assert len(result) == 2
        ids = {s["id"] for s in result}
        assert ids == {"skill_list_a", "skill_list_b"}


class TestLegacyMigration:
    def test_migrate_old_single_file_on_read(self, skill_store):
        """Old-format .md file gets migrated to directory on first read."""
        old_content = """---
id: skill_legacy
name: 旧格式技能
description: 应该被迁移
maturity: testing
created_at: '2026-04-20T10:00:00+08:00'
updated_at: '2026-04-20T10:00:00+08:00'
usage_count: 5
success_count: 4
last_error: null
last_error_at: null
last_tested_at: null
dependencies: []
tags: [legacy]
author: lapwing
---
## 代码

```python
def run():
    return {"migrated": True}
```"""
        old_path = skill_store.skills_dir / "skill_legacy.md"
        old_path.write_text(old_content, encoding="utf-8")

        result = skill_store.read("skill_legacy")
        assert result is not None
        assert result["meta"]["name"] == "旧格式技能"
        assert "migrated" in result["code"]
        # Old file should be gone, directory should exist
        assert not old_path.exists()
        assert (skill_store.skills_dir / "skill_legacy" / "SKILL.md").exists()
        # New fields should be backfilled
        assert result["meta"]["version"] == "1.0.0"
        assert result["meta"]["origin"] == "self-created"

    def test_migrate_preserves_maturity_and_counts(self, skill_store):
        old_content = """---
id: skill_legacy2
name: 保持状态
description: 迁移保持元数据
maturity: stable
usage_count: 10
success_count: 9
dependencies: [requests]
tags: [web]
author: lapwing
---
## 代码

```python
def run():
    return {}
```"""
        (skill_store.skills_dir / "skill_legacy2.md").write_text(old_content, encoding="utf-8")
        result = skill_store.read("skill_legacy2")
        assert result["meta"]["maturity"] == "stable"
        assert result["meta"]["usage_count"] == 10
        assert result["meta"]["success_count"] == 9
        assert result["meta"]["dependencies"] == ["requests"]

    def test_list_includes_migrated_skills(self, skill_store):
        old_content = """---
id: skill_old_list
name: 旧列表
description: 旧格式应该出现在列表中
maturity: draft
usage_count: 0
success_count: 0
tags: []
author: lapwing
---
## 代码

```python
def run():
    return {}
```"""
        (skill_store.skills_dir / "skill_old_list.md").write_text(old_content, encoding="utf-8")
        skill_store.create("skill_new_list", "新列表", "新格式", 'def run(): return {}')
        result = skill_store.list_skills()
        ids = {s["id"] for s in result}
        assert "skill_old_list" in ids
        assert "skill_new_list" in ids


class TestSkillIndex:
    def test_get_skill_index_returns_lightweight_entries(self, skill_store):
        skill_store.create("skill_idx_a", "索引A", "描述A", 'def run(): return 1', tags=["game"])
        skill_store.create("skill_idx_b", "索引B", "描述B", 'def run(): return 2', tags=["util"])
        index = skill_store.get_skill_index()
        assert len(index) == 2
        entry = next(e for e in index if e["id"] == "skill_idx_a")
        assert entry["name"] == "索引A"
        assert entry["description"] == "描述A"
        assert entry["maturity"] == "draft"
        assert entry["tags"] == ["game"]
        # Should NOT contain code
        assert "code" not in entry

    def test_rebuild_index_creates_index_file(self, skill_store):
        skill_store.create("skill_ridx", "重建索引", "测试", 'def run(): return {}')
        skill_store.rebuild_index()
        index_path = skill_store.skills_dir / "_index.json"
        assert index_path.exists()

    def test_get_skill_index_uses_cache(self, skill_store):
        skill_store.create("skill_cache", "缓存", "测试缓存", 'def run(): return {}')
        skill_store.rebuild_index()
        # Read index from cache (should not need to scan directories)
        index = skill_store.get_skill_index()
        assert len(index) == 1
        assert index[0]["id"] == "skill_cache"

    def test_load_skill_full_returns_complete_content(self, skill_store):
        skill_store.create(
            skill_id="skill_full",
            name="完整加载",
            description="返回 SKILL.md 全文",
            code='def run():\n    return {"full": True}',
        )
        content = skill_store.load_skill_full("skill_full")
        assert "完整加载" in content
        assert "def run():" in content

    def test_load_skill_full_nonexistent(self, skill_store):
        assert skill_store.load_skill_full("skill_nope") is None
