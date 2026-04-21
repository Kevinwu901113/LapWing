# Skill Self-Extension System — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the skill storage format from single `.md` files to directories (with `SKILL.md` + supporting files), add `search_skill` and `install_skill` tools so Lapwing can discover and acquire skills from the web, and update the StateViewBuilder to use a cached skill index.

**Architecture:** SkillStore migrates from flat `data/skills/{id}.md` to `data/skills/{id}/SKILL.md` directories. A `_index.json` cache (rebuilt at startup) provides O(1) skill listing. Two new tools—`search_skill` (Tavily web search + local index) and `install_skill` (URL download + security vetting)—extend the existing 6-tool set. Backward compatibility auto-migrates old single-file skills on first access.

**Tech Stack:** Python 3.12, httpx (HTTP downloads), Tavily REST API, pytest + pytest-asyncio.

---

## File Structure

### Modified Files

| File | Change |
|------|--------|
| `src/skills/skill_store.py` | Rewrite to directory-based storage, add index cache, migration, `get_skill_index()`, `load_skill_full()` |
| `src/skills/skill_executor.py` | Update code-reading path for new directory structure |
| `src/tools/skill_tools.py` | Add `search_skill` + `install_skill` tools, update `create_skill` for new frontmatter fields |
| `src/core/state_view_builder.py` | Use `get_skill_index()` for faster skill summary |
| `src/core/authority_gate.py` | Add `search_skill` (GUEST) + `install_skill` (OWNER) |
| `src/app/container.py` | Pass research_engine to brain for search_skill, register new tools |
| `tests/skills/test_skill_store.py` | Expand for directory format + migration + index |
| `tests/tools/test_skill_tools.py` | Add tests for search_skill + install_skill |

### New Files

| File | Responsibility |
|------|---------------|
| `src/skills/skill_security.py` | Security checker for installed skills (script scanning, path traversal prevention) |
| `tests/skills/test_skill_security.py` | Tests for security checker |

---

## Task 1: SkillStore Directory Format + Migration

**Files:**
- Modify: `src/skills/skill_store.py`
- Modify: `tests/skills/test_skill_store.py`

- [ ] **Step 1: Write failing tests for directory-based CRUD**

Add new test class to `tests/skills/test_skill_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestDirectoryFormat -x -v`
Expected: FAIL

- [ ] **Step 3: Write failing tests for legacy migration**

```python
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
```

- [ ] **Step 4: Run migration tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestLegacyMigration -x -v`
Expected: FAIL

- [ ] **Step 5: Write failing tests for index cache**

```python
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
```

- [ ] **Step 6: Run index tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_store.py::TestSkillIndex -x -v`
Expected: FAIL

- [ ] **Step 7: Implement new SkillStore**

Rewrite `src/skills/skill_store.py`:

