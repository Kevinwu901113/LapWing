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
