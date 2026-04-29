"""WikiCompiler — turn pending candidates into wiki patches.

Phase 2 §2.3. Two-stage pipeline:

    pending record (gate-only fields)
        ↓ extract_candidate(record)  — LLM, structured extraction
    MemoryCandidate (14 fields)
        ↓ compile([candidate, ...])  — LLM per subject entity
    CompiledMemoryPatch[]

The compiler reads ``compiler_policy.md`` as guidance for the LLM and
limits the MVP write surface to four page types per the Phase 2 brief:

    - entity.kevin
    - entity.lapwing
    - knowledge.decision-*
    - knowledge.open-question-*

Other candidate types (chitchat, transient task, emotion) accumulate in
``CandidateStore`` with status ``compiled`` and ``last_error="skipped:
out_of_mvp_scope"`` rather than churning the wiki.

LLM channel: we use the LLM router/client directly (no Researcher
Agent), per the Phase 2 brief's "background/memory_compile slot".
Tests stub the LLM with a callable that returns canned JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Protocol

from src.memory.candidate import (
    VALID_RELATION_TYPES,
    CompiledMemoryPatch,
    MemoryCandidate,
    Relation,
)
from src.memory.candidate_store import PendingRecord
from src.memory.memory_schema import MemorySchema
from src.memory.wiki_store import WikiPage, WikiStore

logger = logging.getLogger("lapwing.memory.wiki_compiler")


# ── MVP scope ───────────────────────────────────────────────────────

MVP_PAGE_PREFIXES = (
    "entity.kevin",
    "entity.lapwing",
    "knowledge.decision-",
    "knowledge.open-question-",
)


def _is_in_mvp_scope(page_id: str) -> bool:
    return any(page_id.startswith(p) for p in MVP_PAGE_PREFIXES)


# ── LLM protocol ────────────────────────────────────────────────────


class _LLMLike(Protocol):
    async def query_lightweight(
        self, system: str, user: str, *, slot: str | None = None
    ) -> str: ...


# ── Prompts ─────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """你是 Lapwing 的记忆结构化提取器。
从一段已通过快速门控的对话候选中提取出一个完整的 MemoryCandidate。

输出严格 JSON：
{
  "subject": str,        // canonical entity id, e.g. "entity.kevin"
  "predicate": str,
  "object": str,
  "type": "preference"|"identity"|"project_fact"|"decision"|"relationship"|"commitment"|"skill"|"open_question",
  "salience": float 0-1,
  "confidence": float 0-1,
  "stability": "transient"|"session"|"long_lived"|"permanent",
  "privacy_level": "public"|"personal"|"sensitive"|"secret",
  "contradiction_risk": float 0-1,
  "evidence_quote": str,
  "expires_at": null,
  "relations": [{"type": str, "target": str}, ...]
}

relation type 必须在：owned_by, created_by, creator_of, part_of, depends_on, related_to, supersedes, contradicts。
target 必须是 canonical entity id。
不输出多余文字，只输出 JSON。"""


_COMPILE_SYSTEM = """你是 Lapwing 的 wiki 编译器。
输入：一个 wiki 页面的当前 frontmatter + sections，加一组针对该 subject 的新候选事实。
任务：决定如何更新页面。允许的操作：

- "create"          — 新建页面（只在该 subject 还没有页面时）
- "update_section"  — 重写整个 section（content 是 section 全文）
- "add_fact"        — 在 Stable facts 里增加一行（content 是单行事实）
- "supersede_fact"  — 把旧 fact 标记为 superseded（不删除）
- "add_relation"    — 新增 frontmatter relation（content 是 JSON）

冲突处理：
- 如果新候选与已有 stable fact 直接矛盾 → 必须用 supersede_fact 而不是覆盖
- 如果不确定，给 risk="medium" 或 "high"（high 不会自动应用）