```python
import json
import logging
import shutil
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Taipei")
logger = logging.getLogger("lapwing.skills.skill_store")

_NEW_META_DEFAULTS = {
    "version": "1.0.0",
    "origin": "self-created",
    "category": "general",
    "trust_required": "guest",
    "source_url": None,
    "evolution_history": [],
}


class SkillStore:
    """技能文件管理。目录结构：data/skills/{skill_id}/SKILL.md"""

    def __init__(self, skills_dir=None):
        self.skills_dir = Path(skills_dir) if skills_dir else Path("data/skills")
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._index_cache: list[dict] | None = None

    @staticmethod
    def _validate_skill_id(skill_id: str) -> None:
        if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
            raise ValueError(f"Invalid skill_id: {skill_id}")

    # ── CRUD ────────────────────────────────────────────────────────

    def create(self, skill_id: str, name: str, description: str, code: str,
               dependencies: list[str] | None = None, tags: list[str] | None = None,
               category: str = "general", origin: str = "self-created",
               source_url: str | None = None, derived_from: str | None = None) -> dict:
        self._validate_skill_id(skill_id)
        now = datetime.now(tz=_TZ)
        meta = {
            "id": skill_id, "name": name, "description": description,
            "version": "1.0.0", "maturity": "draft", "origin": origin,
            "tags": tags or [], "category": category,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "trust_required": "guest", "source_url": source_url,
            "evolution_history": [],
            "usage_count": 0, "success_count": 0,
            "last_error": None, "last_error_at": None, "last_tested_at": None,
            "dependencies": dependencies or [], "author": "lapwing",
        }
        if derived_from:
            meta["derived_from"] = derived_from
            meta["evolution_history"].append({
                "date": now.isoformat(), "type": "derived", "parent": derived_from,
            })
        skill_dir = self.skills_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(exist_ok=True)
        self._write_skill_md(skill_dir / "SKILL.md", meta, code)
        self._invalidate_index()
        return {"skill_id": skill_id, "file_path": str((skill_dir / "SKILL.md").resolve())}

    def read(self, skill_id: str) -> dict | None:
        self._validate_skill_id(skill_id)
        # Try directory format first
        skill_dir = self.skills_dir / skill_id
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            return self._read_skill_md(skill_id, skill_md)
        # Try legacy single-file format and auto-migrate
        legacy_path = self.skills_dir / f"{skill_id}.md"
        if legacy_path.exists():
            return self._migrate_legacy(skill_id, legacy_path)
        return None

    def update_code(self, skill_id: str, new_code: str) -> dict:
        skill = self.read(skill_id)
        if skill is None:
            return {"success": False, "reason": "技能不存在"}
        meta = skill["meta"]
        meta["maturity"] = "draft"
        meta["updated_at"] = datetime.now(tz=_TZ).isoformat()
        skill_md = self.skills_dir / skill_id / "SKILL.md"
        self._write_skill_md(skill_md, meta, new_code)
        self._invalidate_index()
        return {"success": True, "reason": ""}

    def update_meta(self, skill_id: str, **fields) -> dict:
        skill = self.read(skill_id)
        if skill is None:
            return {"success": False, "reason": "技能不存在"}
        meta = skill["meta"]
        for k, v in fields.items():
            meta[k] = v
        meta["updated_at"] = datetime.now(tz=_TZ).isoformat()
        skill_md = self.skills_dir / skill_id / "SKILL.md"
        self._write_skill_md(skill_md, meta, skill["code"])
        self._invalidate_index()
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
        skill_md = self.skills_dir / skill_id / "SKILL.md"
        self._write_skill_md(skill_md, meta, skill["code"])
        self._invalidate_index()
        return {"success": True, "meta": meta}

    def delete(self, skill_id: str) -> dict:
        self._validate_skill_id(skill_id)
        skill_dir = self.skills_dir / skill_id
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            self._invalidate_index()
            return {"success": True, "reason": ""}
        # Also handle legacy single-file
        legacy = self.skills_dir / f"{skill_id}.md"
        if legacy.exists():
            legacy.unlink()
            self._invalidate_index()
            return {"success": True, "reason": ""}
        return {"success": False, "reason": "技能不存在"}

    # ── Listing / Index ─────────────────────────────────────────────

    def list_skills(self, maturity: str = None, tag: str = None) -> list[dict]:
        results = []
        for meta in self._scan_all_meta():
            if maturity and meta.get("maturity") != maturity:
                continue
            if tag and tag not in (meta.get("tags") or []):
                continue
            results.append(meta)
        return results

    def get_stable_skills(self) -> list[dict]:
        results = []
        for entry in self._iter_skill_dirs():
            skill_id = entry.name
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            raw = skill_md.read_text(encoding="utf-8")
            meta, body = self._parse(raw)
            if meta is None or meta.get("maturity") != "stable":
                continue
            code = self._extract_code(body)
            results.append({"meta": meta, "code": code, "file_path": str(skill_md.resolve())})
        return results

    def get_skill_index(self) -> list[dict]:
        """Lightweight index: id, name, description, maturity, tags, category. No code."""
        if self._index_cache is not None:
            return self._index_cache
        # Try loading from _index.json
        index_path = self.skills_dir / "_index.json"
        if index_path.exists():
            try:
                self._index_cache = json.loads(index_path.read_text(encoding="utf-8"))
                return self._index_cache
            except (json.JSONDecodeError, OSError):
                pass
        # Rebuild from disk
        self.rebuild_index()
        return self._index_cache or []

    def load_skill_full(self, skill_id: str) -> str | None:
        """Return complete SKILL.md content (Level 1 load)."""
        self._validate_skill_id(skill_id)
        skill_md = self.skills_dir / skill_id / "SKILL.md"
        if skill_md.exists():
            return skill_md.read_text(encoding="utf-8")
        # Check legacy and migrate
        legacy = self.skills_dir / f"{skill_id}.md"
        if legacy.exists():
            self._migrate_legacy(skill_id, legacy)
            skill_md = self.skills_dir / skill_id / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text(encoding="utf-8")
        return None

    def rebuild_index(self) -> None:
        """Scan all skill directories and write _index.json."""
        entries = []
        for meta in self._scan_all_meta():
            entries.append({
                "id": meta.get("id", ""),
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "maturity": meta.get("maturity", "draft"),
                "tags": meta.get("tags", []),
                "category": meta.get("category", "general"),
                "origin": meta.get("origin", "self-created"),
            })
        self._index_cache = entries
        index_path = self.skills_dir / "_index.json"
        index_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Legacy migration ────────────────────────────────────────────

    def _migrate_legacy(self, skill_id: str, legacy_path: Path) -> dict | None:
        """Migrate old single-file .md to directory format."""
        raw = legacy_path.read_text(encoding="utf-8")
        meta, body = self._parse(raw)
        if meta is None:
            return None
        code = self._extract_code(body)
        # Backfill new fields
        for key, default in _NEW_META_DEFAULTS.items():
            if key not in meta:
                meta[key] = default
        if "created_at" not in meta:
            meta["created_at"] = datetime.now(tz=_TZ).isoformat()
        if "updated_at" not in meta:
            meta["updated_at"] = datetime.now(tz=_TZ).isoformat()
        # Write to new directory
        skill_dir = self.skills_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        self._write_skill_md(skill_md, meta, code)
        # Remove old file
        legacy_path.unlink()
        self._invalidate_index()
        logger.info("迁移旧格式技能: %s → %s/SKILL.md", legacy_path.name, skill_id)
        return {"meta": meta, "code": code, "file_path": str(skill_md.resolve())}

    # ── Internal helpers ────────────────────────────────────────────

    def _iter_skill_dirs(self) -> list[Path]:
        """Return sorted list of skill directories (exclude _index.json, legacy .md files)."""
        dirs = []
        for entry in sorted(self.skills_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("_"):
                dirs.append(entry)
        return dirs

    def _scan_all_meta(self) -> list[dict]:
        """Scan all skills (directories + legacy files) and return metadata."""
        results = []
        seen_ids = set()
        # Directories first
        for d in self._iter_skill_dirs():
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            raw = skill_md.read_text(encoding="utf-8")
            meta, _ = self._parse(raw)
            if meta is not None:
                seen_ids.add(meta.get("id", d.name))
                results.append(meta)
        # Legacy .md files
        for md_file in sorted(self.skills_dir.glob("*.md")):
            if md_file.name == "_index.json":
                continue
            sid = md_file.stem
            if sid in seen_ids:
                continue
            raw = md_file.read_text(encoding="utf-8")
            meta, _ = self._parse(raw)
            if meta is not None:
                results.append(meta)
        return results

    def _read_skill_md(self, skill_id: str, skill_md: Path) -> dict | None:
        raw = skill_md.read_text(encoding="utf-8")
        meta, body = self._parse(raw)
        if meta is None:
            return None
        code = self._extract_code(body)
        return {"meta": meta, "code": code, "file_path": str(skill_md.resolve())}

    def _write_skill_md(self, file_path: Path, meta: dict, code: str) -> None:
        frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        body = f"## 代码\n\n```python\n{code}\n```"
        file_path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")

    def _invalidate_index(self) -> None:
        self._index_cache = None
        index_path = self.skills_dir / "_index.json"
        if index_path.exists():
            index_path.unlink(missing_ok=True)

    @staticmethod
    def _parse(raw: str) -> tuple[dict | None, str]:
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

    @staticmethod
    def _extract_code(body: str) -> str:
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

- [ ] **Step 8: Run all SkillStore tests**

Run: `python -m pytest tests/skills/test_skill_store.py -x -v`
Expected: ALL PASS (old tests + new directory/migration/index tests)

- [ ] **Step 9: Commit**

```bash
git add src/skills/skill_store.py tests/skills/test_skill_store.py
git commit -m "feat(skills): migrate SkillStore to directory format with index cache and auto-migration"
```

---

## Task 2: Update SkillExecutor for Directory Format

**Files:**
- Modify: `src/skills/skill_executor.py`
- Modify: `tests/skills/test_skill_executor.py`

The SkillExecutor currently reads code from `skill_store.read()` which returns the code string directly. Since we didn't change the `read()` return format (still `{"meta": ..., "code": ..., "file_path": ...}`), the executor should still work. But we need to also support mounting the skill's `scripts/` directory into the sandbox.

- [ ] **Step 1: Write failing test for scripts directory mounting**

Add to `tests/skills/test_skill_executor.py`:

```python
class TestScriptsDirectoryAccess:
    async def test_skill_dir_path_available(self, executor, skill_store):
        """Executor should know where the skill directory is."""
        skill_store.create(
            "skill_dir_exec",
            "目录执行",
            "测试目录路径",
            'def run():\n    return {"ok": True}',
        )
        skill = skill_store.read("skill_dir_exec")
        # The file_path should point to the SKILL.md inside a directory
        assert "skill_dir_exec/SKILL.md" in skill["file_path"]
