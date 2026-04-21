# Skill Growth Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Lapwing learn executable skills from experience — write code, test it in a Docker sandbox, promote stable skills to first-class tools.

**Architecture:** SkillStore (YAML frontmatter + markdown, mirrors NoteStore) manages skill files under `data/skills/`. SkillExecutor routes execution to Docker sandbox (draft/testing/broken) or host (stable, via VitalGuard). Six LLM-facing tools let Lapwing create/run/edit/list/promote/delete skills. Stable skills auto-register as ToolSpec at boot and hot-register at runtime.


**Tech Stack:** Python 3.12, Docker CLI (`docker run --rm`), pytest + pytest-asyncio, FastAPI (read-only API endpoints).

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/skills/__init__.py` | Package init (empty) |
| `src/skills/skill_store.py` | CRUD + lifecycle for `data/skills/*.md` files |
| `src/skills/skill_executor.py` | Route execution to sandbox or host based on maturity |
| `src/tools/skill_tools.py` | 6 LLM-facing tools: create/run/edit/list/promote/delete |
| `src/api/routes/skills_v2.py` | Read-only REST API for desktop visualization |
| `docker/sandbox/runner_template.py` | Sandbox skill execution wrapper |
| `tests/skills/__init__.py` | Test package init |
| `tests/skills/test_skill_store.py` | SkillStore unit tests |
| `tests/skills/test_skill_executor.py` | SkillExecutor unit tests |
| `tests/tools/test_skill_tools.py` | Skill tool executor tests |
| `tests/core/test_skill_registration.py` | Dynamic + hot registration tests |

### Modified Files

| File | Change |
|------|--------|
| `config/settings.py` | Add `SKILL_SYSTEM_ENABLED` flag + `SKILL_SANDBOX_IMAGE` + `SKILL_SANDBOX_TIMEOUT` |
| `src/app/container.py` | Wire SkillStore + SkillExecutor + register tools + inject services |
| `src/core/brain.py` | Add `skill_store` and `skill_executor` to services dict (~line 245) |
| `src/core/authority_gate.py` | Add 6 skill tools to `OPERATION_AUTH` (OWNER level) |
| `src/core/inner_tick_scheduler.py` | Extend `build_inner_prompt()` with skill reflection items |
| `src/core/state_view.py` | Add `skill_summary: SkillSummary` field to `StateView` |
| `src/core/state_view_builder.py` | Build `SkillSummary` from `SkillStore.list_skills()` |
| `src/core/state_serializer.py` | Render skill summary section in system prompt |
| `src/api/server.py` | Mount `skills_v2` router |
| `docker/sandbox/Dockerfile` | Upgrade base image + pre-install common packages |

---

## Task 1: SkillStore — Data Layer

**Files:**
- Create: `src/skills/__init__.py`
- Create: `src/skills/skill_store.py`
- Create: `tests/skills/__init__.py`
- Create: `tests/skills/test_skill_store.py`

### Step 1.1: Write the SkillStore create + read tests

- [ ] **Step 1.1.1: Create test file with create/read tests**

```python
# tests/skills/__init__.py — empty

# tests/skills/test_skill_store.py
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
```

- [ ] **Step 1.1.2: Run tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestCreate -x -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.skills'`

### Step 1.2: Implement SkillStore create + read

- [ ] **Step 1.2.1: Create package init**

```python
# src/skills/__init__.py — empty
```

- [ ] **Step 1.2.2: Implement SkillStore**

```python
# src/skills/skill_store.py
import uuid
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Taipei")


class SkillStore:
    """技能文件管理。YAML frontmatter + markdown，镜像 NoteStore 模式。"""

    def __init__(self, skills_dir=None):
        self.skills_dir = Path(skills_dir) if skills_dir else Path("data/skills")
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        skill_id: str,
        name: str,
        description: str,
        code: str,
        dependencies: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        now = datetime.now(tz=_TZ)
        meta = {
            "id": skill_id,
            "name": name,
            "description": description,
            "maturity": "draft",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "usage_count": 0,
            "success_count": 0,
            "last_error": None,
            "last_error_at": None,
            "last_tested_at": None,
            "dependencies": dependencies or [],
            "tags": tags or [],
            "author": "lapwing",
        }
        file_path = self.skills_dir / f"{skill_id}.md"
        self._write_file(file_path, meta, code)
        return {"skill_id": skill_id, "file_path": str(file_path.resolve())}

    def read(self, skill_id: str) -> dict | None:
        file_path = self.skills_dir / f"{skill_id}.md"
        if not file_path.exists():
            return None
        raw = file_path.read_text(encoding="utf-8")
        meta, body = self._parse(raw)
        if meta is None:
            return None
        code = self._extract_code(body)
        return {"meta": meta, "code": code, "file_path": str(file_path.resolve())}

    def _write_file(self, file_path: Path, meta: dict, code: str) -> None:
        frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        body = f"## 代码\n\n```python\n{code}\n```"
        file_path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")

    def _parse(self, raw: str) -> tuple[dict | None, str]:
        if not raw.startswith("---"):
            return None, raw
        end = raw.find("\n---\n", 3)
        if end == -1:
            return None, raw
        frontmatter_str = raw[3:end]
        content = raw[end + 5:]
        try:
            meta = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError:
            return None, raw
        return meta, content

    def _extract_code(self, body: str) -> str:
        marker = "```python\n"
        start = body.find(marker)
        if start == -1:
            return ""
        start += len(marker)
        end = body.find("\n```", start)
        if end == -1:
            return body[start:]
        return body[start:end]
```

- [ ] **Step 1.2.3: Run create + read tests**

Run: `python -m pytest tests/skills/test_skill_store.py::TestCreate tests/skills/test_skill_store.py::TestRead -x -v`
Expected: PASS

### Step 1.3: Add remaining SkillStore methods + tests

- [ ] **Step 1.3.1: Add tests for update_code, update_meta, record_execution**

Append to `tests/skills/test_skill_store.py`:

```python
class TestUpdateCode:
    def test_update_code_resets_to_draft(self, skill_store):
        skill_store.create(
            skill_id="skill_upd",
            name="更新测试",
            description="会被更新",
            code='def run():\n    return 1',
        )
        # Manually set maturity to testing via update_meta
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
```

- [ ] **Step 1.3.2: Run new tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestUpdateCode -x -v`
Expected: FAIL — `AttributeError: 'SkillStore' object has no attribute 'update_code'`

- [ ] **Step 1.3.3: Implement update_code, update_meta, record_execution**

Add to `src/skills/skill_store.py` `SkillStore` class:

```python
    def update_code(self, skill_id: str, new_code: str) -> dict:
        skill = self.read(skill_id)
        if skill is None:
            return {"success": False, "reason": "技能不存在"}
        meta = skill["meta"]
        meta["maturity"] = "draft"
        meta["updated_at"] = datetime.now(tz=_TZ).isoformat()
        file_path = self.skills_dir / f"{skill_id}.md"
        self._write_file(file_path, meta, new_code)
        return {"success": True, "reason": ""}

    def update_meta(self, skill_id: str, **fields) -> dict:
        skill = self.read(skill_id)
        if skill is None:
            return {"success": False, "reason": "技能不存在"}
        meta = skill["meta"]
        for k, v in fields.items():
            meta[k] = v
        meta["updated_at"] = datetime.now(tz=_TZ).isoformat()
        file_path = self.skills_dir / f"{skill_id}.md"
        self._write_file(file_path, meta, skill["code"])
        return {"success": True, "reason": ""}

    def record_execution(self, skill_id: str, success: bool, error: str = None) -> dict:
        skill = self.read(skill_id)
        if skill is None:
            return {"success": False, "reason": "技能不存在"}
        meta = skill["meta"]
        now = datetime.now(tz=_TZ)
        meta["usage_count"] = meta.get("usage_count", 0) + 1
        meta["last_tested_at"] = now.isoformat()
        if success:
            meta["success_count"] = meta.get("success_count", 0) + 1
            if meta.get("maturity") == "draft":
                meta["maturity"] = "testing"
        else:
            meta["last_error"] = error
            meta["last_error_at"] = now.isoformat()
            if meta.get("maturity") == "stable":
                meta["maturity"] = "broken"
        meta["updated_at"] = now.isoformat()
        file_path = self.skills_dir / f"{skill_id}.md"
        self._write_file(file_path, meta, skill["code"])
        return {"success": True, "meta": meta}
```

- [ ] **Step 1.3.4: Run all update/record tests**

Run: `python -m pytest tests/skills/test_skill_store.py::TestUpdateCode tests/skills/test_skill_store.py::TestUpdateMeta tests/skills/test_skill_store.py::TestRecordExecution -x -v`
Expected: PASS

### Step 1.4: Add list, get_stable, delete methods + tests

- [ ] **Step 1.4.1: Add tests for list_skills, get_stable_skills, delete**

Append to `tests/skills/test_skill_store.py`:

```python
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
```

- [ ] **Step 1.4.2: Run new tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestListSkills -x -v`
Expected: FAIL — `AttributeError: 'SkillStore' object has no attribute 'list_skills'`

- [ ] **Step 1.4.3: Implement list_skills, get_stable_skills, delete**

Add to `src/skills/skill_store.py` `SkillStore` class:

```python
    def list_skills(self, maturity: str = None, tag: str = None) -> list[dict]:
        results = []
        for md_file in sorted(self.skills_dir.glob("*.md")):
            raw = md_file.read_text(encoding="utf-8")
            meta, _ = self._parse(raw)
            if meta is None:
                continue
            if maturity and meta.get("maturity") != maturity:
                continue
            if tag and tag not in (meta.get("tags") or []):
                continue
            results.append(meta)
        return results

    def get_stable_skills(self) -> list[dict]:
        results = []
        for md_file in sorted(self.skills_dir.glob("*.md")):
            raw = md_file.read_text(encoding="utf-8")
            meta, body = self._parse(raw)
            if meta is None or meta.get("maturity") != "stable":
                continue
            code = self._extract_code(body)
            results.append({"meta": meta, "code": code, "file_path": str(md_file.resolve())})
        return results

    def delete(self, skill_id: str) -> dict:
        file_path = self.skills_dir / f"{skill_id}.md"
        if not file_path.exists():
            return {"success": False, "reason": "技能不存在"}
        file_path.unlink()
        return {"success": True, "reason": ""}
```

- [ ] **Step 1.4.4: Run all SkillStore tests**

Run: `python -m pytest tests/skills/test_skill_store.py -x -v`
Expected: ALL PASS

- [ ] **Step 1.4.5: Commit**

```bash
git add src/skills/__init__.py src/skills/skill_store.py tests/skills/__init__.py tests/skills/test_skill_store.py
git commit -m "feat(skills): add SkillStore — YAML+markdown CRUD for skill files"
```

---

## Task 2: SkillExecutor — Sandbox + Host Execution

**Files:**
- Create: `src/skills/skill_executor.py`
- Create: `tests/skills/test_skill_executor.py`
- Modify: `docker/sandbox/Dockerfile`
- Create: `docker/sandbox/runner_template.py`

### Step 2.1: Write SkillExecutor tests

- [ ] **Step 2.1.1: Create executor test file**

```python
# tests/skills/test_skill_executor.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.skills.skill_executor import SkillExecutor, SkillResult
from src.skills.skill_store import SkillStore


@pytest.fixture
def skill_store(tmp_path):
    return SkillStore(skills_dir=tmp_path / "skills")


@pytest.fixture
def executor(skill_store):
    return SkillExecutor(skill_store=skill_store, sandbox_image="lapwing-sandbox")


class TestSkillResult:
    def test_success_result(self):
        r = SkillResult(success=True, output='{"x": 1}', error="", exit_code=0)
        assert r.success is True
        assert r.timed_out is False

    def test_timeout_result(self):
        r = SkillResult(success=False, output="", error="", exit_code=-1, timed_out=True)
        assert r.timed_out is True


class TestExecuteRouting:
    async def test_nonexistent_skill_fails(self, executor):
        result = await executor.execute("skill_nope")
        assert result.success is False
        assert "不存在" in result.error

    async def test_draft_routes_to_sandbox(self, executor, skill_store):
        skill_store.create(
            "skill_sandbox",
            "沙盒测试",
            "测试沙盒路由",
            'def run():\n    return {"ok": True}',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{"ok": true}', error="", exit_code=0,
            )
            result = await executor.execute("skill_sandbox")
            mock_sb.assert_called_once()
            assert result.success is True

    async def test_stable_routes_to_host(self, executor, skill_store):
        skill_store.create(
            "skill_host",
            "主机测试",
            "测试主机路由",
            'def run():\n    return {"ok": True}',
        )
        skill_store.update_meta("skill_host", maturity="stable")
        with patch.object(executor, "_run_on_host", new_callable=AsyncMock) as mock_host:
            mock_host.return_value = SkillResult(
                success=True, output='{"ok": true}', error="", exit_code=0,
            )
            result = await executor.execute("skill_host")
            mock_host.assert_called_once()
            assert result.success is True

    async def test_broken_routes_to_sandbox(self, executor, skill_store):
        skill_store.create(
            "skill_broken",
            "损坏测试",
            "测试损坏路由",
            'def run():\n    return {}',
        )
        skill_store.update_meta("skill_broken", maturity="broken")
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{}', error="", exit_code=0,
            )
            await executor.execute("skill_broken")
            mock_sb.assert_called_once()


class TestRecordExecution:
    async def test_success_records_to_store(self, executor, skill_store):
        skill_store.create(
            "skill_rec",
            "记录测试",
            "测试执行记录",
            'def run():\n    return {}',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{}', error="", exit_code=0,
            )
            await executor.execute("skill_rec")
        skill = skill_store.read("skill_rec")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 1

    async def test_failure_records_error(self, executor, skill_store):
        skill_store.create(
            "skill_err",
            "错误测试",
            "测试错误记录",
            'def run():\n    raise ValueError("boom")',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=False, output="", error="ValueError: boom", exit_code=1,
            )
            await executor.execute("skill_err")
        skill = skill_store.read("skill_err")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 0
        assert "boom" in skill["meta"]["last_error"]


class TestRunOnHost:
    async def test_host_executes_code(self, executor, skill_store):
        skill_store.create(
            "skill_host_real",
            "主机真实测试",
            "在主机上真实执行",
            'def run(x=1):\n    return {"result": x * 2}',
        )
        skill_store.update_meta("skill_host_real", maturity="stable")
        result = await executor._run_on_host(
            'def run(x=1):\n    return {"result": x * 2}',
            {"x": 5},
            [],
            timeout=10,
        )
        assert result.success is True
        assert '"result": 10' in result.output

    async def test_host_timeout(self, executor):
        result = await executor._run_on_host(
            'import time\ndef run():\n    time.sleep(100)\n    return {}',
            {},
            [],
            timeout=1,
        )
        assert result.success is False
        assert result.timed_out is True
```

- [ ] **Step 2.1.2: Run tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_executor.py::TestSkillResult -x -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.skills.skill_executor'`

### Step 2.2: Implement SkillExecutor

- [ ] **Step 2.2.1: Create SkillExecutor**

```python
# src/skills/skill_executor.py
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("lapwing.skills.skill_executor")

_MAX_OUTPUT = 4000
_SANDBOX_MATURITIES = frozenset({"draft", "testing", "broken"})


@dataclass
class SkillResult:
    success: bool
    output: str
    error: str
    exit_code: int
    timed_out: bool = False


class SkillExecutor:
    """技能执行引擎。根据 maturity 路由到沙盒或主机。"""

    def __init__(self, skill_store, sandbox_image: str = "lapwing-sandbox"):
        self._store = skill_store
        self._sandbox_image = sandbox_image

    async def execute(
        self,
        skill_id: str,
        arguments: dict | None = None,
        timeout: int = 30,
    ) -> SkillResult:
        skill = self._store.read(skill_id)
        if skill is None:
            return SkillResult(
                success=False, output="", error=f"技能 {skill_id} 不存在", exit_code=-1,
            )

        meta = skill["meta"]
        code = skill["code"]
        maturity = meta.get("maturity", "draft")
        dependencies = meta.get("dependencies") or []
        args = arguments or {}

        if maturity in _SANDBOX_MATURITIES:
            result = await self._run_in_sandbox(code, args, dependencies, timeout)
        else:
            result = await self._run_on_host(code, args, dependencies, timeout)

        self._store.record_execution(
            skill_id,
            success=result.success,
            error=result.error if not result.success else None,
        )
        return result

    async def _run_in_sandbox(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")

            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "-v", f"{tmp_dir}:/workspace:ro",
                "--user", "sandboxuser",
                self._sandbox_image,
                "python3", "/workspace/runner.py",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SkillResult(
                    success=False, output="", error="沙盒执行超时", exit_code=-1, timed_out=True,
                )

            stdout = raw_out.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            stderr = raw_err.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            exit_code = proc.returncode if proc.returncode is not None else -1

            return SkillResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr,
                exit_code=exit_code,
            )

        except FileNotFoundError:
            return SkillResult(
                success=False, output="",
                error="Docker 未安装或不可用", exit_code=-1,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SkillResult(
                success=False, output="", error=str(e), exit_code=-1,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _run_on_host(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_host_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")

            runner_code = self._build_runner(arguments, [])
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(runner_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir,
            )

            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SkillResult(
                    success=False, output="", error="主机执行超时", exit_code=-1, timed_out=True,
                )

            stdout = raw_out.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            stderr = raw_err.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            exit_code = proc.returncode if proc.returncode is not None else -1

            return SkillResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr,
                exit_code=exit_code,
            )
        except Exception as e:
            logger.error("主机执行异常: %s", e)
            return SkillResult(
                success=False, output="", error=str(e), exit_code=-1,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_runner(self, arguments: dict, dependencies: list[str]) -> str:
        args_json = json.dumps(arguments, ensure_ascii=False)
        dep_install = ""
        if dependencies:
            dep_install = f"""
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + {repr(dependencies)},
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""
        return f'''import json
import sys
import importlib.util
from pathlib import Path
{dep_install}
def main():
    args = json.loads({repr(args_json)})
    skill_path = str(Path(__file__).parent / "skill.py")
    spec = importlib.util.spec_from_file_location("skill", skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.run(**args)
    print(json.dumps(result, ensure_ascii=False, default=str))

if __name__ == "__main__":
    main()
'''
```

- [ ] **Step 2.2.2: Run executor tests**

Run: `python -m pytest tests/skills/test_skill_executor.py -x -v`
Expected: PASS (sandbox tests use mocks; host tests run for real)

### Step 2.3: Upgrade Docker sandbox

- [ ] **Step 2.3.1: Update Dockerfile**

Replace content of `docker/sandbox/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    requests beautifulsoup4 httpx lxml \
    pandas numpy \
    pyyaml toml

RUN useradd -m -s /bin/bash sandboxuser
USER sandboxuser

WORKDIR /workspace
```

- [ ] **Step 2.3.2: Create runner template**

```python
# docker/sandbox/runner_template.py
"""Sandbox skill execution wrapper. Generated by SkillExecutor at runtime."""
import json
import sys
import importlib.util


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    spec = importlib.util.spec_from_file_location("skill", "/workspace/skill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.run(**args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.3.3: Commit**

```bash
git add src/skills/skill_executor.py tests/skills/test_skill_executor.py docker/sandbox/Dockerfile docker/sandbox/runner_template.py
git commit -m "feat(skills): add SkillExecutor — sandbox/host routing with Docker isolation"
```

---

## Task 3: Skill Tools — LLM Interface

**Files:**
- Create: `src/tools/skill_tools.py`
- Create: `tests/tools/test_skill_tools.py`
- Modify: `src/core/authority_gate.py`

### Step 3.1: Write skill tool tests

- [ ] **Step 3.1.1: Create skill tools test file**

```python
# tests/tools/test_skill_tools.py
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
```

- [ ] **Step 3.1.2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_skill_tools.py::TestCreateSkill::test_create_success -x -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.skill_tools'`

### Step 3.2: Implement skill tools

- [ ] **Step 3.2.1: Create skill_tools.py**

```python
# src/tools/skill_tools.py
"""create_skill / run_skill / edit_skill / list_skills / promote_skill / delete_skill"""
from __future__ import annotations

import logging

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.skill_tools")


# ── Schemas ──────────────────────────────────────────────────────────

CREATE_SKILL_DESCRIPTION = (
    "创建一个新技能。当你写了一段可复用的代码，用这个工具把它保存成技能。"
    "技能创建后状态是 draft，需要在沙盒中测试成功后才能升级。"
    "code 参数必须包含一个 def run(...) 函数作为入口。"
)
CREATE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "唯一标识，格式 skill_{简短描述}"},
        "name": {"type": "string", "description": "人类可读名称"},
        "description": {"type": "string", "description": "一句话说明功能"},
        "code": {"type": "string", "description": "Python 代码，必须包含 def run(...) 入口函数"},
        "dependencies": {
            "type": "array", "items": {"type": "string"},
            "description": "pip 依赖列表（可选）",
        },
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "分类标签（可选）",
        },
    },
    "required": ["skill_id", "name", "description", "code"],
    "additionalProperties": False,
}

RUN_SKILL_DESCRIPTION = (
    "执行一个技能。draft/testing/broken 状态的技能在 Docker 沙盒中执行，"
    "stable 状态的技能在主机上执行。执行结果会自动记录到技能元数据。"
)
RUN_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要执行的技能 ID"},
        "arguments": {
            "type": "object", "description": "传给 run() 函数的参数（可选）",
        },
        "timeout": {
            "type": "integer", "description": "超时秒数（默认 30）",
            "default": 30, "minimum": 1, "maximum": 300,
        },
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

EDIT_SKILL_DESCRIPTION = (
    "修改技能的代码。修改后技能状态会重置为 draft，需要重新测试。"
)
EDIT_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要修改的技能 ID"},
        "code": {"type": "string", "description": "新的 Python 代码"},
    },
    "required": ["skill_id", "code"],
    "additionalProperties": False,
}

LIST_SKILLS_DESCRIPTION = "查看你的技能列表，可以按状态或标签过滤。"
LIST_SKILLS_SCHEMA = {
    "type": "object",
    "properties": {
        "maturity": {
            "type": "string",
            "enum": ["draft", "testing", "stable", "broken"],
            "description": "按状态过滤（可选）",
        },
        "tag": {"type": "string", "description": "按标签过滤（可选）"},
    },
    "additionalProperties": False,
}

PROMOTE_SKILL_DESCRIPTION = (
    "将一个 testing 状态的技能标记为 stable。只有你确信技能足够稳定时才调用。"
    "stable 的技能会被注册为一等工具，可以在对话中直接调用。"
)
PROMOTE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要升级的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}

DELETE_SKILL_DESCRIPTION = "删除一个技能。"
DELETE_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "要删除的技能 ID"},
    },
    "required": ["skill_id"],
    "additionalProperties": False,
}


# ── Executors ────────────────────────────────────────────────────────

async def create_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "SkillStore 未挂载"},
            reason="create_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    name = str(request.arguments.get("name", "")).strip()
    description = str(request.arguments.get("description", "")).strip()
    code = str(request.arguments.get("code", "")).strip()

    if not all([skill_id, name, description, code]):
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "skill_id, name, description, code 都不能为空"},
            reason="create_skill 缺少必需参数",
        )

    dependencies = request.arguments.get("dependencies") or []
    tags = request.arguments.get("tags") or []

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
        )
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": str(exc)},
            reason=f"SkillStore.create 失败: {exc}",
        )

    return ToolExecutionResult(
        success=True,
        payload={
            "created": True,
            "skill_id": result["skill_id"],
            "file_path": result["file_path"],
            "maturity": "draft",
        },
    )


async def run_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    executor = services.get("skill_executor")
    if executor is None:
        return ToolExecutionResult(
            success=False,
            payload={"executed": False, "reason": "SkillExecutor 未挂载"},
            reason="run_skill 在没有 skill_executor 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"executed": False, "reason": "skill_id 不能为空"},
            reason="run_skill 缺少 skill_id",
        )

    arguments = request.arguments.get("arguments") or {}
    timeout = int(request.arguments.get("timeout", 30) or 30)
    timeout = max(1, min(timeout, 300))

    result = await executor.execute(skill_id, arguments=arguments, timeout=timeout)

    return ToolExecutionResult(
        success=result.success,
        payload={
            "executed": True,
            "skill_id": skill_id,
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
    )


async def edit_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "SkillStore 未挂载"},
            reason="edit_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    code = str(request.arguments.get("code", "")).strip()
    if not skill_id or not code:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "skill_id 和 code 不能为空"},
            reason="edit_skill 缺少参数",
        )

    result = store.update_code(skill_id, code)
    return ToolExecutionResult(
        success=result["success"],
        payload={"updated": result["success"], "skill_id": skill_id, "reason": result.get("reason", "")},
        reason=result.get("reason", ""),
    )


async def list_skills_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"skills": [], "reason": "SkillStore 未挂载"},
            reason="list_skills 在没有 skill_store 的上下文中被调用",
        )

    maturity = request.arguments.get("maturity")
    tag = request.arguments.get("tag")
    skills = store.list_skills(maturity=maturity, tag=tag)

    return ToolExecutionResult(
        success=True,
        payload={
            "skills": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "maturity": s["maturity"],
                    "usage_count": s.get("usage_count", 0),
                    "success_count": s.get("success_count", 0),
                    "tags": s.get("tags", []),
                }
                for s in skills
            ],
            "total": len(skills),
        },
    )


async def promote_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": "SkillStore 未挂载"},
            reason="promote_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": "skill_id 不能为空"},
            reason="promote_skill 缺少 skill_id",
        )

    skill = store.read(skill_id)
    if skill is None:
        return ToolExecutionResult(
            success=False,
            payload={"promoted": False, "reason": f"技能 {skill_id} 不存在"},
            reason=f"技能 {skill_id} 不存在",
        )

    if skill["meta"]["maturity"] not in ("testing", "broken"):
        return ToolExecutionResult(
            success=False,
            payload={
                "promoted": False,
                "reason": f"只能从 testing/broken 升级到 stable，当前状态: {skill['meta']['maturity']}",
            },
            reason=f"promote_skill: 当前状态 {skill['meta']['maturity']} 不可升级",
        )

    store.update_meta(skill_id, maturity="stable")

    # Hot-register as a ToolSpec if tool_registry is available
    tool_registry = services.get("tool_registry")
    if tool_registry is not None:
        _register_skill_as_tool(tool_registry, store, services.get("skill_executor"), skill_id)

    return ToolExecutionResult(
        success=True,
        payload={
            "promoted": True,
            "skill_id": skill_id,
            "maturity": "stable",
        },
    )


async def delete_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"deleted": False, "reason": "SkillStore 未挂载"},
            reason="delete_skill 在没有 skill_store 的上下文中被调用",
        )

    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"deleted": False, "reason": "skill_id 不能为空"},
            reason="delete_skill 缺少 skill_id",
        )

    result = store.delete(skill_id)
    return ToolExecutionResult(
        success=result["success"],
        payload={"deleted": result["success"], "skill_id": skill_id, "reason": result.get("reason", "")},
        reason=result.get("reason", ""),
    )


# ── Dynamic tool registration ────────────────────────────────────────

def _register_skill_as_tool(tool_registry, skill_store, skill_executor, skill_id: str) -> None:
    skill = skill_store.read(skill_id)
    if skill is None:
        return
    meta = skill["meta"]

    async def _executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
        executor = (ctx.services or {}).get("skill_executor")
        if executor is None:
            return ToolExecutionResult(success=False, payload={}, reason="SkillExecutor 未挂载")
        result = await executor.execute(skill_id, arguments=req.arguments or {})
        return ToolExecutionResult(
            success=result.success,
            payload={"output": result.output, "error": result.error},
        )

    tool_registry.register(ToolSpec(
        name=skill_id,
        description=meta.get("description", ""),
        json_schema={"type": "object", "properties": {}, "additionalProperties": True},
        executor=_executor,
        capability="skill",
        risk_level="medium",
    ))


def register_skill_tools(tool_registry) -> None:
    """Register the 6 skill management tools into the registry."""
    tool_registry.register(ToolSpec(
        name="create_skill",
        description=CREATE_SKILL_DESCRIPTION,
        json_schema=CREATE_SKILL_SCHEMA,
        executor=create_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="run_skill",
        description=RUN_SKILL_DESCRIPTION,
        json_schema=RUN_SKILL_SCHEMA,
        executor=run_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="edit_skill",
        description=EDIT_SKILL_DESCRIPTION,
        json_schema=EDIT_SKILL_SCHEMA,
        executor=edit_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="list_skills",
        description=LIST_SKILLS_DESCRIPTION,
        json_schema=LIST_SKILLS_SCHEMA,
        executor=list_skills_executor,
        capability="skill",
        risk_level="low",
    ))
    tool_registry.register(ToolSpec(
        name="promote_skill",
        description=PROMOTE_SKILL_DESCRIPTION,
        json_schema=PROMOTE_SKILL_SCHEMA,
        executor=promote_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
    tool_registry.register(ToolSpec(
        name="delete_skill",
        description=DELETE_SKILL_DESCRIPTION,
        json_schema=DELETE_SKILL_SCHEMA,
        executor=delete_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
```

- [ ] **Step 3.2.2: Run skill tool tests**

Run: `python -m pytest tests/tools/test_skill_tools.py -x -v`
Expected: PASS

### Step 3.3: Add authority gate entries

- [ ] **Step 3.3.1: Add 6 skill tools to OPERATION_AUTH**

In `src/core/authority_gate.py`, add after the browser operations block (~line 101):

```python
    # 技能系统
    "create_skill": AuthLevel.OWNER,
    "run_skill": AuthLevel.OWNER,
    "edit_skill": AuthLevel.OWNER,
    "list_skills": AuthLevel.OWNER,
    "promote_skill": AuthLevel.OWNER,
    "delete_skill": AuthLevel.OWNER,
```

- [ ] **Step 3.3.2: Commit**

```bash
git add src/tools/skill_tools.py tests/tools/test_skill_tools.py src/core/authority_gate.py
git commit -m "feat(skills): add 6 LLM-facing skill tools + authority gate entries"
```

---

## Task 4: Container Wiring + Feature Flag

**Files:**
- Modify: `config/settings.py`
- Modify: `src/app/container.py`
- Modify: `src/core/brain.py`
- Create: `tests/core/test_skill_registration.py`

### Step 4.1: Add feature flag + settings

- [ ] **Step 4.1.1: Add SKILL_SYSTEM_ENABLED to settings.py**

In `config/settings.py`, add after other `*_ENABLED` flags (around line 120):

```python
SKILL_SYSTEM_ENABLED: bool = os.getenv("SKILL_SYSTEM_ENABLED", "true").lower() in ("true", "1", "yes")
SKILL_SANDBOX_IMAGE: str = os.getenv("SKILL_SANDBOX_IMAGE", "lapwing-sandbox")
SKILL_SANDBOX_TIMEOUT: int = int(os.getenv("SKILL_SANDBOX_TIMEOUT", "30"))
```

### Step 4.2: Wire into container.py

- [ ] **Step 4.2.1: Add skill system wiring to _configure_brain_dependencies**

In `src/app/container.py`, add after the Research subsystem block (around line 716, before the DurableScheduler section):

```python
        # Skill Growth Model
        from config.settings import SKILL_SYSTEM_ENABLED
        if SKILL_SYSTEM_ENABLED:
            from src.skills.skill_store import SkillStore
            from src.skills.skill_executor import SkillExecutor
            from src.tools.skill_tools import register_skill_tools, _register_skill_as_tool
            from config.settings import SKILL_SANDBOX_IMAGE

            skill_store = SkillStore()
            skill_executor = SkillExecutor(
                skill_store=skill_store,
                sandbox_image=SKILL_SANDBOX_IMAGE,
            )
            self.brain._skill_store = skill_store
            self.brain._skill_executor = skill_executor

            # Register stable skills as first-class tools
            for stable_skill in skill_store.get_stable_skills():
                _register_skill_as_tool(
                    self.brain.tool_registry,
                    skill_store,
                    skill_executor,
                    stable_skill["meta"]["id"],
                )

            # Register the 6 management tools
            register_skill_tools(self.brain.tool_registry)

            logger.info(
                "Skill Growth Model 已装配（%d stable skills registered as tools）",
                len(skill_store.get_stable_skills()),
            )
```

### Step 4.3: Add skill services to brain's services dict

- [ ] **Step 4.3.1: Add skill_store, skill_executor, tool_registry to services**

In `src/core/brain.py`, in the `_complete_chat` method where services dict is built (around line 266), add:

```python
        skill_store = getattr(self, "_skill_store", None)
        if skill_store is not None:
            services["skill_store"] = skill_store
        skill_executor = getattr(self, "_skill_executor", None)
        if skill_executor is not None:
            services["skill_executor"] = skill_executor
        services["tool_registry"] = self.tool_registry
```

### Step 4.4: Write registration tests

- [ ] **Step 4.4.1: Create registration test file**

```python
# tests/core/test_skill_registration.py
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
```

- [ ] **Step 4.4.2: Run registration tests**

Run: `python -m pytest tests/core/test_skill_registration.py -x -v`
Expected: PASS

- [ ] **Step 4.4.3: Run full test suite to check no regressions**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS (~1257+ cases)

- [ ] **Step 4.4.4: Commit**

```bash
git add config/settings.py src/app/container.py src/core/brain.py tests/core/test_skill_registration.py
git commit -m "feat(skills): wire SkillStore + SkillExecutor into container + brain services"
```

---

## Task 5: Inner Tick Prompt + StateView Integration

**Files:**
- Modify: `src/core/inner_tick_scheduler.py`
- Modify: `src/core/state_view.py`
- Modify: `src/core/state_view_builder.py`
- Modify: `src/core/state_serializer.py`

### Step 5.1: Extend inner tick prompt

- [ ] **Step 5.1.1: Add skill reflection items to build_inner_prompt**

In `src/core/inner_tick_scheduler.py`, in `build_inner_prompt()`, after the existing reflection items (line 113) and before the rules section (line 114), add:

```python
    parts.append("- 你学会了哪些新技能？有没有还不够稳定需要再练习的？")
    parts.append("- 你可以想象 Kevin 可能会让你做什么，提前自己模拟练习")
    parts.append("- 你有没有可以写成技能的重复性操作？")
```

### Step 5.2: Add SkillSummary to StateView

- [ ] **Step 5.2.1: Add SkillSummary dataclass to state_view.py**

In `src/core/state_view.py`, before the `StateView` class (around line 162), add:

```python
# ── Skills ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SkillSummary:
    """Snapshot of Lapwing's learned skills for prompt injection."""
    stable_count: int
    testing_count: int
    draft_count: int
    broken_count: int
    stable_names: tuple[str, ...]
    testing_details: tuple[str, ...]  # "name (success_rate N/M)"
```

Then add `skill_summary: SkillSummary | None = None` to `StateView`:

```python
@dataclass(frozen=True, slots=True)
class StateView:
    identity_docs: IdentityDocs
    attention_context: AttentionContext
    trajectory_window: TrajectoryWindow
    memory_snippets: MemorySnippets
    commitments_active: tuple[CommitmentView, ...]
    skill_summary: SkillSummary | None = None
```

### Step 5.3: Build SkillSummary in StateViewBuilder

- [ ] **Step 5.3.1: Add _skill_store attribute and _build_skill_summary method**

In `src/core/state_view_builder.py`, add `_skill_store` to `__init__` (after `_memory_query_chat_turns`):

```python
        self._skill_store = None  # set by container when skill system is enabled
```

Also add `SkillSummary` to the top-level import in `state_view_builder.py:34-43`:

```python
from src.core.state_view import (
    AttentionContext,
    CommitmentView,
    IdentityDocs,
    MemorySnippet,
    MemorySnippets,
    SkillSummary,
    StateView,
    TrajectoryTurn,
    TrajectoryWindow,
)
```

Add `_build_skill_summary` method:

```python
    def _build_skill_summary(self) -> SkillSummary | None:
        if self._skill_store is None:
            return None
        try:
            all_skills = self._skill_store.list_skills()
        except Exception:
            return None
        if not all_skills:
            return None

        counts = {"draft": 0, "testing": 0, "stable": 0, "broken": 0}
        stable_names = []
        testing_details = []
        for s in all_skills:
            m = s.get("maturity", "draft")
            counts[m] = counts.get(m, 0) + 1
            if m == "stable":
                stable_names.append(s.get("name", s.get("id", "")))
            elif m == "testing":
                usage = s.get("usage_count", 0)
                success = s.get("success_count", 0)
                testing_details.append(f"{s.get('name', '')}（成功率 {success}/{usage}）")

        return SkillSummary(
            stable_count=counts["stable"],
            testing_count=counts["testing"],
            draft_count=counts["draft"],
            broken_count=counts["broken"],
            stable_names=tuple(stable_names),
            testing_details=tuple(testing_details),
        )
```

Then call it in both `build_for_chat` and `build_for_inner`, passing `skill_summary=self._build_skill_summary()` to `StateView(...)`.

In `build_for_chat` (around line 143):

```python
        skill_summary = self._build_skill_summary()

        return StateView(
            identity_docs=identity_docs,
            attention_context=attention_context,
            trajectory_window=trajectory_window,
            memory_snippets=memory_snippets,
            commitments_active=commitments_active,
            skill_summary=skill_summary,
        )
```

Same in `build_for_inner` (around line 178).

- [ ] **Step 5.3.2: Wire _skill_store in container.py**

In `src/app/container.py`, inside the skill system block (added in Task 4), add after `self.brain._skill_executor = skill_executor`:

```python
            self.brain.state_view_builder._skill_store = skill_store
```

### Step 5.4: Render skill summary in StateSerializer

- [ ] **Step 5.4.1: Add skill summary rendering to `_render_runtime_state`**

In `src/core/state_serializer.py:109`, at the end of `_render_runtime_state()` — after the promise blocks (line 197) and before the final `return` (line 199) — add skill summary rendering:

```python
    # Skill summary
    if state.skill_summary is not None:
        ss = state.skill_summary
        total = ss.stable_count + ss.testing_count + ss.draft_count + ss.broken_count
        if total > 0:
            skill_lines = [f"stable: {ss.stable_count} 个"]
            if ss.stable_names:
                skill_lines[0] += f"（{'、'.join(ss.stable_names)}）"
            if ss.testing_count:
                detail = f"（{'、'.join(ss.testing_details)}）" if ss.testing_details else ""
                skill_lines.append(f"testing: {ss.testing_count} 个{detail}")
            if ss.draft_count:
                skill_lines.append(f"draft: {ss.draft_count} 个")
            if ss.broken_count:
                skill_lines.append(f"broken: {ss.broken_count} 个")
            lines.append("我的技能：\n" + "\n".join(f"  - {l}" for l in skill_lines))
```

Also add `SkillSummary` to the import block at `src/core/state_serializer.py:32-37`:

```python
from src.core.state_view import (
    CommitmentView,
    SerializedPrompt,
    SkillSummary,
    StateView,
    TrajectoryTurn,
)
```

- [ ] **Step 5.4.2: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 5.4.3: Commit**

```bash
git add src/core/inner_tick_scheduler.py src/core/state_view.py src/core/state_view_builder.py src/core/state_serializer.py src/app/container.py
git commit -m "feat(skills): inject skill summary into inner tick prompt + state view"
```

---

## Task 6: REST API for Desktop

**Files:**
- Create: `src/api/routes/skills_v2.py`
- Modify: `src/api/server.py`

### Step 6.1: Create skills API route

- [ ] **Step 6.1.1: Create skills_v2.py**

```python
# src/api/routes/skills_v2.py
"""技能 REST API — 只读，供桌面端可视化。"""

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("lapwing.api.routes.skills_v2")

router = APIRouter(prefix="/api/v2/skills", tags=["skills-v2"])

_skill_store = None


def init(skill_store) -> None:
    global _skill_store
    _skill_store = skill_store


@router.get("")
async def list_skills(
    maturity: str = Query(None, description="按状态过滤"),
    tag: str = Query(None, description="按标签过滤"),
):
    if _skill_store is None:
        return {"skills": [], "total": 0}
    skills = _skill_store.list_skills(maturity=maturity, tag=tag)
    return {"skills": skills, "total": len(skills)}


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    if _skill_store is None:
        raise HTTPException(status_code=503, detail="SkillStore not available")
    skill = _skill_store.read(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        "meta": skill["meta"],
        "code": skill["code"],
        "file_path": skill["file_path"],
    }
```

### Step 6.2: Mount in server.py

- [ ] **Step 6.2.1: Add skills_v2 route to server.py**

In `src/api/server.py`, after the notes_v2 router initialization (around line 89), add:

```python
    from src.api.routes import skills_v2 as _skills_v2_routes
    _skill_store = getattr(brain, "_skill_store", None)
    _skills_v2_routes.init(_skill_store)
    app.include_router(_skills_v2_routes.router)
```

- [ ] **Step 6.2.2: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 6.2.3: Commit**

```bash
git add src/api/routes/skills_v2.py src/api/server.py
git commit -m "feat(skills): add read-only REST API for desktop skill visualization"
```

---

## Task 7: Final Verification

### Step 7.1: Full test suite

- [ ] **Step 7.1.1: Run all tests**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

### Step 7.2: Import smoke test

- [ ] **Step 7.2.1: Verify imports work**

Run: `python -c "from src.skills.skill_store import SkillStore; from src.skills.skill_executor import SkillExecutor, SkillResult; from src.tools.skill_tools import register_skill_tools; print('OK')"`
Expected: `OK`

### Step 7.3: Manual sanity check

- [ ] **Step 7.3.1: Verify data directory creation**

Run: `python -c "from src.skills.skill_store import SkillStore; s = SkillStore(); s.create('skill_test_sanity', 'test', 'test', 'def run(): return {}'); print(s.read('skill_test_sanity')['meta']['maturity']); s.delete('skill_test_sanity'); print('OK')"`
Expected: `draft` then `OK`

- [ ] **Step 7.3.2: Final commit (if any remaining changes)**

```bash
git status
# If clean, skip. Otherwise commit any missed files.
```
