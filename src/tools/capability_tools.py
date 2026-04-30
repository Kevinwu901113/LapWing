"""Read-only capability tools: list_capabilities, search_capability, view_capability.

Phase 2B: Expose capability library inspection without execution, mutation, or
automatic retrieval. All tools are feature-gated behind capabilities.enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

if TYPE_CHECKING:
    from src.capabilities import CapabilityIndex, CapabilityStore

logger = logging.getLogger("lapwing.tools.capability_tools")

ALLOWED_SCOPES = {"global", "user", "workspace", "session"}
ALLOWED_TYPES = {"skill", "workflow", "dynamic_agent", "memory_pattern", "tool_wrapper", "project_playbook"}
ALLOWED_MATURITIES = {"draft", "testing", "stable", "broken", "repairing"}
ALLOWED_STATUSES = {"active", "disabled", "archived", "quarantined"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high"}


def _validate_enum(value: str | None, allowed: set[str], field: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"Invalid {field} '{value}'. Allowed: {sorted(allowed)}")
    return value


# ── Schemas ──────────────────────────────────────────────────────────

LIST_CAPABILITIES_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Filter by scope",
        },
        "type": {
            "type": "string",
            "enum": ["skill", "workflow", "dynamic_agent", "memory_pattern", "tool_wrapper", "project_playbook"],
            "description": "Filter by capability type",
        },
        "maturity": {
            "type": "string",
            "enum": ["draft", "testing", "stable", "broken", "repairing"],
            "description": "Filter by maturity",
        },
        "status": {
            "type": "string",
            "enum": ["active", "disabled", "archived", "quarantined"],
            "description": "Filter by status",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Filter by risk level",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter by tags (any match)",
        },
        "include_disabled": {
            "type": "boolean",
            "description": "Include disabled capabilities (default false)",
        },
        "include_archived": {
            "type": "boolean",
            "description": "Include archived capabilities (default false)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Max results (default 20, max 100)",
        },
    },
}

SEARCH_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Keyword search across name, description, triggers, tags",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Filter by scope",
        },
        "type": {
            "type": "string",
            "enum": ["skill", "workflow", "dynamic_agent", "memory_pattern", "tool_wrapper", "project_playbook"],
            "description": "Filter by capability type",
        },
        "maturity": {
            "type": "string",
            "enum": ["draft", "testing", "stable", "broken", "repairing"],
            "description": "Filter by maturity",
        },
        "status": {
            "type": "string",
            "enum": ["active", "disabled", "archived", "quarantined"],
            "description": "Filter by status",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Filter by risk level",
        },
        "required_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter by required tools (any match)",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter by tags (any match)",
        },
        "include_all_scopes": {
            "type": "boolean",
            "description": "Return duplicates across scopes (default false = deduplicate by precedence)",
        },
        "include_disabled": {
            "type": "boolean",
            "description": "Include disabled capabilities (default false)",
        },
        "include_archived": {
            "type": "boolean",
            "description": "Include archived capabilities (default false)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "Max results (default 10, max 50)",
        },
    },
}

VIEW_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Capability ID to view",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "include_archived": {
            "type": "boolean",
            "description": "Include archived capability (default false)",
        },
        "include_body": {
            "type": "boolean",
            "description": "Include CAPABILITY.md body (default true)",
        },
        "include_files": {
            "type": "boolean",
            "description": "Include file listings (default true)",
        },
    },
    "required": ["id"],
}


# ── Helpers ──────────────────────────────────────────────────────────

def _compact_summary(doc) -> dict:
    m = doc.manifest
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "type": m.type.value,
        "scope": m.scope.value,
        "maturity": m.maturity.value,
        "status": m.status.value,
        "risk_level": m.risk_level.value,
        "tags": m.tags,
        "triggers": m.triggers,
        "updated_at": m.updated_at.isoformat() if m.updated_at else "",
    }


def _search_result(doc) -> dict:
    m = doc.manifest
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "type": m.type.value,
        "scope": m.scope.value,
        "maturity": m.maturity.value,
        "status": m.status.value,
        "risk_level": m.risk_level.value,
        "trust_required": m.trust_required,
        "triggers": m.triggers,
        "tags": m.tags,
        "required_tools": m.required_tools,
        "updated_at": m.updated_at.isoformat() if m.updated_at else "",
    }


def _list_files(cap_dir: Path) -> dict:
    result: dict[str, list[str]] = {}
    for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
        sub_path = cap_dir / sub
        if sub_path.is_dir():
            names = sorted(
                p.name for p in sub_path.iterdir()
                if p.name not in (".gitkeep",)
            )
            result[sub] = names
        else:
            result[sub] = []
    return result


def _scope_for_store(scope_str: str | None):
    if scope_str is None:
        return None
    from src.capabilities.schema import CapabilityScope
    return CapabilityScope(scope_str)


# ── Executors ────────────────────────────────────────────────────────

def _make_list_capabilities_executor(store: "CapabilityStore", index: "CapabilityIndex | None"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            type_str = _validate_enum(args.get("type"), ALLOWED_TYPES, "type")
            maturity_str = _validate_enum(args.get("maturity"), ALLOWED_MATURITIES, "maturity")
            status_str = _validate_enum(args.get("status"), ALLOWED_STATUSES, "status")
            risk_str = _validate_enum(args.get("risk_level"), ALLOWED_RISK_LEVELS, "risk_level")

            docs = store.list(
                scope=_scope_for_store(scope_str),
                type=type_str,
                maturity=maturity_str,
                status=status_str,
                risk_level=risk_str,
                tags=args.get("tags"),
                include_disabled=bool(args.get("include_disabled", False)),
                include_archived=bool(args.get("include_archived", False)),
                limit=min(int(args.get("limit", 20)), 100),
            )
            return ToolExecutionResult(
                success=True,
                payload={"capabilities": [_compact_summary(d) for d in docs], "count": len(docs)},
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("list_capabilities failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_store_unavailable", "detail": str(e)},
                reason=f"list_capabilities failed: {e}",
            )

    return executor


def _make_search_capability_executor(store: "CapabilityStore", index: "CapabilityIndex | None"):
    _SCOPE_PRECEDENCE = ["session", "workspace", "user", "global"]

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            type_str = _validate_enum(args.get("type"), ALLOWED_TYPES, "type")
            maturity_str = _validate_enum(args.get("maturity"), ALLOWED_MATURITIES, "maturity")
            status_str = _validate_enum(args.get("status"), ALLOWED_STATUSES, "status")
            risk_str = _validate_enum(args.get("risk_level"), ALLOWED_RISK_LEVELS, "risk_level")

            include_all = bool(args.get("include_all_scopes", False))
            include_disabled = bool(args.get("include_disabled", False))
            include_archived = bool(args.get("include_archived", False))

            filters: dict = {}
            if scope_str:
                filters["scope"] = scope_str
            if type_str:
                filters["type"] = type_str
            if maturity_str:
                filters["maturity"] = maturity_str
            if risk_str:
                filters["risk_level"] = risk_str
            if args.get("tags"):
                filters["tags"] = args["tags"]
            if args.get("required_tools"):
                filters["required_tools"] = args["required_tools"]

            if not include_disabled and not include_archived and status_str is None:
                filters["status"] = "active"
            elif status_str:
                filters["status"] = status_str

            limit = min(int(args.get("limit", 10)), 50)

            if index is not None:
                rows = index.search(
                    query=args.get("query"),
                    filters=filters if filters else None,
                    limit=limit * 5 if not include_all else limit,
                )
                docs = []
                for row in rows:
                    row_path = Path(row["path"])
                    if row_path.is_dir() and (row_path / "CAPABILITY.md").exists():
                        from src.capabilities.document import CapabilityParser
                        try:
                            docs.append(CapabilityParser().parse(row_path))
                        except Exception:
                            continue
            else:
                docs = store.search(
                    query=args.get("query"),
                    filters=filters if filters else None,
                    limit=500,
                )

            # Handle archived: index search defaults to active, but store.list includes
            # archived only when requested. For index-based search, we need to check.
            if not include_archived and index is not None:
                docs = [d for d in docs if d.manifest.status.value != "archived"]
            if not include_disabled and index is not None:
                docs = [d for d in docs if d.manifest.status.value != "disabled"]

            if not include_all:
                seen: set[str] = set()
                deduped: list = []
                docs_by_id: dict[str, list] = {}
                for d in docs:
                    docs_by_id.setdefault(d.manifest.id, []).append(d)
                for cap_id in docs_by_id:
                    entries = docs_by_id[cap_id]
                    best = min(entries, key=lambda d: _SCOPE_PRECEDENCE.index(d.manifest.scope.value)
                               if d.manifest.scope.value in _SCOPE_PRECEDENCE else 99)
                    deduped.append(best)
                docs = deduped

            docs = docs[:limit]
            return ToolExecutionResult(
                success=True,
                payload={"results": [_search_result(d) for d in docs], "count": len(docs)},
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("search_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_store_unavailable", "detail": str(e)},
                reason=f"search_capability failed: {e}",
            )

    return executor


def _make_view_capability_executor(store: "CapabilityStore", index: "CapabilityIndex | None"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            cap_id = str(args.get("id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "id is required"},
                    reason="view_capability requires an id",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            include_archived = bool(args.get("include_archived", False))
            include_body = bool(args.get("include_body", True))
            include_files = bool(args.get("include_files", True))

            from src.capabilities.errors import CapabilityError
            from src.capabilities.schema import CapabilityScope

            scope = CapabilityScope(scope_str) if scope_str else None

            from src.capabilities.document import CapabilityParser

            doc = None
            archived_but_not_included = False
            try:
                doc = store.get(cap_id, scope)
            except CapabilityError:
                pass

            # If not found in active dirs, check archived/ for better error message
            if doc is None:
                parser = CapabilityParser()
                scopes_to_check = [scope] if scope else [
                    CapabilityScope.SESSION,
                    CapabilityScope.WORKSPACE,
                    CapabilityScope.USER,
                    CapabilityScope.GLOBAL,
                ]
                for s in scopes_to_check:
                    parent = store.data_dir / "archived" / s.value
                    if not parent.is_dir():
                        continue
                    # Exact match first
                    exact = parent / cap_id
                    if exact.is_dir() and (exact / "CAPABILITY.md").exists():
                        if include_archived:
                            doc = parser.parse(exact)
                        else:
                            archived_but_not_included = True
                        break
                    # Timestamped collision dirs
                    for entry in sorted(parent.iterdir(), reverse=True):
                        if entry.is_dir() and entry.name.startswith(f"{cap_id}_") \
                                and (entry / "CAPABILITY.md").exists():
                            if include_archived:
                                doc = parser.parse(entry)
                            else:
                                archived_but_not_included = True
                            break
                    if doc is not None or archived_but_not_included:
                        break

            if doc is None:
                payload = {"error": "not_found", "id": cap_id}
                if archived_but_not_included:
                    payload["detail"] = "Capability is archived. Use include_archived=true to view."
                return ToolExecutionResult(
                    success=False,
                    payload=payload,
                    reason=f"Capability '{cap_id}' not found",
                )

            if doc.manifest.status.value == "archived" and not include_archived:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "id": cap_id,
                             "detail": "Capability is archived. Use include_archived=true to view."},
                    reason=f"Capability '{cap_id}' is archived",
                )

            m = doc.manifest
            result: dict = {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "type": m.type.value,
                "scope": m.scope.value,
                "version": m.version,
                "maturity": m.maturity.value,
                "status": m.status.value,
                "risk_level": m.risk_level.value,
                "trust_required": m.trust_required,
                "required_tools": m.required_tools,
                "required_permissions": m.required_permissions,
                "triggers": m.triggers,
                "tags": m.tags,
                "created_at": m.created_at.isoformat() if m.created_at else "",
                "updated_at": m.updated_at.isoformat() if m.updated_at else "",
                "content_hash": doc.content_hash,
            }

            if include_body:
                result["body"] = doc.body

            if include_files:
                result["files"] = _list_files(doc.directory)

            return ToolExecutionResult(success=True, payload=result)
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("view_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_store_unavailable", "detail": str(e)},
                reason=f"view_capability failed: {e}",
            )

    return executor


# ── Registration ─────────────────────────────────────────────────────

def register_capability_tools(
    tool_registry,
    store: "CapabilityStore",
    index: "CapabilityIndex | None" = None,
) -> None:
    """Register the 3 read-only capability tools.

    Only 3 tools: list_capabilities, search_capability, view_capability.
    No create, disable, archive, promote, or execution tools.
    """
    if store is None:
        logger.warning("register_capability_tools called with store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="list_capabilities",
        description=(
            "列出能力库中的能力。返回紧凑摘要列表，"
            "可按 scope/type/maturity/status/risk_level/tags 过滤。"
            "默认只返回 active 且未归档的能力。"
        ),
        json_schema=LIST_CAPABILITIES_SCHEMA,
        executor=_make_list_capabilities_executor(store, index),
        capability="capability_read",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="search_capability",
        description=(
            "搜索能力库。支持按关键词（名称、描述、触发器、标签）和过滤器"
            "（scope/type/maturity/status/risk_level/required_tools/tags）搜索。"
            "默认按 scope 优先级去重（session > workspace > user > global）。"
            "默认不返回 disabled/archived/quarantined 的能力。"
        ),
        json_schema=SEARCH_CAPABILITY_SCHEMA,
        executor=_make_search_capability_executor(store, index),
        capability="capability_read",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_capability",
        description=(
            "查看单个能力的完整文档。返回 manifest 元数据、CAPABILITY.md 正文、"
            "以及标准目录的文件列表（不包含脚本/测试/示例/评估/跟踪/版本的文件内容）。"
            "不执行任何脚本。scope 省略时按优先级解析：session > workspace > user > global。"
        ),
        json_schema=VIEW_CAPABILITY_SCHEMA,
        executor=_make_view_capability_executor(store, index),
        capability="capability_read",
        risk_level="low",
    ))

    logger.info("Phase 2B capability read tools registered (list/search/view)")