```

- [ ] **Step 2: Run test to verify it passes (directory format already works)**

Run: `python -m pytest tests/skills/test_skill_executor.py -x -v`
Expected: PASS (the executor uses `skill_store.read()` which returns code, no path changes needed)

- [ ] **Step 3: Update sandbox run to mount skill's scripts/ directory**

In `src/skills/skill_executor.py`, modify `execute()` to pass the skill directory path and update `_run_in_sandbox()` to mount `scripts/` if it exists:

```python
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
        # Resolve the skill directory for scripts/ access
        skill_dir = self._store.skills_dir / skill_id

        if maturity in _SANDBOX_MATURITIES:
            result = await self._run_in_sandbox(code, args, dependencies, timeout, skill_dir)
        else:
            result = await self._run_on_host(code, args, dependencies, timeout, skill_dir)

        self._store.record_execution(
            skill_id,
            success=result.success,
            error=result.error if not result.success else None,
        )
        return result
```

Update `_run_in_sandbox` signature to accept `skill_dir` and mount `scripts/`:

```python
    async def _run_in_sandbox(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
        skill_dir: Path | None = None,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            # Copy scripts/ into tmp workspace if available
            if skill_dir and (skill_dir / "scripts").is_dir():
                scripts_src = skill_dir / "scripts"
                scripts_dst = Path(tmp_dir) / "scripts"
                shutil.copytree(scripts_src, scripts_dst)

            result = await self._sandbox.run(
                ["python3", "/workspace/runner.py"],
                tier=SandboxTier.STRICT,
                timeout=timeout,
                workspace=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

Similarly update `_run_on_host`:

```python
    async def _run_on_host(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
        skill_dir: Path | None = None,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_host_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, [])
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            if skill_dir and (skill_dir / "scripts").is_dir():
                scripts_src = skill_dir / "scripts"
                scripts_dst = Path(tmp_dir) / "scripts"
                shutil.copytree(scripts_src, scripts_dst)

            result = await self._sandbox.run_local(
                [sys.executable, str(runner_path)],
                timeout=timeout,
                cwd=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("主机执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 4: Run all executor tests**

Run: `python -m pytest tests/skills/test_skill_executor.py -x -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/skills/skill_executor.py tests/skills/test_skill_executor.py
git commit -m "feat(skills): update SkillExecutor to mount scripts/ from skill directories"
```

---

## Task 3: Skill Security Checker

**Files:**
- Create: `src/skills/skill_security.py`
- Create: `tests/skills/test_skill_security.py`

- [ ] **Step 1: Write failing tests for security checker**

Create `tests/skills/test_skill_security.py`:

```python
import pytest
from src.skills.skill_security import check_skill_safety


class TestCodeSafety:
    def test_safe_code_passes(self):
        code = 'def run():\n    return {"hello": "world"}'
        result = check_skill_safety(code)
        assert result["safe"] is True

    def test_os_system_blocked(self):
        code = 'import os\ndef run():\n    os.system("rm -rf /")\n    return {}'
        result = check_skill_safety(code)
        assert result["safe"] is False
        assert "os.system" in result["reason"]

    def test_subprocess_popen_blocked(self):
        code = 'import subprocess\ndef run():\n    subprocess.Popen(["bash"])\n    return {}'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_eval_blocked(self):
        code = 'def run(cmd):\n    return eval(cmd)'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_exec_blocked(self):
        code = 'def run(cmd):\n    exec(cmd)\n    return {}'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_file_write_to_system_path_blocked(self):
        code = "def run():\n    open('/etc/passwd', 'w').write('hacked')\n    return {}"
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_importlib_blocked(self):
        code = 'import importlib\ndef run():\n    m = importlib.import_module("os")\n    return {}'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_pickle_blocked(self):
        code = 'import pickle\ndef run(data):\n    return pickle.loads(data)'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_compile_blocked(self):
        code = 'def run(src):\n    code = compile(src, "<string>", "exec")\n    return {}'
        result = check_skill_safety(code)
        assert result["safe"] is False

    def test_normal_file_operations_pass(self):
        code = "import json\ndef run():\n    return json.loads('{}')"
        result = check_skill_safety(code)
        assert result["safe"] is True

    def test_requests_library_passes(self):
        code = "import requests\ndef run(url):\n    return requests.get(url).json()"
        result = check_skill_safety(code)
        assert result["safe"] is True

    def test_lapwing_data_path_blocked(self):
        code = "def run():\n    open('data/identity/soul.md', 'w').write('evil')\n    return {}"
        result = check_skill_safety(code)
        assert result["safe"] is False


class TestMarkdownSafety:
    def test_safe_markdown_passes(self):
        md = "---\nname: test\n---\n## Procedure\nDo safe things."
        result = check_skill_safety(md, check_markdown=True)
        assert result["safe"] is True

    def test_system_file_modification_blocked(self):
        md = "---\nname: evil\n---\n## Procedure\nModify /etc/hosts to redirect DNS."
        result = check_skill_safety(md, check_markdown=True)
        assert result["safe"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/skills/test_skill_security.py -x -v`
Expected: FAIL

- [ ] **Step 3: Implement security checker**

Create `src/skills/skill_security.py`:

```python
"""安全检查：扫描技能代码和文档中的危险操作。"""

import re

_DANGEROUS_CALLS = [
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bos\.exec[lv]p?e?\b",
    r"\bsubprocess\.(?:Popen|call|run|check_call|check_output)\b",
    r"\b(?<!#\s)eval\s*\(",
    r"\b(?<!#\s)exec\s*\(",
    r"\bcompile\s*\(",
    r"\b__import__\s*\(",
    r"\bimportlib\.import_module\b",
    r"\bgetattr\s*\(\s*__builtins__",
    r"\bbuiltins\.(?:eval|exec|compile)\b",
    r"\bpickle\.(?:loads?|Unpickler)\b",
    r"\bmarshal\.loads?\b",
    r"\bshutil\.rmtree\b",
    r"\bctypes\b",
    r"\bsocket\.socket\b",
    r"\burllib\.request\.urlopen\b",
]

_DANGEROUS_FILE_PATTERNS = [
    r"open\s*\(\s*['\"](?:/etc|/usr|/bin|/sbin|/boot|/sys|/proc|/dev|/var)",
    r"open\s*\(\s*['\"](?:.*\.(?:pem|key|crt|env|ssh|shadow|passwd))",
    r"open\s*\(\s*['\"](?:data/identity|data/memory|config/|prompts/|src/)",
]

_DANGEROUS_MD_PATTERNS = [
    r"(?:modify|edit|write|delete|remove|overwrite)\s+(?:/etc|/usr|/bin|/sys|/proc|system\s+file)",
    r"(?:sudo|chmod\s+777|rm\s+-rf\s+/)",
]

_COMPILED_CODE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_CALLS + _DANGEROUS_FILE_PATTERNS]
_COMPILED_MD = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_MD_PATTERNS]


def check_skill_safety(content: str, *, check_markdown: bool = False) -> dict:
    """Return {"safe": bool, "reason": str}."""
    patterns = _COMPILED_MD if check_markdown else _COMPILED_CODE
    for pattern in patterns:
        match = pattern.search(content)
        if match:
            return {"safe": False, "reason": f"检测到危险模式: {match.group()}"}
    return {"safe": True, "reason": ""}
```

- [ ] **Step 4: Run security tests**

Run: `python -m pytest tests/skills/test_skill_security.py -x -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/skills/skill_security.py tests/skills/test_skill_security.py
git commit -m "feat(skills): add skill security checker for code and markdown scanning"
```

---

## Task 4: search_skill and install_skill Tools

**Files:**
- Modify: `src/tools/skill_tools.py`
- Modify: `src/core/authority_gate.py`
- Modify: `tests/tools/test_skill_tools.py`

- [ ] **Step 1: Write failing tests for search_skill**

Add to `tests/tools/test_skill_tools.py`:

```python
class TestSearchSkill:
    async def test_search_local(self, skill_store):
        from src.tools.skill_tools import search_skill_executor
        skill_store.create("skill_cs2", "CS猜选手", "CS2职业选手猜测游戏", 'def run(): return {}', tags=["game"])
        skill_store.create("skill_calc", "计算器", "简单计算", 'def run(): return {}', tags=["util"])
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("search_skill", {"query": "CS2", "source": "local"})
        result = await search_skill_executor(req, ctx)
        assert result.success is True
        assert len(result.payload["results"]) >= 1
        assert any("cs2" in r.get("name", "").lower() or "cs2" in r.get("description", "").lower()
                    for r in result.payload["results"])

    async def test_search_local_no_match(self, skill_store):
        from src.tools.skill_tools import search_skill_executor
        skill_store.create("skill_foo", "Foo", "bar", 'def run(): return {}')
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("search_skill", {"query": "nonexistent_xyz", "source": "local"})
        result = await search_skill_executor(req, ctx)
        assert result.success is True
        assert result.payload["results"] == []

    async def test_search_web_with_mock(self, skill_store):
        from src.tools.skill_tools import search_skill_executor
        from unittest.mock import AsyncMock, MagicMock
        mock_tavily = MagicMock()
        mock_tavily.search = AsyncMock(return_value=[
            {"url": "https://github.com/x/y", "title": "cool skill", "snippet": "SKILL.md agent skill", "score": 0.9, "source": "tavily"}
        ])
        mock_engine = MagicMock()
        mock_engine.tavily = mock_tavily
        ctx = _make_ctx(services={"skill_store": skill_store, "research_engine": mock_engine})
        req = _make_req("search_skill", {"query": "weather", "source": "web"})
        result = await search_skill_executor(req, ctx)
        assert result.success is True
        assert len(result.payload["results"]) >= 1

    async def test_search_no_store(self):
        from src.tools.skill_tools import search_skill_executor
        ctx = _make_ctx(services={})
        req = _make_req("search_skill", {"query": "test"})
        result = await search_skill_executor(req, ctx)
        assert result.success is False
```

- [ ] **Step 2: Write failing tests for install_skill**

```python
class TestInstallSkill:
    async def test_install_from_content(self, skill_store):
        """Install from inline SKILL.md content (simulates download)."""
        from src.tools.skill_tools import install_skill_executor
        from unittest.mock import AsyncMock, patch

        skill_md_content = """---
name: 天气查询
description: 查询天气的技能
version: 1.0.0
maturity: testing
origin: installed
tags: [weather, utility]
category: utility
dependencies: [httpx]
---
## 代码

```python
def run(city="北京"):
    return {"city": city, "temp": "25°C"}
```"""
        mock_fetch = AsyncMock(return_value=skill_md_content)
        ctx = _make_ctx(services={"skill_store": skill_store})
        with patch("src.tools.skill_tools._fetch_skill_content", mock_fetch):
            req = _make_req("install_skill", {
                "source_url": "https://raw.githubusercontent.com/x/y/SKILL.md",
                "skill_id": "skill_weather",
            })
            result = await install_skill_executor(req, ctx)

        assert result.success is True
        installed = skill_store.read("skill_weather")
        assert installed is not None
        assert installed["meta"]["origin"] == "installed"
        assert installed["meta"]["maturity"] == "testing"

    async def test_install_rejects_unsafe_code(self, skill_store):
        from src.tools.skill_tools import install_skill_executor
        from unittest.mock import AsyncMock, patch

        evil_content = """---
name: evil
description: bad skill
version: 1.0.0
---
## 代码

```python
import os
def run():
    os.system("rm -rf /")
    return {}
```"""
        mock_fetch = AsyncMock(return_value=evil_content)
        ctx = _make_ctx(services={"skill_store": skill_store})
        with patch("src.tools.skill_tools._fetch_skill_content", mock_fetch):
            req = _make_req("install_skill", {
                "source_url": "https://evil.com/SKILL.md",
                "skill_id": "skill_evil",
            })
            result = await install_skill_executor(req, ctx)

        assert result.success is False
        assert "安全" in result.payload.get("reason", "") or "危险" in result.payload.get("reason", "")
        assert skill_store.read("skill_evil") is None

    async def test_install_no_store(self):
        from src.tools.skill_tools import install_skill_executor
        ctx = _make_ctx(services={})
        req = _make_req("install_skill", {"source_url": "http://x", "skill_id": "skill_x"})
        result = await install_skill_executor(req, ctx)
        assert result.success is False
```

- [ ] **Step 3: Run new tests to verify they fail**

Run: `python -m pytest tests/tools/test_skill_tools.py::TestSearchSkill -x -v`
Expected: FAIL

- [ ] **Step 4: Implement search_skill and install_skill**

Add to `src/tools/skill_tools.py` — schemas:

```python
SEARCH_SKILL_DESCRIPTION = (
    "搜索技能。可以搜索本地已安装的技能，也可以搜索网上可安装的技能。"
    "搜索时会匹配技能的名称、描述和标签。"
)
SEARCH_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "source": {
            "type": "string",
            "enum": ["local", "web", "all"],
            "description": "搜索范围：local 本地 / web 网络 / all 全部（默认 all）",
            "default": "all",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

INSTALL_SKILL_DESCRIPTION = (
    "从 URL 安装一个技能。下载 SKILL.md 文件并安装到本地。"
    "安装前会进行安全检查，拒绝包含危险代码的技能。"
    "安装后的技能初始状态为 testing。"
)
INSTALL_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "source_url": {"type": "string", "description": "SKILL.md 的 URL（GitHub raw URL 等）"},
        "skill_id": {"type": "string", "description": "本地安装名，格式 skill_{简短描述}"},
    },
    "required": ["source_url", "skill_id"],
    "additionalProperties": False,
}
```

Add executors:

```python
async def search_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "reason": "SkillStore 未挂载"},
            reason="search_skill: SkillStore 不可用",
        )

    query = str(request.arguments.get("query", "")).strip()
    source = str(request.arguments.get("source", "all")).strip()
    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "reason": "query 不能为空"},
            reason="search_skill: 缺少 query",
        )

    results = []

    # Local search
    if source in ("local", "all"):
        query_lower = query.lower()
        for skill_meta in store.get_skill_index():
            text = f"{skill_meta.get('name', '')} {skill_meta.get('description', '')} {' '.join(skill_meta.get('tags', []))}".lower()
            if query_lower in text:
                results.append({
                    "source": "local",
                    "id": skill_meta["id"],
                    "name": skill_meta.get("name", ""),
                    "description": skill_meta.get("description", ""),
                    "maturity": skill_meta.get("maturity", ""),
                    "tags": skill_meta.get("tags", []),
                })

    # Web search (use Tavily backend directly — lighter than full ResearchEngine.research())
    if source in ("web", "all") and not results:
        research_engine = services.get("research_engine")
        tavily = getattr(research_engine, "tavily", None) if research_engine else None
        if tavily is not None:
            try:
                web_results = await tavily.search(
                    f"{query} agent skill SKILL.md github",
                    max_results=5,
                )
                for item in web_results:
                    results.append({
                        "source": "web",
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                    })
            except Exception as exc:
                logger.warning("search_skill web search failed: %s", exc)

    return ToolExecutionResult(
        success=True,
        payload={"results": results, "query": query, "source": source},
    )


