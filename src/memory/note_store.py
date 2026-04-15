import uuid
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 时区：台北
_TZ = ZoneInfo("Asia/Taipei")


class NoteStore:
    """记忆树的文件层管理。"""

    def __init__(self, notes_dir=None):
        """
        notes_dir: 笔记目录路径，默认为 data/memory/notes。
        接受 Path 或 str，目录不存在时自动创建。
        """
        self.notes_dir = Path(notes_dir) if notes_dir else Path("data/memory/notes")
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        content: str,
        note_type: str = "observation",
        path: str = None,
        source_refs: list[str] = None,
        trust: str = "self",
        parent_note: str = None,
    ) -> dict:
        """
        写入笔记，自动生成 YAML frontmatter。

        - note_id 格式：note_{YYYYMMDD_HHMMSS}_{4hex}
        - 文件名格式：{note_type}_{YYYYMMDD_HHMMSS}_{4hex}.md
        - 时区：Asia/Taipei
        - 若指定 path（如 "people/kevin"），在子目录下创建
        - frontmatter 字段：id, created_at, updated_at, actor, note_type,
          source_refs, trust, embedding_version, parent_note

        返回：{"note_id": str, "file_path": str}
        """
        now = datetime.now(tz=_TZ)
        ts = now.strftime("%Y%m%d_%H%M%S")
        suffix = uuid.uuid4().hex[:4]

        note_id = f"note_{ts}_{suffix}"
        filename = f"{note_type}_{ts}_{suffix}.md"

        # 确定目标目录
        if path:
            target_dir = self.notes_dir / path
        else:
            target_dir = self.notes_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        file_path = target_dir / filename

        # 构建 frontmatter
        meta = {
            "id": note_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "actor": "lapwing",
            "note_type": note_type,
            "source_refs": source_refs or [],
            "trust": trust,
            "embedding_version": "pending",
            "parent_note": parent_note,
        }

        frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        raw = f"---\n{frontmatter}---\n{content}"

        file_path.write_text(raw, encoding="utf-8")

        return {"note_id": note_id, "file_path": str(file_path.resolve())}

    def read(self, note_id_or_path: str) -> dict | None:
        """
        通过 note_id 或文件路径读取笔记。

        返回：{"meta": dict, "content": str, "file_path": str} 或 None
        """
        file_path = self._resolve_path(note_id_or_path)
        if file_path is None or not file_path.exists():
            return None

        raw = file_path.read_text(encoding="utf-8")
        meta, content = self._parse_note(raw)

        return {
            "meta": meta or {},
            "content": content,
            "file_path": str(file_path.resolve()),
        }

    def _resolve_path(self, note_id_or_path: str) -> Path | None:
        """
        按以下顺序定位文件：
        1. 尝试作为绝对路径
        2. 尝试作为 notes_dir 下的相对路径
        3. 遍历所有 .md 文件，在 frontmatter 中匹配 note_id
        """
        # 1. 绝对路径
        p = Path(note_id_or_path)
        if p.is_absolute() and p.exists():
            return p

        # 2. notes_dir 下的相对路径
        relative = self.notes_dir / note_id_or_path
        if relative.exists():
            return relative

        # 3. 按 note_id 搜索
        for md_file in self.notes_dir.rglob("*.md"):
            try:
                raw = md_file.read_text(encoding="utf-8")
                meta, _ = self._parse_note(raw)
                if meta and meta.get("id") == note_id_or_path:
                    return md_file
            except Exception:
                continue

        return None

    def _parse_note(self, raw: str) -> tuple[dict | None, str]:
        """解析 ---frontmatter--- + 正文，返回 (meta_dict, content_str)。"""
        if not raw.startswith("---"):
            return None, raw

        # 找到结束的 ---
        end = raw.find("\n---\n", 3)
        if end == -1:
            return None, raw

        frontmatter_str = raw[3:end]  # 去掉开头 ---
        content = raw[end + 5:]       # 跳过 \n---\n

        try:
            meta = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError:
            return None, raw

        return meta, content
