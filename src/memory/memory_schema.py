"""MemorySchema — frontmatter contract + page template for wiki pages.

Phase 1 §1.3 of the wiki blueprint. Every wiki page has YAML frontmatter
followed by a structured markdown body. This module is the single source
of truth for what fields are required, what their types are, and how a
fresh page is rendered.

We do not pull in a heavy schema lib — pages are tiny and we want the
checks to be cheap enough to run during ``deterministic`` lint passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

# ── Schema definition ───────────────────────────────────────────────

REQUIRED_FIELDS = (
    "id",
    "type",
    "title",
    "created_at",
    "updated_at",
    "compiler_version",
)

OPTIONAL_FIELDS = (
    "aliases",
    "status",
    "confidence",
    "review_after",
    "source_ids",
    "relations",
    "tags",
    "stability",
    "privacy_level",
    "supersedes",
    "superseded_by",
    "expires_at",
)

VALID_TYPES = (
    "entity",
    "preference",
    "project",
    "decision",
    "concept",
    "commitment",
    "open_question",
    "meta",
)

VALID_STATUSES = (
    "active",
    "superseded",
    "contested",
    "uncertain",
    "expired",
)

VALID_STABILITY = (
    "permanent",
    "long_lived",
    "session",
    "transient",
)

VALID_PRIVACY = (
    "public",
    "personal",
    "sensitive",
    "secret",
)

VALID_RELATION_TYPES = (
    "owned_by",
    "created_by",
    "creator_of",
    "part_of",
    "depends_on",
    "related_to",
    "supersedes",
    "contradicts",
)

# Standard body sections, in order.
PAGE_SECTIONS = (
    "Current summary",
    "Stable facts",
    "Active decisions",
    "Open questions",
    "Recent changes",
    "Evidence",
    "Superseded notes",
)

WIKI_PAGE_TEMPLATE = """---
{frontmatter_yaml}---

# {title}

## Current summary

{summary}

## Stable facts

{stable_facts}

## Active decisions

{active_decisions}

## Open questions

{open_questions}

## Recent changes

{recent_changes}

## Evidence

{evidence}

## Superseded notes

