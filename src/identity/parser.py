from __future__ import annotations

# 身份 Markdown 解析器 — 确定性块提取 + LLM 分类缓存 + 重建/差异/校验
# Identity markdown parser — deterministic block extraction + LLM classification cache + rebuild/diff/validate

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.identity.models import (
    ClaimRevision,
    RevisionAction,
    compute_claim_id_from_key,
    compute_raw_block_id,
)

logger = logging.getLogger("lapwing.identity.parser")

# ---------------------------------------------------------------------------
# 正则表达式
# ---------------------------------------------------------------------------

# 内联方括号元数据: [key=value]
_RE_INLINE_BRACKET = re.compile(r"\[(\w+)=([^\]]+)\]")

# HTML 注释锚点: <!-- claim: key -->
_RE_CLAIM_ANCHOR = re.compile(r"<!--\s*claim:\s*(\S+)\s*-->")

# 节级默认值: <!-- claim-defaults: key=val key=val ... -->
_RE_SECTION_DEFAULTS = re.compile(r"<!--\s*claim-defaults:\s*(.+?)\s*-->")

# frontmatter 分隔符
_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 标题行
_RE_HEADING = re.compile(r"^#{1,6}\s+")

# 列表项行
_RE_LIST_ITEM = re.compile(r"^-\s+")

# 键值对（用于 section defaults）
_RE_KV_PAIR = re.compile(r"(\w+)=(\S+)")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class RawBlock:
    """确定性解析层输出的原始块。"""
    source_file: str
    stable_block_key: str
    raw_block_id: str
    text: str                       # 原始文本内容
    source_span: tuple[int, int]    # UTF-8 字节偏移 (start, end)
    inline_metadata: dict           # 从 [key=val] 方括号提取
    section_defaults: dict          # 从 <!-- claim-defaults: ... --> 提取
    defaults: dict                  # 从 frontmatter claim_defaults 提取

    def effective_metadata(self) -> dict:
        """合并优先级: inline > section_defaults > frontmatter defaults。"""
        merged = {}
        merged.update(self.defaults)
        merged.update(self.section_defaults)
        merged.update(self.inline_metadata)
        return merged


@dataclass
class ExtractionCacheKey:
    """LLM 分类缓存键，由多维度组合计算。"""
    candidate_text_sha: str
    section_context_sha: str
    frontmatter_defaults_sha: str
    prompt_version: str
    model_id: str
    schema_version: str

    def compute(self) -> str:
        """SHA-256 of all fields, first 16 hex chars."""
        payload = "::".join([
            self.candidate_text_sha,
            self.section_context_sha,
            self.frontmatter_defaults_sha,
            self.prompt_version,
            self.model_id,
            self.schema_version,
        ])
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class RebuildReport:
    """重建报告。"""
    created: int = 0
    updated: int = 0
    deprecated: int = 0
    skipped_tombstoned: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ValidateResult:
    """校验结果。"""
    passed: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 frontmatter YAML 并返回 (claim_defaults, 剩余文本)。

    简单实现：用 yaml.safe_load 解析 frontmatter 块。
    如果没有 frontmatter，返回空字典和原始文本。
    """
    m = _RE_FRONTMATTER.match(text)
    if not m:
        return {}, text

    frontmatter_raw = m.group(1)
    remainder = text[m.end():]

    try:
        import yaml
        parsed = yaml.safe_load(frontmatter_raw)
    except Exception:
        # 回退：简单键值解析
        parsed = {}
        for line in frontmatter_raw.strip().splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                parsed[k.strip()] = v.strip()

    if not isinstance(parsed, dict):
        return {}, remainder

    return parsed.get("claim_defaults", {}), remainder


def _parse_section_defaults_kv(kv_str: str) -> dict:
    """解析 'key=val key=val' 格式为字典。"""
    result = {}
    for m in _RE_KV_PAIR.finditer(kv_str):
        result[m.group(1)] = m.group(2)
    return result


def _extract_inline_metadata(line: str) -> tuple[dict, str]:
    """提取行首 [key=val] 序列，返回 (元数据字典, 剩余文本)。

    仅提取行首连续的方括号标记（列表项前缀 '- ' 之后）。
    """
    metadata = {}
    # 去除列表项前缀
    text = line
    prefix = ""
    list_m = _RE_LIST_ITEM.match(text)
    if list_m:
        prefix = list_m.group(0)
        text = text[len(prefix):]

    # 持续提取开头的 [key=val]
    while text.startswith("["):
        bracket_m = re.match(r"\[(\w+)=([^\]]+)\]", text)
        if not bracket_m:
            break
        metadata[bracket_m.group(1)] = bracket_m.group(2)
        text = text[bracket_m.end():]

    # 去除前导空格
    text = text.lstrip()
    return metadata, text


def _compute_fallback_key(text: str) -> str:
    """用文本的 SHA-256 前 12 个十六进制字符作为回退 stable_block_key。"""
    canonical = text.strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _sha256_str(s: str) -> str:
    """字符串 SHA-256 前 16 个十六进制字符。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# IdentityParser