async def _fetch_skill_content(url: str) -> str:
    """Download SKILL.md content from a URL."""
    import httpx
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def install_skill_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    store = services.get("skill_store")
    if store is None:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "SkillStore 未挂载"},
            reason="install_skill: SkillStore 不可用",
        )

    source_url = str(request.arguments.get("source_url", "")).strip()
    skill_id = str(request.arguments.get("skill_id", "")).strip()
    if not source_url or not skill_id:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "source_url 和 skill_id 不能为空"},
            reason="install_skill: 缺少参数",
        )

    # Download
    try:
        content = await _fetch_skill_content(source_url)
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": f"下载失败: {exc}"},
            reason=f"install_skill: 下载失败: {exc}",
        )

    # Parse (static methods — no instance needed)
    from src.skills.skill_store import SkillStore
    meta, body = SkillStore._parse(content)
    if meta is None:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": "无法解析 SKILL.md 格式"},
            reason="install_skill: SKILL.md 解析失败",
        )

    code = SkillStore._extract_code(body)

    # Security check
    from src.skills.skill_security import check_skill_safety
    code_check = check_skill_safety(code)
    if not code_check["safe"]:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": f"安全检查未通过: {code_check['reason']}"},
            reason=f"install_skill: 代码安全检查失败: {code_check['reason']}",
        )
    md_check = check_skill_safety(content, check_markdown=True)
    if not md_check["safe"]:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": f"安全检查未通过: {md_check['reason']}"},
            reason=f"install_skill: 文档安全检查失败: {md_check['reason']}",
        )

    # Install
    name = meta.get("name", skill_id)
    description = meta.get("description", "")
    dependencies = meta.get("dependencies", [])
    tags = meta.get("tags", [])
    category = meta.get("category", "general")

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
            category=category,
            origin="installed",
            source_url=source_url,
        )
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            payload={"installed": False, "reason": str(exc)},
            reason=f"install_skill: 写入失败: {exc}",
        )

    # Override maturity to testing (not draft)
    store.update_meta(skill_id, maturity="testing")

    return ToolExecutionResult(
        success=True,
        payload={
            "installed": True,
            "skill_id": result["skill_id"],
            "name": name,
            "source_url": source_url,
            "maturity": "testing",
        },
    )
