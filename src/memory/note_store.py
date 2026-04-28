import logging
import re
import uuid
import yaml
from pathlib import Path

from src.core.time_utils import now as local_now

logger = logging.getLogger("lapwing.memory.note_store")


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
        - 时区：默认本地时区
        - 若指定 path（如 "people/kevin"），在子目录下创建
        - frontmatter 字段：id, created_at, updated_at, actor, note_type,
          source_refs, trust, embedding_version, parent_note

        返回：{"note_id": str, "file_path": str}
        """
        now = local_now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        suffix = uuid.uuid4().hex[:4]

        note_id = f"note_{ts}_{suffix}"
        filename = f"{note_type}_{ts}_{suffix}.md"

        # 确定目标目录
        if path:
            # path 是分类目录，不能与笔记文件名约定冲突。
            # 任意片段以 .md 结尾会造成目录与笔记文件同名，
            # 后续 rglob("*.md") 等遍历会把目录当成文件读取。
            for part in Path(path).parts:
                if part.endswith(".md"):
                    raise ValueError(
                        f"path 片段不能以 .md 结尾（与笔记文件命名冲突）：{part}"
                    )
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
            except Exception as e:
                logger.warning("笔记操作失败 [_resolve_path 遍历 %s]: %s", md_file, e, exc_info=True)
                continue

        return None

    def edit(self, note_id_or_path: str, new_content: str) -> dict:
        """
        编辑笔记正文。保留 frontmatter，更新 updated_at，设置 embedding_version 为 "pending"。
        返回：{"success": bool, "reason": str}
        """
        file_path = self._resolve_path(note_id_or_path)
        if file_path is None or not file_path.exists():
            return {"success": False, "reason": "笔记不存在"}

        raw = file_path.read_text(encoding="utf-8")
        meta, _ = self._parse_note(raw)
        if meta is None:
            meta = {}

        now = local_now()
        meta["updated_at"] = now.isoformat()
        meta["embedding_version"] = "pending"

        frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        file_path.write_text(f"---\n{frontmatter}---\n{new_content}", encoding="utf-8")

        return {"success": True, "reason": ""}

    def list_notes(self, path: str = None) -> list[dict]:
        """
        列出目录条目。path=None 表示根 notes_dir。
        返回：[{"name": str, "type": "file"|"dir", "note_id": str|None}]
        - 跳过点文件
        - 对 .md 文件从 frontmatter 提取 note_id
        - 按名称排序
        - 目录不存在时返回 []
        """
        target = self.notes_dir / path if path else self.notes_dir
        if not target.exists() or not target.is_dir():
            return []

        entries = []
        for item in sorted(target.iterdir(), key=lambda p: p.name):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                entries.append({"name": item.name, "type": "dir", "note_id": None})
            elif item.is_file() and item.suffix == ".md":
                note_id = None
                try:
                    raw = item.read_text(encoding="utf-8")
                    meta, _ = self._parse_note(raw)
                    if meta:
                        note_id = meta.get("id")
                except Exception as e:
                    logger.warning("笔记操作失败 [list_notes 读取 %s]: %s", item, e, exc_info=True)
                entries.append({"name": item.name, "type": "file", "note_id": note_id})

        return entries

    def move(self, note_id_or_path: str, new_path: str) -> dict:
        """
        将笔记移动到 notes_dir 下的新子目录。
        new_path: 目标子目录（如 "people/friends"）
        目标目录不存在时自动创建。
        返回：{"success": bool, "reason": str, "new_path": str (绝对路径)}
        """
        file_path = self._resolve_path(note_id_or_path)
        if file_path is None or not file_path.exists():
            return {"success": False, "reason": "笔记不存在", "new_path": ""}

        target_dir = self.notes_dir / new_path
        target_dir.mkdir(parents=True, exist_ok=True)

        dest = target_dir / file_path.name
        file_path.rename(dest)

        return {"success": True, "reason": "", "new_path": str(dest.resolve())}

    def search_keyword(self, keyword: str, limit: int = 10) -> list[dict]:
        """
        在所有笔记中进行大小写不敏感的关键字搜索。
        返回：[{"note_id": str|None, "file_path": str, "snippet": str}]
        """
        results = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        for md_file in sorted(self.notes_dir.rglob("*.md")):
            if len(results) >= limit:
                break
            try:
                raw = md_file.read_text(encoding="utf-8")
                meta, content = self._parse_note(raw)
                if pattern.search(content):
                    note_id = meta.get("id") if meta else None
                    snippet = self._extract_snippet(content, keyword)
                    results.append({
                        "note_id": note_id,
                        "file_path": str(md_file.resolve()),
                        "snippet": snippet,
                    })
            except Exception as e:
                logger.warning("笔记操作失败 [search_keyword %s]: %s", md_file, e, exc_info=True)
                continue

        return results

    def get_all_for_embedding(self) -> list[dict]:
        """获取所有 embedding_version == "pending" 的笔记。
        返回：[{"note_id", "file_path", "content", "meta"}]
        """
        results = []
        for md_file in self.notes_dir.rglob("*.md"):
            try:
                raw = md_file.read_text(encoding="utf-8")
                meta, content = self._parse_note(raw)
                if meta and meta.get("embedding_version") == "pending":
                    results.append({
                        "note_id": meta.get("id"),
                        "file_path": str(md_file.resolve()),
                        "content": content,
                        "meta": meta,
                    })
            except Exception as e:
                logger.warning("笔记操作失败 [get_all_for_embedding %s]: %s", md_file, e, exc_info=True)
                continue
        return results

    def mark_embedded(self, file_path: str, version: str = "v1"):
        """将笔记的 embedding_version 字段更新为指定版本。"""
        p = Path(file_path)
        if not p.exists():
            return

        raw = p.read_text(encoding="utf-8")
        meta, content = self._parse_note(raw)
        if meta is None:
            return

        meta["embedding_version"] = version
        frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        p.write_text(f"---\n{frontmatter}---\n{content}", encoding="utf-8")

    def _extract_snippet(self, content: str, keyword: str, context_chars: int = 100) -> str:
        """提取关键字匹配周围的片段，截断时添加 ... 前缀/后缀。"""
        match = re.search(re.escape(keyword), content, re.IGNORECASE)
        if not match:
            return content[:context_chars]

        start = max(0, match.start() - context_chars // 2)
        end = min(len(content), match.end() + context_chars // 2)

        snippet = content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        return snippet

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