# ---------------------------------------------------------------------------

class IdentityParser:
    """身份 Markdown 解析器。

    - parse_text(): 确定性解析，不需要 store
    - classify_block(): LLM 分类（或无 LLM 时用默认值）
    - rebuild(): 从文件重建主张到 store
    - validate(): 校验文件格式
    """

    def __init__(
        self,
        *,
        store=None,
        identity_dir: Path | None = None,
        llm_router=None,
        prompt_version: str = "v1",
        model_id: str = "default",
        schema_version: str = "s1",
    ):
        self._store = store
        self._identity_dir = identity_dir
        self._llm_router = llm_router
        self._prompt_version = prompt_version
        self._model_id = model_id
        self._schema_version = schema_version

    # ------------------------------------------------------------------
    # 确定性解析层 (Task 11)
    # ------------------------------------------------------------------

    def parse_text(self, md_text: str, source_file: str) -> list[RawBlock]:
        """解析 Markdown 文本为 RawBlock 列表。

        确定性层：正则提取、元数据解析、ID 计算。不需要 store。
        """
        if not md_text.strip():
            return []

        # 解析 frontmatter
        claim_defaults, body = _parse_frontmatter(md_text)
        if not isinstance(claim_defaults, dict):
            claim_defaults = {}

        # 计算 body 在原始文本中的 UTF-8 字节偏移基准
        body_byte_offset = len(md_text.encode("utf-8")) - len(body.encode("utf-8"))

        # 按行处理
        lines = body.split("\n")
        blocks: list[RawBlock] = []
        current_section_defaults: dict = {}
        pending_anchor: str | None = None

        # 记录行→字节偏移映射（基于 body 的起始位置）
        line_byte_offsets: list[int] = []
        offset = 0
        for line in lines:
            line_byte_offsets.append(offset)
            offset += len(line.encode("utf-8")) + 1  # +1 for '\n'

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 空行
            if not stripped:
                i += 1
                continue

            # 标题行 → 重置 section defaults
            if _RE_HEADING.match(stripped):
                current_section_defaults = {}
                i += 1
                continue

            # HTML 注释: claim-defaults
            section_m = _RE_SECTION_DEFAULTS.match(stripped)
            if section_m:
                current_section_defaults = _parse_section_defaults_kv(section_m.group(1))
                i += 1
                continue

            # HTML 注释: claim anchor
            anchor_m = _RE_CLAIM_ANCHOR.match(stripped)
            if anchor_m:
                pending_anchor = anchor_m.group(1)
                i += 1
                continue

            # 列表项 → 一个块
            if _RE_LIST_ITEM.match(stripped):
                block_text = line
                block_start_line = i
                # 提取内联元数据
                inline_meta, clean_text = _extract_inline_metadata(stripped)
                # stable_block_key
                stable_key = (
                    pending_anchor
                    or inline_meta.pop("id", None)
                    or _compute_fallback_key(clean_text)
                )
                pending_anchor = None

                # 字节偏移
                byte_start = body_byte_offset + line_byte_offsets[i]
                byte_end = byte_start + len(block_text.encode("utf-8"))

                blocks.append(RawBlock(
                    source_file=source_file,
                    stable_block_key=stable_key,
                    raw_block_id=compute_raw_block_id(source_file, stable_key),
                    text=block_text,
                    source_span=(byte_start, byte_end),
                    inline_metadata=inline_meta,
                    section_defaults=dict(current_section_defaults),
                    defaults=dict(claim_defaults),
                ))
                i += 1
                continue

            # 段落文本 → 一个块（连续非空、非标题、非注释行）
            para_lines = [line]
            block_start_line = i
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if (
                    not next_line
                    or _RE_HEADING.match(next_line)
                    or _RE_SECTION_DEFAULTS.match(next_line)
                    or _RE_CLAIM_ANCHOR.match(next_line)
                    or _RE_LIST_ITEM.match(next_line)
                ):
                    break
                para_lines.append(lines[i])
                i += 1

            block_text = "\n".join(para_lines)
            inline_meta, clean_text = _extract_inline_metadata(para_lines[0].strip())
            stable_key = (
                pending_anchor
                or inline_meta.pop("id", None)
                or _compute_fallback_key(block_text)
            )
            pending_anchor = None

            byte_start = body_byte_offset + line_byte_offsets[block_start_line]
            byte_end = byte_start + len(block_text.encode("utf-8"))

            blocks.append(RawBlock(
                source_file=source_file,
                stable_block_key=stable_key,
                raw_block_id=compute_raw_block_id(source_file, stable_key),
                text=block_text,
                source_span=(byte_start, byte_end),
                inline_metadata=inline_meta,
                section_defaults=dict(current_section_defaults),
                defaults=dict(claim_defaults),
            ))

        return blocks

    # ------------------------------------------------------------------
    # LLM 分类层 (Task 12)
    # ------------------------------------------------------------------

    async def classify_block(self, block: RawBlock) -> dict:
        """分类 RawBlock（LLM 调用或缓存命中）。

        1. 计算缓存键
        2. 检查 store extraction cache（如有）
        3. 命中则返回缓存结果
        4. 未命中且有 llm_router 则调用 LLM
        5. 无 llm_router 则从 effective_metadata 提取默认值
        6. 缓存结果（如有 store）
        """
        # 缓存键
        cache_key_obj = ExtractionCacheKey(
            candidate_text_sha=_sha256_str(block.text),
            section_context_sha=_sha256_str(str(block.section_defaults)),
            frontmatter_defaults_sha=_sha256_str(str(block.defaults)),
            prompt_version=self._prompt_version,
            model_id=self._model_id,
            schema_version=self._schema_version,
        )
        cache_key = cache_key_obj.compute()

        # 检查缓存
        if self._store is not None:
            cached = await self._store.get_extraction_cache(cache_key)
            if cached is not None:
                return cached

        # 无 LLM router → 从 effective_metadata 提取默认值
        if self._llm_router is None:
            meta = block.effective_metadata()
            result = {
                "type": meta.get("type", "belief"),
                "owner": meta.get("owner", "lapwing"),
                "confidence": float(meta.get("confidence", 0.5)),
                "sensitivity": meta.get("sensitivity", "public"),
            }
        else:
            # TODO: 实现 LLM 调用分类
            # 目前回退到默认值
            meta = block.effective_metadata()
            result = {
                "type": meta.get("type", "belief"),
                "owner": meta.get("owner", "lapwing"),
                "confidence": float(meta.get("confidence", 0.5)),
                "sensitivity": meta.get("sensitivity", "public"),
            }

        # 缓存结果
        if self._store is not None:
            await self._store.set_extraction_cache(cache_key, result)

        return result

    # ------------------------------------------------------------------
    # 重建流程 (Task 13)
    # ------------------------------------------------------------------

    async def rebuild(self, auth) -> RebuildReport:
        """从 Markdown 文件全量重建主张。

        1. 扫描 identity_dir 下的 .md 文件
        2. 逐文件解析 → 逐块比较 → 创建/更新修订
        3. 更新 identity_source_files 和 identity_claim_sources
        """
        from src.identity.auth import check_scope
        check_scope(auth, "identity.write")

        if self._store is None:
            raise ValueError("rebuild 需要 store")
        if self._identity_dir is None:
            raise ValueError("rebuild 需要 identity_dir")

        report = RebuildReport()

        # 扫描所有 .md 文件（含子目录）
        md_files = sorted(self._identity_dir.rglob("*.md"))

        for md_path in md_files:
            try:
                await self._rebuild_file(md_path, auth, report)
            except Exception as e:
                report.errors.append(f"{md_path.name}: {e}")
                logger.warning("rebuild file %s failed: %s", md_path, e)

        return report

    async def _rebuild_file(
        self,
        md_path: Path,
        auth,
        report: RebuildReport,
    ) -> None:
        """重建单个文件中的主张。"""
        # 读取文件
        content = md_path.read_text(encoding="utf-8")
        file_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # 计算相对路径（相对于 identity_dir）
        rel_path = str(md_path.relative_to(self._identity_dir))

        # 解析
        blocks = self.parse_text(content, rel_path)

        # 获取 tombstone 列表
        tombstoned_ids = await self._get_tombstoned_claim_ids()

        now = datetime.now(timezone.utc).isoformat()

        # 收集本次重建后该文件中出现的 claim_id（用于弃用孤儿主张）
        seen_claim_ids: set[str] = set()

        for block in blocks:
            claim_local_key = "claim_0"
            claim_id = compute_claim_id_from_key(
                rel_path, block.stable_block_key, claim_local_key
            )

            seen_claim_ids.add(claim_id)

            # 检查 tombstone
            if claim_id in tombstoned_ids:
                report.skipped_tombstoned += 1
                continue

            # 分类
            classification = await self.classify_block(block)

            # 构建 object_val（去除方括号元数据后的纯文本）
            _, clean_text = _extract_inline_metadata(block.text.strip())
            # 去除列表项前缀
            if _RE_LIST_ITEM.match(block.text.strip()):
                list_m = _RE_LIST_ITEM.match(block.text.strip())
                raw_after_prefix = block.text.strip()[list_m.end():]
                _, clean_text = _extract_inline_metadata(raw_after_prefix)

            object_val = clean_text.strip()

            # 检查现有主张
            existing = await self._store.get_claim(claim_id, auth)

            if existing is not None:
                # 比较 object_val（去除尾部空白）
                existing_val = (existing.object_val or "").strip()
                if existing_val == object_val:
                    # 无变化 → 不产生修订，但更新 provenance
                    await self._store.upsert_claim_source(
                        claim_id=claim_id,
                        source_file=rel_path,
                        byte_start=block.source_span[0],
                        byte_end=block.source_span[1],
                        sha256=file_sha,
                        stable_block_key=block.stable_block_key,
                    )
                    continue

                # 文本变化 → UPDATE 修订
                old_snapshot = {
                    "object_val": existing.object_val,
                    "status": existing.status if isinstance(existing.status, str) else existing.status.value,
                }
                new_snapshot = self._build_snapshot(
                    claim_id=claim_id,
                    block=block,
                    classification=classification,
                    object_val=object_val,
                    rel_path=rel_path,
                    claim_local_key=claim_local_key,
                    created_at=existing.created_at,
                )
                revision = ClaimRevision(
                    revision_id=str(uuid4()),
                    claim_id=claim_id,
                    action=RevisionAction.UPDATED,
                    old_snapshot=old_snapshot,
                    new_snapshot=new_snapshot,
                    actor=auth.actor,
                    reason="rebuild: text changed",
                    created_at=now,
                )
                await self._store.append_revision(revision, auth)
                report.updated += 1
            else:
                # 新主张 → CREATE 修订
                new_snapshot = self._build_snapshot(
                    claim_id=claim_id,
                    block=block,
                    classification=classification,
                    object_val=object_val,
                    rel_path=rel_path,
                    claim_local_key=claim_local_key,
                    created_at=now,
                )
                revision = ClaimRevision(
                    revision_id=str(uuid4()),
                    claim_id=claim_id,
                    action=RevisionAction.CREATED,
                    old_snapshot=None,
                    new_snapshot=new_snapshot,
                    actor=auth.actor,
                    reason="rebuild: new claim",
                    created_at=now,
                )
                await self._store.append_revision(revision, auth)
                report.created += 1

            # 更新 provenance
            await self._store.upsert_claim_source(
                claim_id=claim_id,
                source_file=rel_path,
                byte_start=block.source_span[0],
                byte_end=block.source_span[1],
                sha256=file_sha,
                stable_block_key=block.stable_block_key,
            )

        # 弃用该文件中不再出现的活跃主张（孤儿清理）
        from src.identity.models import ClaimStatus
        all_claims = await self._store.list_claims(auth, status=ClaimStatus.ACTIVE.value)
        for claim in all_claims:
            if claim.source_file == rel_path and claim.claim_id not in seen_claim_ids:
                if claim.claim_id not in tombstoned_ids:
                    await self._store.deprecate_claim(
                        claim.claim_id, auth, "rebuild: no longer in source file"
                    )
                    report.deprecated += 1

        # 更新 identity_source_files
        await self._upsert_source_file(rel_path, file_sha)

    def _build_snapshot(
        self,
        *,
        claim_id: str,
        block: RawBlock,
        classification: dict,
        object_val: str,
        rel_path: str,
        claim_local_key: str,
        created_at: str,
    ) -> dict:
        """构建修订快照字典。"""
        return {
            "claim_id": claim_id,
            "raw_block_id": block.raw_block_id,
            "claim_local_key": claim_local_key,
            "source_file": rel_path,
            "stable_block_key": block.stable_block_key,
            "claim_type": classification.get("type", "belief"),
            "owner": classification.get("owner", "lapwing"),
            "predicate": "",
            "object_val": object_val,
            "confidence": classification.get("confidence", 0.5),
            "sensitivity": classification.get("sensitivity", "public"),
            "status": "active",
            "tags": [],
            "created_at": created_at,
        }

    async def _get_tombstoned_claim_ids(self) -> set[str]:
        """从 identity_redaction_tombstones 表读取所有被 tombstone 的 claim_id。"""
        tombstones = await self._store._list_tombstones()
        return {t["claim_id"] for t in tombstones}

    async def _upsert_source_file(self, file_path: str, sha256: str) -> None:
        """更新 identity_source_files 中的文件 SHA。"""
        now = datetime.now(timezone.utc).isoformat()
        await self._store._db.execute(
            "INSERT OR REPLACE INTO identity_source_files "
            "(file_path, sha256, last_parsed_at) VALUES (?, ?, ?)",
            (file_path, sha256, now),
        )
        await self._store._db.commit()

    # ------------------------------------------------------------------
    # 校验 (Task 13)
    # ------------------------------------------------------------------

    def validate(
        self,
        *,
        strict: bool = False,
        production_files: list[str] | None = None,
    ) -> ValidateResult:
        """校验身份文件。

        --strict 模式下，production_files 中的每个块必须有显式 [id=...] 或 <!-- claim: ... -->。
        """
        if self._identity_dir is None:
            return ValidateResult(passed=True)

        warnings: list[str] = []
        errors: list[str] = []
        prod_set = set(production_files or [])

        for md_path in sorted(self._identity_dir.rglob("*.md")):
            rel_path = str(md_path.relative_to(self._identity_dir))

            if rel_path not in prod_set:
                continue

            try:
                content = md_path.read_text(encoding="utf-8")
            except Exception as e:
                errors.append(f"Cannot read {rel_path}: {e}")
                continue

            blocks = self.parse_text(content, rel_path)
            for block in blocks:
                # 检查是否有显式 id
                has_explicit_id = (
                    "id" in block.inline_metadata
                    or len(block.stable_block_key) != 12  # 不是 sha256 回退
                )
                # 更准确的检查：如果 stable_block_key 等于文本的 sha256 回退，就没有显式 id
                fallback_key = _compute_fallback_key(block.text)
                if block.stable_block_key == fallback_key:
                    has_explicit_id = False

                if not has_explicit_id:
                    msg = (
                        f"{rel_path}: block missing explicit id "
                        f"(text: {block.text[:50]!r}...)"
                    )
                    warnings.append(msg)

        passed = True
        if strict and warnings:
            passed = False

        return ValidateResult(passed=passed, warnings=warnings, errors=errors)
