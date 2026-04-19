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

    @staticmethod
    def _validate_skill_id(skill_id: str) -> None:
        if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
            raise ValueError(f"Invalid skill_id: {skill_id}")

    def create(self, skill_id: str, name: str, description: str, code: str,
               dependencies: list[str] | None = None, tags: list[str] | None = None) -> dict:
        self._validate_skill_id(skill_id)
        now = datetime.now(tz=_TZ)
        meta = {
            "id": skill_id, "name": name, "description": description,
            "maturity": "draft", "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "usage_count": 0, "success_count": 0,
            "last_error": None, "last_error_at": None, "last_tested_at": None,
            "dependencies": dependencies or [], "tags": tags or [], "author": "lapwing",
        }
        file_path = self.skills_dir / f"{skill_id}.md"
        self._write_file(file_path, meta, code)
        return {"skill_id": skill_id, "file_path": str(file_path.resolve())}

    def read(self, skill_id: str) -> dict | None:
        self._validate_skill_id(skill_id)
        file_path = self.skills_dir / f"{skill_id}.md"
        if not file_path.exists():
            return None
        raw = file_path.read_text(encoding="utf-8")
        meta, body = self._parse(raw)
        if meta is None:
            return None
        code = self._extract_code(body)
        return {"meta": meta, "code": code, "file_path": str(file_path.resolve())}

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
        self._validate_skill_id(skill_id)
        file_path = self.skills_dir / f"{skill_id}.md"
        if not file_path.exists():
            return {"success": False, "reason": "技能不存在"}
        file_path.unlink()
        return {"success": True, "reason": ""}

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