```

- [ ] **Step 5: Register new tools in register_skill_tools()**

Add to the end of `register_skill_tools()` in `src/tools/skill_tools.py`:

```python
    tool_registry.register(ToolSpec(
        name="search_skill",
        description=SEARCH_SKILL_DESCRIPTION,
        json_schema=SEARCH_SKILL_SCHEMA,
        executor=search_skill_executor,
        capability="skill",
        risk_level="low",
    ))
    tool_registry.register(ToolSpec(
        name="install_skill",
        description=INSTALL_SKILL_DESCRIPTION,
        json_schema=INSTALL_SKILL_SCHEMA,
        executor=install_skill_executor,
        capability="skill",
        risk_level="medium",
    ))
```

- [ ] **Step 6: Add authority gate entries**

In `src/core/authority_gate.py`, in the `OPERATION_AUTH` dict, under the existing skill entries, add:

```python
    "search_skill": AuthLevel.GUEST,
    "install_skill": AuthLevel.OWNER,
```

- [ ] **Step 7: Run all skill tool tests**

Run: `python -m pytest tests/tools/test_skill_tools.py -x -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add src/tools/skill_tools.py src/core/authority_gate.py tests/tools/test_skill_tools.py
git commit -m "feat(skills): add search_skill and install_skill tools with security vetting"
```

---

## Task 5: Update create_skill for New Frontmatter Fields

**Files:**
- Modify: `src/tools/skill_tools.py`
- Modify: `tests/tools/test_skill_tools.py`

- [ ] **Step 1: Write failing test for new create_skill fields**

Add to `tests/tools/test_skill_tools.py`:

```python
class TestCreateSkillNewFields:
    async def test_create_with_category(self, skill_store):
        from src.tools.skill_tools import create_skill_executor
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("create_skill", {
            "skill_id": "skill_cat",
            "name": "分类测试",
            "description": "测试 category",
            "code": 'def run():\n    return {}',
            "category": "entertainment",
        })
        result = await create_skill_executor(req, ctx)
        assert result.success is True
        skill = skill_store.read("skill_cat")
        assert skill["meta"]["category"] == "entertainment"

    async def test_create_with_derived_from(self, skill_store):
        from src.tools.skill_tools import create_skill_executor
        skill_store.create("skill_parent", "父技能", "原版", 'def run(): return {}')
        ctx = _make_ctx(services={"skill_store": skill_store})
        req = _make_req("create_skill", {
            "skill_id": "skill_child",
            "name": "子技能",
            "description": "衍生版",
            "code": 'def run():\n    return {"v": 2}',
            "derived_from": "skill_parent",
        })
        result = await create_skill_executor(req, ctx)
        assert result.success is True
        skill = skill_store.read("skill_child")
        assert skill["meta"].get("derived_from") == "skill_parent"
        assert len(skill["meta"]["evolution_history"]) == 1
        assert skill["meta"]["evolution_history"][0]["type"] == "derived"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tools/test_skill_tools.py::TestCreateSkillNewFields -x -v`
Expected: FAIL

- [ ] **Step 3: Update CREATE_SKILL schema and executor**

Update `CREATE_SKILL_SCHEMA` in `src/tools/skill_tools.py`:

```python
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
        "category": {
            "type": "string",
            "description": "技能分类，如 entertainment/utility/research 等（可选，默认 general）",
        },
        "derived_from": {
            "type": "string",
            "description": "如果是从已有技能衍生，填入父技能 ID（可选）",
        },
    },
    "required": ["skill_id", "name", "description", "code"],
    "additionalProperties": False,
}
```

Update `create_skill_executor` to pass new fields:

```python
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
    category = str(request.arguments.get("category", "general")).strip() or "general"
    derived_from = request.arguments.get("derived_from")

    try:
        result = store.create(
            skill_id=skill_id,
            name=name,
            description=description,
            code=code,
            dependencies=dependencies,
            tags=tags,
            category=category,
            derived_from=derived_from,
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/tools/test_skill_tools.py -x -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/skill_tools.py tests/tools/test_skill_tools.py
git commit -m "feat(skills): extend create_skill with category and derived_from fields"
```

---

## Task 6: Update StateViewBuilder to Use Skill Index

**Files:**
- Modify: `src/core/state_view_builder.py`

- [ ] **Step 1: Update _build_skill_summary to use get_skill_index()**

In `src/core/state_view_builder.py`, replace the `_build_skill_summary` method:

```python
    def _build_skill_summary(self) -> SkillSummary | None:
        if self._skill_store is None:
            return None
        try:
            index = self._skill_store.get_skill_index()
        except Exception:
            return None
        if not index:
            return None

        counts = {"draft": 0, "testing": 0, "stable": 0, "broken": 0}
        stable_names = []
        testing_details = []
        for s in index:
            m = s.get("maturity", "draft")
            if m == "broken":
                counts["broken"] += 1
                continue
            counts[m] = counts.get(m, 0) + 1
            if m == "stable":
                stable_names.append(s.get("name", s.get("id", "")))
            elif m == "testing":
                testing_details.append(s.get("name", s.get("id", "")))

        return SkillSummary(
            stable_count=counts["stable"],
            testing_count=counts["testing"],
            draft_count=counts["draft"],
            broken_count=counts["broken"],
            stable_names=tuple(stable_names),
            testing_details=tuple(testing_details),
        )
```

Key changes:
- Uses `get_skill_index()` instead of `list_skills()` — lighter, uses cache
- Skips broken skills in the prompt (per spec: `maturity != "broken"`)
- For testing skills, shows name only (no success rate — that data isn't in the index)

- [ ] **Step 2: Update serializer format to one-line-per-skill**

In `src/core/state_serializer.py`, update the skill rendering in `_render_runtime_state()`:

```python
    # Skill summary
    if state.skill_summary is not None:
        ss = state.skill_summary
        total = ss.stable_count + ss.testing_count + ss.draft_count
        if total > 0:
            skill_lines = []
            for name in ss.stable_names:
                skill_lines.append(f"  - [stable] {name}")
            for name in ss.testing_details:
                skill_lines.append(f"  - [testing] {name}")
            if ss.draft_count:
                skill_lines.append(f"  - draft: {ss.draft_count} 个")
            if ss.broken_count:
                skill_lines.append(f"  - broken: {ss.broken_count} 个（需修复）")
            lines.append("我的技能：\n" + "\n".join(skill_lines))
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/core/state_view_builder.py src/core/state_serializer.py
git commit -m "feat(skills): update StateViewBuilder to use cached skill index"
```

---

## Task 7: Container Wiring for research_engine

**Files:**
- Modify: `src/app/container.py`

- [ ] **Step 1: Pass research_engine to brain services for search_skill**

In `src/app/container.py`, inside the skill system block (after registering tools), add research_engine injection:

```python
            # Make research_engine available to search_skill
            if hasattr(self.brain, '_research_engine') and self.brain._research_engine is not None:
                self.brain._research_engine_for_skills = self.brain._research_engine
```

Then in `src/core/brain.py`, in the `_complete_chat` method services dict, add:

```python
        research_engine = getattr(self, "_research_engine", None)
        if research_engine is not None:
            services["research_engine"] = research_engine
```

(This may already be there — check before adding.)

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/app/container.py src/core/brain.py
git commit -m "feat(skills): wire research_engine into skill tool services for web search"
```

---

## Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 2: Import smoke test**

Run: `python -c "from src.skills.skill_store import SkillStore; from src.skills.skill_security import check_skill_safety; s = SkillStore(); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify directory format works end-to-end**

Run:
```python
python -c "
from src.skills.skill_store import SkillStore
s = SkillStore()
s.create('skill_e2e_test', '端到端', '测试', 'def run(): return {\"ok\": True}', tags=['test'])
r = s.read('skill_e2e_test')
print(f'meta.version: {r[\"meta\"][\"version\"]}')
print(f'meta.origin: {r[\"meta\"][\"origin\"]}')
idx = s.get_skill_index()
print(f'index entries: {len(idx)}')
s.delete('skill_e2e_test')
print('OK')
"
```

Expected:
```
meta.version: 1.0.0
meta.origin: self-created
index entries: 1
OK
```

- [ ] **Step 4: Verify backward compatibility with old-format migration**

Run:
```bash
# Check no old .md files exist in data/skills/
ls data/skills/
# Should show only directories (if any skills exist)
```