输出严格 JSON：
{
  "patches": [
    {
      "operation": "...",
      "section": "...",     // optional
      "content": "...",
      "reason": "...",
      "risk": "low"|"medium"|"high"
    }, ...
  ]
}

不要输出多余文字。"""


# ── Compiler ────────────────────────────────────────────────────────


class WikiCompiler:
    """Compile pending candidates into wiki patches."""

    def __init__(
        self,
        llm: _LLMLike,
        wiki_store: WikiStore,
        *,
        policy_path: str | Path | None = None,
        slot: str = "lightweight_judgment",
        compiler_version: str = "wiki-compiler-v1",
    ) -> None:
        self._llm = llm
        self._wiki_store = wiki_store
        self._slot = slot
        self._schema = MemorySchema()
        self._compiler_version = compiler_version
        self._policy_text = _load_policy(policy_path)

    # ── Public API ──────────────────────────────────────────────────

    async def extract_candidate(self, record: PendingRecord) -> MemoryCandidate:
        """Turn a fast-gate pending record into a structured candidate."""
        user_prompt = (
            f"候选 id: {record.id}\n"
            f"gate score: {record.gate_score}\n"
            f"rough category: {record.rough_category}\n"
            f"source ids: {record.source_ids}\n"
        )
        raw = await self._llm.query_lightweight(
            _EXTRACT_SYSTEM, user_prompt, slot=self._slot,
        )
        parsed = _parse_json_loose(raw)
        if parsed is None:
            raise ValueError(
                f"extract_candidate: unparsable LLM response for {record.id}"
            )

        relations_raw = parsed.get("relations") or []
        relations: list[Relation] = []
        for r in relations_raw:
            if not isinstance(r, dict):
                continue
            rtype = r.get("type")
            target = r.get("target")
            if not isinstance(rtype, str) or not isinstance(target, str):
                continue
            if rtype not in VALID_RELATION_TYPES:
                logger.warning(
                    "[wiki_compiler] dropped unknown relation type %r", rtype,
                )
                continue
            relations.append(Relation(type=rtype, target=target))

        return MemoryCandidate(
            id=record.id,
            source_ids=record.source_ids,
            subject=str(parsed.get("subject", "entity.unknown")),
            predicate=str(parsed.get("predicate", "")),
            object=str(parsed.get("object", "")),
            type=parsed.get("type", "preference"),
            salience=_coerce_float(parsed.get("salience"), 0.5),
            confidence=_coerce_float(parsed.get("confidence"), 0.5),
            stability=parsed.get("stability", "session"),
            privacy_level=parsed.get("privacy_level", "personal"),
            contradiction_risk=_coerce_float(parsed.get("contradiction_risk"), 0.0),
            evidence_quote=str(parsed.get("evidence_quote", "")),
            expires_at=parsed.get("expires_at"),
            relations=relations,
        )

    async def compile(
        self, candidates: list[MemoryCandidate],
    ) -> list[CompiledMemoryPatch]:
        """Group candidates by subject, ask LLM for patches per group."""
        out: list[CompiledMemoryPatch] = []
        if not candidates:
            return out

        groups: dict[str, list[MemoryCandidate]] = {}
        for c in candidates:
            page_id = self._page_id_for(c)
            if page_id is None:
                logger.info(
                    "[wiki_compiler] skipping out-of-MVP candidate %s (subject=%s, type=%s)",
                    c.id, c.subject, c.type,
                )
                continue
            groups.setdefault(page_id, []).append(c)

        for page_id, group in groups.items():
            patches = await self._compile_group(page_id, group)
            out.extend(patches)
        return out

    # ── Internals ───────────────────────────────────────────────────

    def _page_id_for(self, candidate: MemoryCandidate) -> str | None:
        """Resolve which wiki page id this candidate should target.

        MVP scope: entity candidates target their subject; decisions and
        open_questions get a deterministic slug from predicate+object.
        Anything else returns None so ``compile`` skips it.
        """
        if candidate.subject in ("entity.kevin", "entity.lapwing"):
            return candidate.subject
        if candidate.type == "decision":
            slug = _slug(candidate.predicate or candidate.object or candidate.id)
            return f"knowledge.decision-{slug}"
        if candidate.type == "open_question":
            slug = _slug(candidate.predicate or candidate.object or candidate.id)
            return f"knowledge.open-question-{slug}"
        return None

    async def _compile_group(
        self, page_id: str, group: list[MemoryCandidate],
    ) -> list[CompiledMemoryPatch]:
        existing = await self._wiki_store.get_page(page_id)
        before_hash = _hash_page(existing) if existing else None

        if existing is None:
            decided_create = await self._should_create_new_page(page_id, group)
            if not decided_create:
                logger.info(
                    "[wiki_compiler] not enough signal to create %s yet "
                    "(need 3+ candidates of long_lived+ stability)", page_id,
                )
                return []

        target_path = _wiki_target_path(self._wiki_store, page_id)
        if existing is None:
            return [self._patch_create(page_id, target_path, group)]

        # Existing page: ask LLM how to update
        contradicts = self._detect_contradictions(group, existing)
        user_prompt = self._build_compile_prompt(page_id, existing, group, contradicts)

        try:
            raw = await self._llm.query_lightweight(
                _COMPILE_SYSTEM, user_prompt, slot=self._slot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wiki_compiler] LLM call failed: %s", exc)
            return []

        parsed = _parse_json_loose(raw)
        if not parsed or not isinstance(parsed.get("patches"), list):
            logger.warning(
                "[wiki_compiler] unparsable patches for %s: %r",
                page_id, raw[:200],
            )
            return []

        out: list[CompiledMemoryPatch] = []
        primary_candidate_id = group[0].id
        all_source_ids = sorted({s for c in group for s in c.source_ids})
        for raw_patch in parsed["patches"]:
            if not isinstance(raw_patch, dict):
                continue
            try:
                op = raw_patch.get("operation")
                if op not in (
                    "create", "update_section", "add_fact",
                    "supersede_fact", "add_relation",
                ):
                    continue
                out.append(CompiledMemoryPatch(
                    target_page_id=page_id,
                    target_path=str(target_path),
                    operation=op,
                    section=raw_patch.get("section"),
                    content=str(raw_patch.get("content", "")),
                    reason=str(raw_patch.get("reason", "")),
                    source_ids=all_source_ids,
                    before_hash=before_hash,
                    risk=raw_patch.get("risk") or "low",
                    candidate_id=primary_candidate_id,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[wiki_compiler] dropped malformed patch: %s", exc)
        return out

    async def _should_create_new_page(
        self, page_id: str, group: list[MemoryCandidate],
    ) -> bool:
        """compiler_policy.md rule: a brand-new page needs 3+ supporting
        candidates of at least long_lived stability."""
        if not _is_in_mvp_scope(page_id):
            return False
        long_lived = [
            c for c in group if c.stability in ("long_lived", "permanent")
        ]
        return len(long_lived) >= 3

    def _detect_contradictions(
        self, group: list[MemoryCandidate], existing: WikiPage,
    ) -> list[str]:
        """Cheap heuristic contradiction signal for the LLM prompt."""
        stable_text = existing.sections.get("Stable facts", "").lower()
        out: list[str] = []
        for c in group:
            if c.contradiction_risk >= 0.6:
                out.append(c.predicate or c.object)
                continue
            if c.predicate and c.predicate.lower() in stable_text:
                out.append(c.predicate)
        return out

    # ── Patch builders ──────────────────────────────────────────────

    def _patch_create(
        self,
        page_id: str,
        target_path: Path,
        group: list[MemoryCandidate],
    ) -> CompiledMemoryPatch:
        primary = group[0]
        title = page_id.split(".", 1)[-1].replace("-", " ").title()
        page_type = "entity" if page_id.startswith("entity.") else _knowledge_type(page_id)
        source_ids = sorted({s for c in group for s in c.source_ids})

        stable_facts = "\n".join(
            f"- {c.predicate} {c.object}".strip() for c in group if (c.predicate or c.object)
        )
        if not stable_facts:
            stable_facts = "- (待补充)"

        evidence = "\n".join(
            f"- {c.evidence_quote}" for c in group if c.evidence_quote
        ) or "（来自候选编译）"

        relations_kw: dict[str, Any] = {}
        merged_relations = _merge_relations(group)
        if merged_relations:
            relations_kw["relations"] = [r.model_dump() for r in merged_relations]

        body = self._schema.render_page(
            page_id,
            page_type,
            title,
            summary=primary.evidence_quote or f"自动编译于 {primary.id}",
            stable_facts=stable_facts,
            evidence=evidence,
            confidence=max(c.confidence for c in group),
            stability=_dominant_stability(group),
            privacy_level=primary.privacy_level,
            status="active",
            source_ids=source_ids,
            **relations_kw,
        )
        return CompiledMemoryPatch(
            target_page_id=page_id,
            target_path=str(target_path),
            operation="create",
            content=body,
            reason=f"create page from {len(group)} candidates",
            source_ids=source_ids,
            before_hash=None,
            risk="low",
            candidate_id=primary.id,
        )

    def _build_compile_prompt(
        self,
        page_id: str,
        existing: WikiPage,
        group: list[MemoryCandidate],
        contradictions: list[str],
    ) -> str:
        candidate_blob = json.dumps(
            [c.model_dump() for c in group], ensure_ascii=False, indent=2,
        )
        existing_blob = json.dumps({
            "id": existing.id,
            "frontmatter": existing.frontmatter,
            "sections": existing.sections,
        }, ensure_ascii=False, indent=2)
        contradiction_note = (
            f"\n注意：以下事实可能与已有页面矛盾，必须用 supersede_fact "
            f"而不是覆盖：{contradictions}\n"
            if contradictions else ""
        )
        return (
            f"=== 编译策略（参考） ===\n{self._policy_text}\n\n"
            f"=== 已有页面 ({page_id}) ===\n{existing_blob}\n\n"
            f"=== 新候选 ===\n{candidate_blob}\n"
            f"{contradiction_note}"
        )


# ── Free helpers ────────────────────────────────────────────────────


def _load_policy(path: str | Path | None) -> str:
    if path is None:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _hash_page(page: WikiPage) -> str:
    """Recompute hash from on-disk text — cheaper than re-rendering."""
    p = Path(page.path)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _wiki_target_path(wiki_store: WikiStore, page_id: str) -> Path:
    base = wiki_store._wiki_dir  # noqa: SLF001 — internal coupling is fine here
    if "." not in page_id:
        return base / f"{page_id}.md"
    ns, slug = page_id.split(".", 1)
    slug = slug.replace("/", "-")
    if ns == "entity":
        return base / "entities" / f"{slug}.md"
    if ns == "knowledge":
        return base / "knowledge" / f"{slug}.md"
    if ns == "meta":
        return base / "_meta" / f"{slug}.md"
    return base / f"{slug}.md"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower()).strip("-")
    return s or "untitled"


def _knowledge_type(page_id: str) -> str:
    if page_id.startswith("knowledge.decision-"):
        return "decision"
    if page_id.startswith("knowledge.open-question-"):
        return "open_question"
    return "concept"


def _merge_relations(candidates: Iterable[MemoryCandidate]) -> list[Relation]:
    seen: set[tuple[str, str]] = set()
    out: list[Relation] = []
    for c in candidates:
        for r in c.relations:
            key = (r.type, r.target)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def _dominant_stability(candidates: list[MemoryCandidate]) -> str:
    order = {"transient": 0, "session": 1, "long_lived": 2, "permanent": 3}
    return max(candidates, key=lambda c: order.get(c.stability, 0)).stability


def _coerce_float(v: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _parse_json_loose(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```\s*$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