{superseded_notes}
"""


@dataclass(frozen=True)
class SchemaViolation:
    rule: str
    field: str | None
    message: str
    severity: Literal["error", "warning"] = "error"


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z0-9._-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_type(value: Any, expected: type, field: str) -> SchemaViolation | None:
    if not isinstance(value, expected):
        return SchemaViolation(
            rule="type",
            field=field,
            message=f"{field} must be {expected.__name__}, got {type(value).__name__}",
        )
    return None


class MemorySchema:
    """Validate and generate wiki page frontmatter + body structure."""

    # ── Validation ───────────────────────────────────────────────────

    def validate_frontmatter(self, frontmatter: dict[str, Any]) -> list[SchemaViolation]:
        errors: list[SchemaViolation] = []

        for field in REQUIRED_FIELDS:
            if field not in frontmatter or frontmatter[field] in (None, ""):
                errors.append(
                    SchemaViolation(
                        rule="required",
                        field=field,
                        message=f"missing required field: {field}",
                    )
                )

        # type checks
        page_id = frontmatter.get("id")
        if isinstance(page_id, str) and not _ID_RE.match(page_id):
            errors.append(
                SchemaViolation(
                    rule="format",
                    field="id",
                    message=f"id must match '<namespace>.<slug>' (got {page_id!r})",
                )
            )

        page_type = frontmatter.get("type")
        if isinstance(page_type, str) and page_type not in VALID_TYPES:
            errors.append(
                SchemaViolation(
                    rule="enum",
                    field="type",
                    message=f"type must be one of {VALID_TYPES}, got {page_type!r}",
                )
            )

        status = frontmatter.get("status")
        if status is not None and status not in VALID_STATUSES:
            errors.append(
                SchemaViolation(
                    rule="enum",
                    field="status",
                    message=f"status must be one of {VALID_STATUSES}, got {status!r}",
                )
            )

        stability = frontmatter.get("stability")
        if stability is not None and stability not in VALID_STABILITY:
            errors.append(
                SchemaViolation(
                    rule="enum",
                    field="stability",
                    message=f"stability must be one of {VALID_STABILITY}, got {stability!r}",
                )
            )

        privacy = frontmatter.get("privacy_level")
        if privacy is not None and privacy not in VALID_PRIVACY:
            errors.append(
                SchemaViolation(
                    rule="enum",
                    field="privacy_level",
                    message=f"privacy_level must be one of {VALID_PRIVACY}, got {privacy!r}",
                )
            )

        confidence = frontmatter.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
                errors.append(
                    SchemaViolation(
                        rule="range",
                        field="confidence",
                        message="confidence must be float in [0.0, 1.0]",
                    )
                )

        relations = frontmatter.get("relations")
        if relations is not None:
            if not isinstance(relations, list):
                errors.append(
                    SchemaViolation(
                        rule="type",
                        field="relations",
                        message="relations must be a list",
                    )
                )
            else:
                for i, rel in enumerate(relations):
                    if not isinstance(rel, dict) or "type" not in rel or "target" not in rel:
                        errors.append(
                            SchemaViolation(
                                rule="format",
                                field=f"relations[{i}]",
                                message="each relation must have 'type' and 'target'",
                            )
                        )
                        continue
                    if rel["type"] not in VALID_RELATION_TYPES:
                        errors.append(
                            SchemaViolation(
                                rule="enum",
                                field=f"relations[{i}].type",
                                message=f"relation type must be one of {VALID_RELATION_TYPES}, got {rel['type']!r}",
                                severity="warning",
                            )
                        )

        for list_field in ("aliases", "source_ids", "tags"):
            v = frontmatter.get(list_field)
            if v is not None and not isinstance(v, list):
                errors.append(
                    SchemaViolation(
                        rule="type",
                        field=list_field,
                        message=f"{list_field} must be a list",
                    )
                )

        return errors

    def validate_page(self, page_path: str | Path) -> list[SchemaViolation]:
        path = Path(page_path)
        if not path.exists():
            return [
                SchemaViolation(
                    rule="exists",
                    field=None,
                    message=f"page file not found: {path}",
                )
            ]
        text = path.read_text(encoding="utf-8")
        return self.validate_text(text)

    def validate_text(self, text: str) -> list[SchemaViolation]:
        match = _FRONTMATTER_RE.match(text)
        if not match:
            return [
                SchemaViolation(
                    rule="format",
                    field=None,
                    message="page must start with --- YAML frontmatter ---",
                )
            ]
        try:
            data = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            return [
                SchemaViolation(
                    rule="yaml",
                    field=None,
                    message=f"invalid YAML in frontmatter: {exc}",
                )
            ]
        if not isinstance(data, dict):
            return [
                SchemaViolation(
                    rule="format",
                    field=None,
                    message="frontmatter must be a YAML mapping",
                )
            ]
        return self.validate_frontmatter(data)

    # ── Parsing ──────────────────────────────────────────────────────

    def parse(self, text: str) -> tuple[dict[str, Any], str]:
        """Split a page into (frontmatter dict, body string).

        Caller should run ``validate_text`` first if it wants to fail
        loudly on malformed input — this method returns ``({}, text)``
        when no frontmatter is present so callers can handle gracefully.
        """
        match = _FRONTMATTER_RE.match(text)
        if not match:
            return {}, text
        try:
            data = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}, text
        if not isinstance(data, dict):
            return {}, text
        body = text[match.end():]
        return data, body

    def extract_sections(self, body: str) -> dict[str, str]:
        """Map H2 section name → trimmed content. Sections beyond the
        canonical list are still returned (forwards compatibility).
        """
        sections: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []
        for line in body.splitlines():
            if line.startswith("## "):
                if current_name is not None:
                    sections[current_name] = "\n".join(current_lines).strip()
                current_name = line[3:].strip()
                current_lines = []
            else:
                if current_name is not None:
                    current_lines.append(line)
        if current_name is not None:
            sections[current_name] = "\n".join(current_lines).strip()
        return sections

    # ── Generation ───────────────────────────────────────────────────

    def generate_frontmatter(
        self,
        page_id: str,
        page_type: str,
        title: str,
        **kwargs: Any,
    ) -> str:
        """Render the YAML frontmatter block (without enclosing ``---``)."""
        now = _utc_now()
        data: dict[str, Any] = {
            "id": page_id,
            "type": page_type,
            "title": title,
            "created_at": kwargs.pop("created_at", now),
            "updated_at": kwargs.pop("updated_at", now),
            "compiler_version": kwargs.pop("compiler_version", "wiki-compiler-v1"),
        }
        for key in OPTIONAL_FIELDS:
            if key in kwargs:
                data[key] = kwargs.pop(key)
        # leftover keys are accepted (forward-compat) and tail-emitted
        for key, value in kwargs.items():
            data[key] = value
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def render_page(
        self,
        page_id: str,
        page_type: str,
        title: str,
        *,
        summary: str = "",
        stable_facts: str = "",
        active_decisions: str = "",
        open_questions: str = "",
        recent_changes: str = "",
        evidence: str = "",
        superseded_notes: str = "",
        **frontmatter_kwargs: Any,
    ) -> str:
        fm = self.generate_frontmatter(page_id, page_type, title, **frontmatter_kwargs)
        return WIKI_PAGE_TEMPLATE.format(
            frontmatter_yaml=fm,
            title=title,
            summary=summary or "（暂无）",
            stable_facts=stable_facts or "（暂无）",
            active_decisions=active_decisions or "（暂无）",
            open_questions=open_questions or "（暂无）",
            recent_changes=recent_changes or "（暂无）",
            evidence=evidence or "（暂无）",
            superseded_notes=superseded_notes or "（暂无）",
        )
