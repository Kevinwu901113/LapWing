"""Read-only capability tools: list/search/view/load capability.
Phase 3C: lifecycle management tools: evaluate_capability, plan_capability_transition,
transition_capability (feature-gated behind capabilities.lifecycle_tools_enabled).

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
    from src.capabilities.lifecycle import CapabilityLifecycleManager

logger = logging.getLogger("lapwing.tools.capability_tools")

ALLOWED_SCOPES = {"global", "user", "workspace", "session"}
ALLOWED_TYPES = {"skill", "workflow", "dynamic_agent", "memory_pattern", "tool_wrapper", "project_playbook"}
ALLOWED_MATURITIES = {"draft", "testing", "stable", "broken", "repairing"}
ALLOWED_STATUSES = {
    "active",
    "broken",
    "repairing",
    "disabled",
    "archived",
    "quarantined",
    "needs_permission",
    "environment_mismatch",
}
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
            "enum": sorted(ALLOWED_STATUSES),
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
            "enum": sorted(ALLOWED_STATUSES),
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

LOAD_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Capability ID to load read-only",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "sections": {
            "type": "array",
            "items": {"type": "string", "enum": ["manifest", "body", "files"]},
            "description": "Sections to include. Default: manifest and body.",
        },
        "include_archived": {
            "type": "boolean",
            "description": "Include archived capability (default false)",
        },
    },
    "required": ["id"],
}

RUN_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Capability ID to run"},
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "arguments": {
            "type": "object",
            "description": "Arguments passed to the capability entrypoint",
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": 300,
            "description": "Execution timeout in seconds",
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
        "do_not_apply_when": m.do_not_apply_when,
        "sensitive_contexts": [v.value if hasattr(v, "value") else str(v) for v in m.sensitive_contexts],
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
        "do_not_apply_when": m.do_not_apply_when,
        "sensitive_contexts": [v.value if hasattr(v, "value") else str(v) for v in m.sensitive_contexts],
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


def _capability_execution_metadata(manifest) -> tuple[str, dict]:
    execution = manifest.extra.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    entry_type = (
        execution.get("entry_type")
        or manifest.extra.get("entry_type")
        or manifest.extra.get("entrypoint_type")
        or ""
    )
    if not entry_type and (execution.get("skill_id") or manifest.extra.get("skill_id")):
        entry_type = "skill_bridge"
    if not entry_type:
        entry_type = "procedural"
    return str(entry_type), execution


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


def _make_load_capability_executor(store: "CapabilityStore", index: "CapabilityIndex | None"):
    view_executor = _make_view_capability_executor(store, index)

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        args = dict(request.arguments)
        sections = args.pop("sections", None) or ["manifest", "body"]
        include_body = "body" in sections
        include_files = "files" in sections
        view_request = ToolExecutionRequest(
            name="view_capability",
            arguments={
                **args,
                "include_body": include_body,
                "include_files": include_files,
            },
        )
        result = await view_executor(view_request, context)
        if not result.success:
            return result
        payload = dict(result.payload)
        if "manifest" not in sections:
            for key in (
                "type", "scope", "version", "maturity", "status", "risk_level",
                "trust_required", "required_tools", "required_permissions",
                "triggers", "tags", "created_at", "updated_at", "content_hash",
            ):
                payload.pop(key, None)
        payload["loaded_sections"] = list(sections)
        payload["read_only"] = True
        return ToolExecutionResult(success=True, payload=payload)

    return executor


def _make_run_capability_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.eval_records import get_latest_valid_eval_record
            from src.capabilities.reuse_preflight import (
                CapabilityUseContext,
                ReusePreflightInput,
                run_reuse_preflight,
            )
            from src.core.runtime_profiles import get_runtime_profile
            from src.core.tool_dispatcher import ServiceContextView
            from src.eval.axes import AxisStatus, EvalAxis

            args = request.arguments
            cap_id = str(args.get("id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"executed": False, "reason": "id is required"},
                    reason="run_capability requires an id",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            doc = store.get(cap_id, _scope_for_store(scope_str))
            latest = get_latest_valid_eval_record(doc)
            if latest is None:
                return ToolExecutionResult(
                    success=False,
                    payload={
                        "executed": False,
                        "capability_id": doc.id,
                        "reason": "stale_evaluation",
                    },
                    reason="stale_evaluation",
                )

            for axis in (EvalAxis.FUNCTIONAL.value, EvalAxis.SAFETY.value):
                result = latest.axes.get(axis)
                status = getattr(result, "status", AxisStatus.UNKNOWN)
                status_value = status.value if hasattr(status, "value") else str(status)
                if status_value != AxisStatus.PASS.value:
                    return ToolExecutionResult(
                        success=False,
                        payload={
                            "executed": False,
                            "capability_id": doc.id,
                            "reason": "axis_failed",
                            "axis": axis,
                            "status": status_value,
                        },
                        reason="axis_failed",
                    )

            entry_type, execution = _capability_execution_metadata(doc.manifest)
            if entry_type == "procedural":
                return ToolExecutionResult(
                    success=False,
                    payload={"executed": False, "capability_id": doc.id, "reason": "procedural_not_executable"},
                    reason="procedural_not_executable",
                )
            if entry_type not in {"skill_bridge", "executable_script"}:
                return ToolExecutionResult(
                    success=False,
                    payload={"executed": False, "capability_id": doc.id, "reason": "unknown_entry_type"},
                    reason="unknown_entry_type",
                )

            svc = ServiceContextView(context.services or {})
            skill_executor = svc.skill_executor
            if skill_executor is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"executed": False, "capability_id": doc.id, "reason": "SkillExecutor 未挂载"},
                    reason=f"run_capability requires skill_executor for {entry_type}",
                )

            runtime_profile = get_runtime_profile(context.runtime_profile or "standard")
            tool_registry = svc.tool_registry
            available_tools = set()
            if tool_registry is not None:
                available_tools = {tool.name for tool in tool_registry.get_tools_for_profile(runtime_profile)}

            preflight = run_reuse_preflight(ReusePreflightInput(
                capability=doc,
                runtime_profile=runtime_profile,
                auth_level=context.auth_level,
                current_context=CapabilityUseContext(),
                requested_arguments=args.get("arguments") or {},
                execution_mode="run",
                latest_eval_record=latest,
                available_tools=available_tools,
                available_permissions=set(),
            ))
            if not preflight.allowed:
                return ToolExecutionResult(
                    success=False,
                    payload={
                        "executed": False,
                        "capability_id": doc.id,
                        "reason": preflight.reason,
                        "details": preflight.details,
                    },
                    reason=preflight.reason,
                )

            call_arguments = args.get("arguments") or {}
            timeout = max(1, min(int(args.get("timeout", 30) or 30), 300))

            if entry_type == "executable_script":
                from src.skills.skill_executor import CapabilityExecutionContext

                entry_script = str(
                    execution.get("entry_script") or doc.manifest.extra.get("entry_script") or "",
                ).strip()
                if not entry_script:
                    return ToolExecutionResult(
                        success=False,
                        payload={"executed": False, "capability_id": doc.id, "reason": "missing_entry_script"},
                        reason="missing_entry_script",
                    )
                dependencies = execution.get("dependencies") or doc.manifest.extra.get("dependencies") or []
                if not isinstance(dependencies, list):
                    dependencies = [str(dependencies)]
                result = await skill_executor.execute_directory(
                    directory=doc.directory,
                    entry_script=entry_script,
                    arguments=call_arguments,
                    timeout=timeout,
                    capability_context=CapabilityExecutionContext(
                        capability_id=doc.id,
                        capability_version=doc.manifest.version,
                        capability_content_hash=doc.content_hash,
                        maturity=doc.manifest.maturity.value,
                        dependencies=tuple(str(dep) for dep in dependencies),
                    ),
                )
                return ToolExecutionResult(
                    success=result.success,
                    payload={
                        "executed": True,
                        "capability_id": doc.id,
                        "capability_version": doc.manifest.version,
                        "capability_content_hash": doc.content_hash,
                        "entry_type": "executable_script",
                        "entry_script": entry_script,
                        "output": result.output,
                        "error": result.error,
                        "exit_code": result.exit_code,
                        "timed_out": result.timed_out,
                    },
                    reason=result.error if not result.success else "",
                )

            skill_id = str(execution.get("skill_id") or doc.manifest.extra.get("skill_id") or "").strip()
            if not skill_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"executed": False, "capability_id": doc.id, "reason": "skill_bridge_missing_skill_id"},
                    reason="skill_bridge_missing_skill_id",
                )

            result = await skill_executor.execute(skill_id, arguments=call_arguments, timeout=timeout)
            return ToolExecutionResult(
                success=result.success,
                payload={
                    "executed": True,
                    "capability_id": doc.id,
                    "capability_version": doc.manifest.version,
                    "capability_content_hash": doc.content_hash,
                    "entry_type": "skill_bridge",
                    "skill_id": skill_id,
                    "output": result.output,
                    "error": result.error,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                },
                reason=result.error if not result.success else "",
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"executed": False, "error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("run_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"executed": False, "error": "run_capability_failed", "detail": str(e)},
                reason=f"run_capability failed: {e}",
            )

    return executor


# ── Phase 3C: Lifecycle management tool schemas ─────────────────────

EVALUATE_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Capability ID to evaluate",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "write_record": {
            "type": "boolean",
            "description": "Persist the EvalRecord to evals/ (default true)",
        },
        "include_findings": {
            "type": "boolean",
            "description": "Include detailed findings in response (default true)",
        },
    },
    "required": ["id"],
}

PLAN_CAPABILITY_TRANSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Capability ID to plan a transition for",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "target": {
            "type": "string",
            "enum": ["testing", "stable", "broken", "repairing", "disabled", "archived"],
            "description": "Target maturity or status",
        },
        "approval": {
            "type": "object",
            "description": "Approval object for high-risk transitions",
            "properties": {
                "approved": {"type": "boolean"},
                "approved_by": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "failure_evidence": {
            "type": "object",
            "description": "Evidence of failure (required for stable→broken)",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for the transition",
        },
    },
    "required": ["id", "target"],
}

TRANSITION_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Capability ID to transition",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope to look in (omitted = resolve by precedence)",
        },
        "target": {
            "type": "string",
            "enum": ["testing", "stable", "broken", "repairing", "disabled", "archived"],
            "description": "Target maturity or status",
        },
        "approval": {
            "type": "object",
            "description": "Approval object for high-risk transitions",
            "properties": {
                "approved": {"type": "boolean"},
                "approved_by": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "failure_evidence": {
            "type": "object",
            "description": "Evidence of failure (required for stable→broken)",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for the transition",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, preview the transition without applying it (default false)",
        },
    },
    "required": ["id", "target"],
}


# ── Phase 3C: Lifecycle tool executors ──────────────────────────────

def _make_evaluate_capability_executor(lifecycle: "CapabilityLifecycleManager"):
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
                    reason="evaluate_capability requires an id",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            write_record = bool(args.get("write_record", True))
            include_findings = bool(args.get("include_findings", True))

            record = lifecycle.evaluate(
                cap_id, scope=scope_str, write_record=write_record,
            )

            result: dict = {
                "capability_id": cap_id,
                "scope": scope_str or "",
                "content_hash": record.content_hash,
                "evaluator_version": record.evaluator_version,
                "created_at": record.created_at,
                "passed": record.passed,
                "score": record.score,
                "required_approval": record.required_approval,
                "recommended_maturity": record.recommended_maturity,
            }

            if include_findings:
                result["findings"] = [
                    {
                        "severity": f.severity.value,
                        "code": f.code,
                        "message": f.message,
                        "location": f.location,
                    }
                    for f in record.findings
                ]

            if write_record:
                result["eval_record_id"] = record.created_at

            return ToolExecutionResult(success=True, payload=result)
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("evaluate_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_store_unavailable", "detail": str(e)},
                reason=f"evaluate_capability failed: {e}",
            )

    return executor


def _make_plan_capability_transition_executor(lifecycle: "CapabilityLifecycleManager"):
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
                    reason="plan_capability_transition requires an id",
                )

            target = str(args.get("target", "")).strip()
            if target not in ("testing", "stable", "broken", "repairing", "disabled", "archived"):
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"Invalid target '{target}'"},
                    reason=f"Invalid target: {target}",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            approval = args.get("approval")
            failure_evidence = args.get("failure_evidence")
            reason = args.get("reason")

            plan = lifecycle.plan_transition(
                cap_id,
                target,
                scope=scope_str,
                approval=approval,
                failure_evidence=failure_evidence,
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "capability_id": cap_id,
                    "scope": plan.scope,
                    "from_maturity": plan.from_maturity,
                    "target": target,
                    "allowed": plan.allowed,
                    "required_approval": plan.required_approval,
                    "required_evidence": plan.required_evidence,
                    "blocking_findings": plan.blocking_findings,
                    "policy_decisions": plan.policy_decisions,
                    "explanation": plan.explanation,
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("plan_capability_transition failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_store_unavailable", "detail": str(e)},
                reason=f"plan_capability_transition failed: {e}",
            )

    return executor


def _make_transition_capability_executor(lifecycle: "CapabilityLifecycleManager"):
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
                    reason="transition_capability requires an id",
                )

            target = str(args.get("target", "")).strip()
            if target not in ("testing", "stable", "broken", "repairing", "disabled", "archived"):
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"Invalid target '{target}'"},
                    reason=f"Invalid target: {target}",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")
            approval = args.get("approval")
            failure_evidence = args.get("failure_evidence")
            reason = args.get("reason")
            dry_run = bool(args.get("dry_run", False))

            if dry_run:
                plan = lifecycle.plan_transition(
                    cap_id,
                    target,
                    scope=scope_str,
                    approval=approval,
                    failure_evidence=failure_evidence,
                )
                return ToolExecutionResult(
                    success=True,
                    payload={
                        "capability_id": cap_id,
                        "scope": plan.scope,
                        "from_maturity": plan.from_maturity,
                        "target": target,
                        "applied": False,
                        "dry_run": True,
                        "allowed": plan.allowed,
                        "required_approval": plan.required_approval,
                        "required_evidence": plan.required_evidence,
                        "blocking_findings": plan.blocking_findings,
                        "policy_decisions": plan.policy_decisions,
                        "explanation": plan.explanation,
                    },
                )

            result = lifecycle.apply_transition(
                cap_id,
                target,
                scope=scope_str,
                approval=approval,
                failure_evidence=failure_evidence,
                reason=reason,
            )

            payload: dict = {
                "capability_id": result.capability_id,
                "scope": result.scope,
                "from_maturity": result.from_maturity,
                "to_maturity": result.to_maturity,
                "from_status": result.from_status,
                "to_status": result.to_status,
                "applied": result.applied,
                "message": result.message,
                "content_hash_before": result.content_hash_before,
                "content_hash_after": result.content_hash_after,
            }

            if result.eval_record_id:
                payload["eval_record_id"] = result.eval_record_id
            if result.version_snapshot_id:
                payload["version_snapshot_id"] = result.version_snapshot_id
            if result.policy_decisions:
                payload["policy_decisions"] = result.policy_decisions
            if result.blocking_findings:
                payload["blocking_findings"] = result.blocking_findings

            return ToolExecutionResult(
                success=result.applied,
                payload=payload,
                reason="" if result.applied else result.message,
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("transition_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "transition_failed", "detail": str(e)},
                reason=f"transition_capability failed: {e}",
            )

    return executor


# ── Registration ─────────────────────────────────────────────────────

def register_capability_tools(
    tool_registry,
    store: "CapabilityStore",
    index: "CapabilityIndex | None" = None,
) -> None:
    """Register read-only capability tools.

    Only list/search/view/load. No create, disable, archive, promote, or execution tools.
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

    tool_registry.register(ToolSpec(
        name="load_capability",
        description=(
            "按需读取能力文档的指定部分。只读，不执行脚本，不提升成熟度，"
            "不安装、不授权、不修改能力。默认返回 manifest 与正文。"
        ),
        json_schema=LOAD_CAPABILITY_SCHEMA,
        executor=_make_load_capability_executor(store, index),
        capability="capability_read",
        risk_level="low",
    ))

    logger.info("Phase 2B capability read tools registered (list/search/view/load)")


def register_capability_lifecycle_tools(
    tool_registry,
    lifecycle: "CapabilityLifecycleManager",
) -> None:
    """Register Phase 3C lifecycle management tools.

    Three tools: evaluate_capability, plan_capability_transition,
    transition_capability. All use capability_lifecycle tag so they
    require an explicit operator profile — not granted to standard/default.
    """
    if lifecycle is None:
        logger.warning("register_capability_lifecycle_tools called with lifecycle=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="evaluate_capability",
        description=(
            "对指定能力运行确定性评估器（CapabilityEvaluator），检查安全性、"
            "质量、完整性。可选择性将 EvalRecord 持久化到 evals/ 目录。"
            "此工具不会修改能力的 maturity 或 status。"
            "不会执行脚本。"
        ),
        json_schema=EVALUATE_CAPABILITY_SCHEMA,
        executor=_make_evaluate_capability_executor(lifecycle),
        capability="capability_lifecycle",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="plan_capability_transition",
        description=(
            "预览一个生命周期转换是否会通过 policy/evaluator/planner 门控。"
            "纯只读操作，不会产生任何文件变更、快照写入、索引刷新或变更日志。"
            "返回 allowed 及所需的 approval/evidence/findings。"
        ),
        json_schema=PLAN_CAPABILITY_TRANSITION_SCHEMA,
        executor=_make_plan_capability_transition_executor(lifecycle),
        capability="capability_lifecycle",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="transition_capability",
        description=(
            "对指定能力执行受控生命周期转换。转换必须通过 planner → policy → "
            "evaluator 门控。被阻止的转换不会产生任何文件/索引/变更日志修改。"
            "成功的转换会在变更前写入版本快照、更新 manifest maturity/status、"
            "重新计算 content_hash、刷新索引并可选记录 MutationLog。"
            "设置 dry_run=true 可进行纯预览而不执行变更。"
            "不会执行脚本、导入脚本或运行 shell。"
        ),
        json_schema=TRANSITION_CAPABILITY_SCHEMA,
        executor=_make_transition_capability_executor(lifecycle),
        capability="capability_lifecycle",
        risk_level="medium",
    ))

    logger.info("Phase 3C capability lifecycle tools registered (evaluate/plan/transition)")


def register_capability_runner_tools(
    tool_registry,
    store: "CapabilityStore",
) -> None:
    """Register the gated capability-native runner.

    Supports explicit skill_bridge capabilities and executable_script
    capabilities after latest-valid eval and reuse preflight.
    """
    if store is None:
        logger.warning("register_capability_runner_tools called with store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="run_capability",
        description=(
            "Run a stable capability through the capability reuse preflight. "
            "Supports explicit skill_bridge and executable_script entrypoints; "
            "procedural capabilities are refused."
        ),
        json_schema=RUN_CAPABILITY_SCHEMA,
        executor=_make_run_capability_executor(store),
        capability="capability_runner",
        risk_level="medium",
    ))

    logger.info("Capability runner tool registered (skill_bridge/executable_script)")


# ── Phase 5A: Curator tool schemas ──────────────────────────────────────

REFLECT_EXPERIENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "trace_summary": {
            "type": "object",
            "description": (
                "TraceSummary dict with fields: trace_id, user_request (required), "
                "final_result, task_type, context, tools_used, files_touched, "
                "commands_run, errors_seen, failed_attempts, successful_steps, "
                "verification, user_feedback, existing_capability_id, created_at, metadata"
            ),
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope for any generated proposal (default: workspace)",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, return decision + curated experience without persisting (default: true)",
        },
    },
    "required": ["trace_summary"],
}

PROPOSE_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "trace_summary": {
            "type": "object",
            "description": "Optional TraceSummary dict (required if curated_experience not provided)",
        },
        "curated_experience": {
            "type": "object",
            "description": "Optional CuratedExperience dict (required if trace_summary not provided; takes precedence if both given)",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Scope for the proposal (default: workspace)",
        },
        "capability_type": {
            "type": "string",
            "enum": ["skill", "workflow", "project_playbook"],
            "description": "Capability type (default: from curated_experience recommendation)",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Risk level (default: from curator decision)",
        },
        "apply": {
            "type": "boolean",
            "description": "If true, also create a draft capability in the store (default: false)",
        },
        "approval": {
            "type": "object",
            "description": "Approval object (required for high-risk proposals when apply=true)",
            "properties": {
                "approved": {"type": "boolean"},
                "approved_by": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "proposed_id": {
            "type": "string",
            "description": "Custom proposal_id (auto-generated if omitted)",
        },
        "name": {
            "type": "string",
            "description": "Custom capability name (auto-derived if omitted)",
        },
    },
}


# ── Phase 5A: Curator tool executors ────────────────────────────────────


def _make_reflect_experience_executor(store: "CapabilityStore", index: "CapabilityIndex | None"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.curator import ExperienceCurator
            from src.capabilities.trace_summary import TraceSummary

            args = request.arguments
            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope")

            trace_dict = args.get("trace_summary")
            if not isinstance(trace_dict, dict):
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "trace_summary is required and must be an object"},
                    reason="reflect_experience requires a trace_summary dict",
                )

            try:
                trace = TraceSummary.from_dict(trace_dict)
            except ValueError as e:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"Invalid trace_summary: {e}"},
                    reason=str(e),
                )

            sanitized = trace.sanitize()

            curator = ExperienceCurator()
            decision = curator.should_reflect(sanitized)
            experience = curator.summarize(sanitized)

            return ToolExecutionResult(
                success=True,
                payload={
                    "decision": decision.to_dict(),
                    "experience": experience.to_dict(),
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("reflect_experience failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_curator_unavailable", "detail": str(e)},
                reason=f"reflect_experience failed: {e}",
            )

    return executor


def _make_propose_capability_executor(
    store: "CapabilityStore",
    index: "CapabilityIndex | None",
    data_dir: str = "data/capabilities",
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.curator import CuratedExperience, ExperienceCurator
            from src.capabilities.proposal import (
                CapabilityProposal,
                mark_applied,
                persist_proposal,
            )
            from src.capabilities.trace_summary import TraceSummary

            args = request.arguments
            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope") or "workspace"
            cap_type = _validate_enum(args.get("capability_type"), {"skill", "workflow", "project_playbook"}, "capability_type")
            risk_level = _validate_enum(args.get("risk_level"), ALLOWED_RISK_LEVELS, "risk_level")
            apply_flag = bool(args.get("apply", False))
            approval = args.get("approval")
            proposed_id = str(args.get("proposed_id", "")).strip() or None
            name = str(args.get("name", "")).strip() or None

            # ── Build proposal ─────────────────────────────────────
            curator = ExperienceCurator()
            sanitized_trace: TraceSummary | None = None

            curated_dict = args.get("curated_experience")
            if isinstance(curated_dict, dict):
                # Use provided curated experience directly.
                ce = curated_dict
                experience = CuratedExperience(
                    problem=str(ce.get("problem", "")),
                    context=str(ce.get("context", "")),
                    successful_steps=_as_strs(ce.get("successful_steps", [])),
                    failed_attempts=_as_strs(ce.get("failed_attempts", [])),
                    key_commands=_as_strs(ce.get("key_commands", [])),
                    key_files=_as_strs(ce.get("key_files", [])),
                    required_tools=_as_strs(ce.get("required_tools", [])),
                    verification=_as_strs(ce.get("verification", [])),
                    pitfalls=_as_strs(ce.get("pitfalls", [])),
                    generalization_boundary=str(ce.get("generalization_boundary", "")),
                    recommended_capability_type=str(ce.get("recommended_capability_type", "skill")),
                    suggested_triggers=_as_strs(ce.get("suggested_triggers", [])),
                    suggested_tags=_as_strs(ce.get("suggested_tags", [])),
                    source_trace_id=str(ce["source_trace_id"]) if ce.get("source_trace_id") is not None else None,
                )
                decision = curator.should_reflect(TraceSummary.from_dict({"user_request": experience.problem}))
            elif isinstance(args.get("trace_summary"), dict):
                trace_dict = args["trace_summary"]
                try:
                    trace = TraceSummary.from_dict(trace_dict)
                except ValueError as e:
                    return ToolExecutionResult(
                        success=False,
                        payload={"error": f"Invalid trace_summary: {e}"},
                        reason=str(e),
                    )
                sanitized_trace = trace.sanitize()
                decision = curator.should_reflect(sanitized_trace)
                experience = curator.summarize(sanitized_trace)
            else:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "Must provide trace_summary or curated_experience"},
                    reason="propose_capability requires one of trace_summary or curated_experience",
                )

            proposal = curator.propose_capability(
                experience,
                scope=scope_str,
                cap_type=cap_type,
                risk_level=risk_level or decision.risk_level,
                approval=approval,
                proposed_id=proposed_id,
                name=name,
            )
            proposal.curator_decision = decision.to_dict()

            # ── apply=false (default): persist proposal only ───────
            if not apply_flag:
                from pathlib import Path

                trace_to_persist = sanitized_trace if sanitized_trace else TraceSummary.from_dict(
                    {"user_request": experience.problem or "curated experience"}
                ).sanitize()

                try:
                    prop_dir = persist_proposal(proposal, trace_to_persist, data_dir)
                    return ToolExecutionResult(
                        success=True,
                        payload={
                            "proposal_id": proposal.proposal_id,
                            "proposed_capability_id": proposal.proposed_capability_id,
                            "proposal_dir": str(prop_dir),
                            "applied": False,
                            "name": proposal.name,
                            "type": proposal.type,
                            "scope": proposal.scope,
                            "risk_level": proposal.risk_level,
                            "required_approval": proposal.required_approval,
                            "decision": decision.to_dict(),
                        },
                    )
                except FileExistsError:
                    return ToolExecutionResult(
                        success=False,
                        payload={"error": f"Proposal '{proposal.proposal_id}' already exists"},
                        reason=f"Proposal ID collision: {proposal.proposal_id}",
                    )
                except Exception as e:
                    logger.debug("propose_capability persist failed", exc_info=True)
                    return ToolExecutionResult(
                        success=False,
                        payload={"error": "proposal_persist_failed", "detail": str(e)},
                        reason=f"persist_proposal failed: {e}",
                    )

            # ── apply=true: create draft capability ─────────────────
            # High risk requires approval.
            if proposal.risk_level == "high" or proposal.required_approval:
                if not isinstance(approval, dict) or not approval.get("approved", False):
                    return ToolExecutionResult(
                        success=False,
                        payload={
                            "error": "approval required for high-risk capability proposal",
                            "proposal_id": proposal.proposal_id,
                            "risk_level": proposal.risk_level,
                            "required_approval": True,
                        },
                        reason="High-risk proposal requires explicit approval to apply",
                    )

            # Medium risk: if approval provided but rejected, block.
            if proposal.risk_level == "medium" and isinstance(approval, dict):
                if not approval.get("approved", False):
                    return ToolExecutionResult(
                        success=False,
                        payload={
                            "error": "approval denied for medium-risk capability proposal",
                            "proposal_id": proposal.proposal_id,
                        },
                        reason="Medium-risk proposal approval was denied",
                    )

            # First persist the proposal.
            from pathlib import Path

            trace_to_persist = sanitized_trace if sanitized_trace else TraceSummary.from_dict(
                {"user_request": experience.problem or "curated experience"}
            ).sanitize()

            try:
                prop_dir = persist_proposal(proposal, trace_to_persist, data_dir)
            except FileExistsError:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": f"Proposal '{proposal.proposal_id}' already exists"},
                    reason=f"Proposal ID collision: {proposal.proposal_id}",
                )
            except Exception as e:
                logger.debug("propose_capability persist failed", exc_info=True)
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "proposal_persist_failed", "detail": str(e)},
                    reason=f"persist_proposal failed: {e}",
                )

            # Create draft capability through store.
            from src.capabilities.schema import CapabilityScope

            try:
                doc = store.create_draft(
                    scope=CapabilityScope(scope_str),
                    cap_id=proposal.proposed_capability_id,
                    name=proposal.name,
                    description=proposal.description,
                    type=proposal.type,
                    body=proposal.body_markdown,
                    risk_level=proposal.risk_level,
                    tags=proposal.tags,
                    triggers=proposal.triggers,
                    trust_required=proposal.trust_required,
                    required_tools=proposal.required_tools,
                    required_permissions=proposal.required_permissions,
                )
            except FileExistsError:
                return ToolExecutionResult(
                    success=False,
                    payload={
                        "error": f"Capability '{proposal.proposed_capability_id}' already exists",
                        "proposal_id": proposal.proposal_id,
                    },
                    reason="Draft capability creation failed: ID already exists",
                )
            except Exception as e:
                logger.debug("propose_capability create_draft failed", exc_info=True)
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "draft_creation_failed", "detail": str(e),
                             "proposal_id": proposal.proposal_id},
                    reason=f"create_draft failed: {e}",
                )

            # Run evaluator.
            eval_record_id = None
            try:
                from src.capabilities.evaluator import CapabilityEvaluator
                from src.capabilities.eval_records import write_eval_record

                evaluator = CapabilityEvaluator()
                eval_record = evaluator.evaluate(doc)
                write_eval_record(eval_record, doc)
                eval_record_id = eval_record.created_at
            except Exception:
                logger.debug("Evaluator run failed during apply=true", exc_info=True)

            # Refresh index.
            try:
                if index is not None:
                    index.upsert(doc)
            except Exception:
                logger.debug("Index refresh failed during apply=true", exc_info=True)

            # Mark proposal as applied.
            try:
                mark_applied(proposal.proposal_id, doc.manifest.id, data_dir)
            except Exception:
                logger.debug("mark_applied failed during apply=true", exc_info=True)

            return ToolExecutionResult(
                success=True,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "proposal_dir": str(prop_dir),
                    "applied": True,
                    "capability_id": doc.manifest.id,
                    "name": doc.manifest.name,
                    "type": doc.manifest.type.value,
                    "scope": doc.manifest.scope.value,
                    "maturity": doc.manifest.maturity.value,
                    "status": doc.manifest.status.value,
                    "risk_level": proposal.risk_level,
                    "eval_record_id": eval_record_id,
                    "content_hash": doc.content_hash,
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("propose_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "capability_curator_unavailable", "detail": str(e)},
                reason=f"propose_capability failed: {e}",
            )

    return executor


def _as_strs(value: object) -> list[str]:
    """Coerce a value to a list of strings."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


# ── Phase 5A: Curator tool registration ────────────────────────────────


def register_capability_curator_tools(
    tool_registry,
    store: "CapabilityStore",
    index: "CapabilityIndex | None" = None,
    data_dir: str = "data/capabilities",
) -> None:
    """Register Phase 5A curator tools.

    Two tools: reflect_experience, propose_capability.
    Both use capability_curator tag so they require an explicit
    operator profile — not granted to standard/default/chat/inner_tick.
    """
    if store is None:
        logger.warning("register_capability_curator_tools called with store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="reflect_experience",
        description=(
            "对一次任务追踪进行反思，确定是否应创建新能力。"
            "使用确定性启发式算法（无 LLM/网络/Shell），返回 CuratorDecision "
            "和 CuratedExperience。不会修改存储或索引，不会创建文件。"
        ),
        json_schema=REFLECT_EXPERIENCE_SCHEMA,
        executor=_make_reflect_experience_executor(store, index),
        capability="capability_curator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="propose_capability",
        description=(
            "根据对任务追踪的反思，创建一个能力提案。apply=false 时，"
            "仅创建 proposal.json / PROPOSAL.md / 源追踪摘要文件，"
            "不会修改存储或索引。apply=true 时，还会创建草稿能力、"
            "运行评估器并写入 EvalRecord（但绝不提升 maturity）。"
            "高风险提案需要批准对象。"
        ),
        json_schema=PROPOSE_CAPABILITY_SCHEMA,
        executor=_make_propose_capability_executor(store, index, data_dir),
        capability="capability_curator",
        risk_level="medium",
    ))

    logger.info("Phase 5A capability curator tools registered (reflect/propose)")


# ── Phase 7A: Import tool schemas ──────────────────────────────────────

INSPECT_CAPABILITY_PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Local filesystem path to the capability package directory",
        },
        "scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Target scope for import (default: user)",
        },
        "include_files": {
            "type": "boolean",
            "description": "Include file listings in response (default: true)",
        },
    },
    "required": ["path"],
}

IMPORT_CAPABILITY_PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Local filesystem path to the capability package directory",
        },
        "target_scope": {
            "type": "string",
            "enum": ["global", "user", "workspace", "session"],
            "description": "Target scope for the imported capability (default: user)",
        },
        "imported_by": {
            "type": "string",
            "description": "Identifier of the person or system performing the import",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for the import",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, inspect only without copying or writing (default: false)",
        },
    },
    "required": ["path"],
}


# ── Phase 7A: Import tool executors ────────────────────────────────────


def _make_inspect_capability_package_executor(
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.import_quarantine import inspect_capability_package as _inspect

            args = request.arguments
            path_str = str(args.get("path", "")).strip()
            if not path_str:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "path is required"},
                    reason="inspect_capability_package requires a path",
                )

            scope_str = _validate_enum(args.get("scope"), ALLOWED_SCOPES, "scope") or "user"
            include_files = bool(args.get("include_files", True))

            result = _inspect(
                path=path_str,
                store=store,
                evaluator=evaluator,
                policy=policy,
                target_scope=scope_str,
                include_files=include_files,
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "id": result.id,
                    "name": result.name,
                    "description": result.description,
                    "type": result.type,
                    "declared_scope": result.declared_scope,
                    "target_scope": result.target_scope,
                    "maturity": result.maturity,
                    "status": result.status,
                    "risk_level": result.risk_level,
                    "required_tools": result.required_tools,
                    "required_permissions": result.required_permissions,
                    "triggers": result.triggers,
                    "tags": result.tags,
                    "files": result.files,
                    "eval_findings": result.eval_findings,
                    "eval_passed": result.eval_passed,
                    "eval_score": result.eval_score,
                    "policy_findings": result.policy_findings,
                    "would_import": result.would_import,
                    "quarantine_reason": result.quarantine_reason,
                    "warnings": result.warnings,
                    "errors": result.errors,
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("inspect_capability_package failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "inspect_package_failed", "detail": str(e)},
                reason=f"inspect_capability_package failed: {e}",
            )

    return executor


def _make_import_capability_package_executor(
    store: "CapabilityStore",
    index: "CapabilityIndex | None",
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.import_quarantine import import_capability_package as _import

            args = request.arguments
            path_str = str(args.get("path", "")).strip()
            if not path_str:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "path is required"},
                    reason="import_capability_package requires a path",
                )

            scope_str = _validate_enum(args.get("target_scope"), ALLOWED_SCOPES, "target_scope") or "user"
            imported_by = str(args.get("imported_by", "")).strip() or None
            reason = str(args.get("reason", "")).strip() or None
            dry_run = bool(args.get("dry_run", False))

            result = _import(
                path=path_str,
                store=store,
                evaluator=evaluator,
                policy=policy,
                index=index,
                target_scope=scope_str,
                imported_by=imported_by,
                reason=reason,
                dry_run=dry_run,
            )

            if result.dry_run or not result.applied:
                success = result.dry_run  # dry_run is not an error
                inspect_payload = None
                if result.inspect_result:
                    ir = result.inspect_result
                    inspect_payload = {
                        "id": ir.id,
                        "name": ir.name,
                        "type": ir.type,
                        "target_scope": ir.target_scope,
                        "maturity": ir.maturity,
                        "status": ir.status,
                        "risk_level": ir.risk_level,
                        "eval_passed": ir.eval_passed,
                        "eval_score": ir.eval_score,
                        "would_import": ir.would_import,
                        "quarantine_reason": ir.quarantine_reason,
                        "warnings": ir.warnings,
                    }

                return ToolExecutionResult(
                    success=success,
                    payload={
                        "capability_id": result.capability_id,
                        "dry_run": dry_run,
                        "applied": False,
                        "inspect": inspect_payload,
                        "errors": result.errors,
                    },
                    reason="; ".join(result.errors) if result.errors else "",
                )

            # Successful import
            return ToolExecutionResult(
                success=True,
                payload={
                    "capability_id": result.capability_id,
                    "quarantine_path": result.quarantine_path,
                    "import_report_path": result.import_report_path,
                    "dry_run": False,
                    "applied": True,
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("import_capability_package failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "import_package_failed", "detail": str(e)},
                reason=f"import_capability_package failed: {e}",
            )

    return executor


# ── Phase 7A: Import tool registration ──────────────────────────────────


def register_capability_import_tools(
    tool_registry,
    store: "CapabilityStore",
    index: "CapabilityIndex | None",
    evaluator: Any,
    policy: Any,
) -> None:
    """Register Phase 7A external import tools.

    Two tools: inspect_capability_package, import_capability_package.
    Both use capability_import_operator tag so they require an explicit
    operator profile — not granted to standard/default/chat/inner_tick.
    """
    if store is None:
        logger.warning("register_capability_import_tools called with store=None, skipping")
        return
    if evaluator is None:
        logger.warning("register_capability_import_tools called with evaluator=None, skipping")
        return
    if policy is None:
        logger.warning("register_capability_import_tools called with policy=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="inspect_capability_package",
        description=(
            "检查外部能力包，不执行任何写入操作。解析包目录、运行评估器和"
            "策略检查，返回检查结果和安全发现。不会复制文件、执行脚本、"
            "更新索引或访问网络。"
        ),
        json_schema=INSPECT_CAPABILITY_PACKAGE_SCHEMA,
        executor=_make_inspect_capability_package_executor(store, evaluator, policy),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="import_capability_package",
        description=(
            "将外部能力包导入隔离存储区。导入的能力处于隔离状态（status=quarantined, "
            "maturity=draft），永远不会被自动激活、执行或提升。导入不会执行包中的脚本。"
            "设置 dry_run=true 可进行纯检查而不写入任何文件。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=IMPORT_CAPABILITY_PACKAGE_SCHEMA,
        executor=_make_import_capability_package_executor(store, index, evaluator, policy),
        capability="capability_import_operator",
        risk_level="medium",
    ))

    logger.info("Phase 7A capability import tools registered (inspect/import)")


# ── Phase 7B: Quarantine review tool schemas ──────────────────────────


LIST_QUARANTINED_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "risk_level": {
            "type": "string",
            "description": "Filter by risk level: low, medium, high",
        },
        "review_status": {
            "type": "string",
            "description": "Filter by latest review status: needs_changes, approved_for_testing, rejected",
        },
        "imported_after": {
            "type": "string",
            "description": "ISO 8601 timestamp; only show capabilities imported after this time",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 20, max 100)",
        },
    },
}

VIEW_QUARANTINE_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "include_findings": {
            "type": "boolean",
            "description": "Include eval/policy findings (default: true)",
        },
        "include_files_summary": {
            "type": "boolean",
            "description": "Include files summary (default: true)",
        },
    },
    "required": ["capability_id"],
}

AUDIT_QUARANTINED_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID to audit",
        },
        "write_report": {
            "type": "boolean",
            "description": "Write audit report to quarantine storage (default: true)",
        },
    },
    "required": ["capability_id"],
}

MARK_QUARANTINE_REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "review_status": {
            "type": "string",
            "description": "Review decision: needs_changes, approved_for_testing, rejected",
        },
        "reviewer": {
            "type": "string",
            "description": "Name/ID of the reviewer (optional)",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for the review decision (required)",
        },
        "expires_at": {
            "type": "string",
            "description": "ISO 8601 timestamp when this review expires (optional)",
        },
    },
    "required": ["capability_id", "review_status", "reason"],
}


# ── Phase 7B: Quarantine review tool executors ────────────────────────


def _make_list_quarantined_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_review import list_quarantined_capabilities as _list

            args = request.arguments
            risk_level = args.get("risk_level")
            review_status = args.get("review_status")
            imported_after = args.get("imported_after")
            limit = min(int(args.get("limit", 20)), 100)

            results = _list(
                store_data_dir=store.data_dir,
                risk_level=risk_level,
                review_status=review_status,
                imported_after=imported_after,
                limit=limit,
            )

            return ToolExecutionResult(
                success=True,
                payload={"capabilities": results, "count": len(results)},
            )
        except Exception as e:
            logger.debug("list_quarantined_capabilities failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "list_quarantined_failed", "detail": str(e)},
                reason=f"list_quarantined_capabilities failed: {e}",
            )

    return executor


def _make_view_quarantine_report_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_review import view_quarantine_report as _view
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="view_quarantine_report requires capability_id",
                )

            include_findings = bool(args.get("include_findings", True))
            include_files = bool(args.get("include_files_summary", True))

            result = _view(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                include_findings=include_findings,
                include_files_summary=include_files,
            )

            return ToolExecutionResult(success=True, payload=result)
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": "not_found", "detail": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("view_quarantine_report failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "view_report_failed", "detail": str(e)},
                reason=f"view_quarantine_report failed: {e}",
            )

    return executor


def _make_audit_quarantined_executor(
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_review import audit_quarantined_capability as _audit
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="audit_quarantined_capability requires capability_id",
                )

            write_report = bool(args.get("write_report", True))

            report = _audit(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                evaluator=evaluator,
                policy=policy,
                write_report=write_report,
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "capability_id": report.capability_id,
                    "audit_id": report.audit_id,
                    "passed": report.passed,
                    "risk_level": report.risk_level,
                    "findings": report.findings,
                    "recommended_review_status": report.recommended_review_status,
                    "remediation_suggestions": report.remediation_suggestions,
                    "written_report": write_report,
                },
            )
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": "not_found", "detail": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("audit_quarantined_capability failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "audit_failed", "detail": str(e)},
                reason=f"audit_quarantined_capability failed: {e}",
            )

    return executor


def _make_mark_quarantine_review_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_review import mark_quarantine_review as _mark
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            review_status = str(args.get("review_status", "")).strip()
            reason = str(args.get("reason", "")).strip()

            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="mark_quarantine_review requires capability_id",
                )

            decision = _mark(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                review_status=review_status,
                reviewer=str(args.get("reviewer", "")).strip(),
                reason=reason,
                expires_at=args.get("expires_at"),
            )

            return ToolExecutionResult(
                success=True,
                payload={
                    "capability_id": decision.capability_id,
                    "review_id": decision.review_id,
                    "review_status": decision.review_status,
                    "reviewer": decision.reviewer,
                    "reason": decision.reason,
                    "created_at": decision.created_at,
                    "expires_at": decision.expires_at,
                },
            )
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("mark_quarantine_review failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "mark_review_failed", "detail": str(e)},
                reason=f"mark_quarantine_review failed: {e}",
            )

    return executor


# ── Phase 7B: Quarantine review tool registration ─────────────────────


def register_quarantine_review_tools(
    tool_registry,
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
) -> None:
    """Register Phase 7B quarantine review tools.

    Four tools: list_quarantined_capabilities, view_quarantine_report,
    audit_quarantined_capability, mark_quarantine_review.

    All use capability_import_operator tag — operator-only, not granted
    to standard/default/chat/local_execution/browser/identity.
    """
    if store is None:
        logger.warning("register_quarantine_review_tools called with store=None, skipping")
        return
    if evaluator is None:
        logger.warning("register_quarantine_review_tools called with evaluator=None, skipping")
        return
    if policy is None:
        logger.warning("register_quarantine_review_tools called with policy=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="list_quarantined_capabilities",
        description=(
            "列出所有处于隔离状态的能力包。只读操作，只访问隔离存储目录，"
            "不会触碰活跃能力列表。返回紧凑摘要（不包含脚本内容或源路径）。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=LIST_QUARANTINED_SCHEMA,
        executor=_make_list_quarantined_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_quarantine_report",
        description=(
            "查看隔离能力的导入报告和已有的评估/策略检查发现。"
            "只读操作，不会执行脚本或导入代码。返回不含脚本内容和源路径的报告。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=VIEW_QUARANTINE_REPORT_SCHEMA,
        executor=_make_view_quarantine_report_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="audit_quarantined_capability",
        description=(
            "对隔离能力进行确定性本地审计。重新运行评估器和策略检查，"
            "扫描文档中的危险模式、提示注入、缺失章节、工具/权限风险。"
            "永远不会执行脚本、导入 Python 代码、运行测试、访问网络或调用 LLM。"
            "如果 write_report=true，会将审计报告写入隔离存储。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=AUDIT_QUARANTINED_SCHEMA,
        executor=_make_audit_quarantined_executor(store, evaluator, policy),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="mark_quarantine_review",
        description=(
            "为隔离能力写入审查决策。审查状态可以是 needs_changes、"
            "approved_for_testing 或 rejected。此操作仅写入审查报告，"
            "不会改变能力的隔离状态或草稿成熟度，不会激活或提升能力。"
            "approved_for_testing 只是标记审查通过，不会使能力可检索或可运行。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=MARK_QUARANTINE_REVIEW_SCHEMA,
        executor=_make_mark_quarantine_review_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    logger.info("Phase 7B quarantine review tools registered (list/view/audit/mark)")


# ── Phase 7C: Quarantine transition request tool schemas ──────────────


REQUEST_TRANSITION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "requested_target_scope": {
            "type": "string",
            "enum": ["user", "workspace", "session", "global"],
            "description": "Target scope for the eventual transition (default: user)",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for the transition request (required)",
        },
        "created_by": {
            "type": "string",
            "description": "Identifier of the operator creating the request (optional)",
        },
        "source_review_id": {
            "type": "string",
            "description": "Specific review ID to reference (default: latest approved_for_testing review)",
        },
        "source_audit_id": {
            "type": "string",
            "description": "Specific audit report ID to reference (default: latest audit)",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, validate gates without writing (default: false)",
        },
    },
    "required": ["capability_id", "reason"],
}

LIST_TRANSITION_REQUESTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Filter by quarantined capability ID (optional)",
        },
        "status": {
            "type": "string",
            "enum": ["pending", "cancelled", "rejected", "superseded"],
            "description": "Filter by request status",
        },
        "target_scope": {
            "type": "string",
            "enum": ["user", "workspace", "session", "global"],
            "description": "Filter by requested target scope",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 20, max 100)",
        },
    },
}

VIEW_TRANSITION_REQUEST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "request_id": {
            "type": "string",
            "description": "Transition request ID to view",
        },
    },
    "required": ["capability_id", "request_id"],
}

CANCEL_TRANSITION_REQUEST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "request_id": {
            "type": "string",
            "description": "Transition request ID to cancel",
        },
        "reason": {
            "type": "string",
            "description": "Reason for cancellation (required)",
        },
    },
    "required": ["capability_id", "request_id", "reason"],
}


# ── Phase 7C: Quarantine transition request tool executors ────────────


def _make_request_transition_executor(
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_transition import (
                request_quarantine_testing_transition as _req,
            )
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="request_quarantine_testing_transition requires capability_id",
                )

            scope = str(args.get("requested_target_scope", "user")).strip()
            reason = str(args.get("reason", "")).strip()
            dry_run = bool(args.get("dry_run", False))

            result = _req(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                requested_target_scope=scope,
                reason=reason,
                evaluator=evaluator,
                policy=policy,
                created_by=str(args.get("created_by", "")).strip() or None,
                source_review_id=str(args.get("source_review_id", "")).strip() or None,
                source_audit_id=str(args.get("source_audit_id", "")).strip() or None,
                dry_run=dry_run,
            )

            if result.get("would_create"):
                return ToolExecutionResult(success=True, payload=result)
            else:
                return ToolExecutionResult(
                    success=False,
                    payload=result,
                    reason="; ".join(result.get("blocking_reasons", [])),
                )
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e), "blocking_reasons": [str(e)]},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("request_quarantine_testing_transition failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "request_transition_failed", "detail": str(e)},
                reason=f"request_quarantine_testing_transition failed: {e}",
            )

    return executor


def _make_list_transition_requests_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_transition import (
                list_quarantine_transition_requests as _list,
            )

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip() or None
            status = str(args.get("status", "")).strip() or None
            target_scope = str(args.get("target_scope", "")).strip() or None
            limit = min(int(args.get("limit", 20)), 100)

            results = _list(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                status=status,
                target_scope=target_scope,
                limit=limit,
            )

            return ToolExecutionResult(
                success=True,
                payload={"requests": results, "count": len(results)},
            )
        except Exception as e:
            logger.debug("list_quarantine_transition_requests failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "list_transition_requests_failed", "detail": str(e)},
                reason=f"list_quarantine_transition_requests failed: {e}",
            )

    return executor


def _make_view_transition_request_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_transition import (
                view_quarantine_transition_request as _view,
            )
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            req_id = str(args.get("request_id", "")).strip()

            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="view_quarantine_transition_request requires capability_id",
                )
            if not req_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "request_id is required"},
                    reason="view_quarantine_transition_request requires request_id",
                )

            result = _view(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                request_id=req_id,
            )

            return ToolExecutionResult(success=True, payload=result)
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": "not_found", "detail": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("view_quarantine_transition_request failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "view_request_failed", "detail": str(e)},
                reason=f"view_quarantine_transition_request failed: {e}",
            )

    return executor


def _make_cancel_transition_request_executor(store: "CapabilityStore"):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_transition import (
                cancel_quarantine_transition_request as _cancel,
            )
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            req_id = str(args.get("request_id", "")).strip()
            reason = str(args.get("reason", "")).strip()

            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="cancel_quarantine_transition_request requires capability_id",
                )
            if not req_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "request_id is required"},
                    reason="cancel_quarantine_transition_request requires request_id",
                )

            result = _cancel(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                request_id=req_id,
                reason=reason,
            )

            return ToolExecutionResult(success=True, payload=result)
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e)},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("cancel_quarantine_transition_request failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "cancel_request_failed", "detail": str(e)},
                reason=f"cancel_quarantine_transition_request failed: {e}",
            )

    return executor


# ── Phase 7C: Quarantine transition request tool registration ─────────


def register_quarantine_transition_tools(
    tool_registry,
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
) -> None:
    """Register Phase 7C quarantine transition request tools.

    Four tools: request_quarantine_testing_transition,
    list_quarantine_transition_requests, view_quarantine_transition_request,
    cancel_quarantine_transition_request.

    All use capability_import_operator tag — operator-only, not granted
    to standard/default/chat/local_execution/browser/identity.

    No activate/promote/apply/run tools are created.
    """
    if store is None:
        logger.warning("register_quarantine_transition_tools called with store=None, skipping")
        return
    if evaluator is None:
        logger.warning("register_quarantine_transition_tools called with evaluator=None, skipping")
        return
    if policy is None:
        logger.warning("register_quarantine_transition_tools called with policy=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="request_quarantine_testing_transition",
        description=(
            "为通过审查的隔离能力创建测试转换请求。此操作仅创建请求对象，"
            "不会激活、提升或移动能力，不会执行脚本或运行测试。"
            "必须满足：能力在隔离区、status=quarantined、maturity=draft、"
            "审查状态为 approved_for_testing、审计报告通过。"
            "设置 dry_run=true 可进行门控检查而不写入请求。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=REQUEST_TRANSITION_SCHEMA,
        executor=_make_request_transition_executor(store, evaluator, policy),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="list_quarantine_transition_requests",
        description=(
            "列出所有隔离区测试转换请求。可按 capability_id/status/target_scope 过滤。"
            "只读操作，返回紧凑摘要，不包含脚本内容或原始路径。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=LIST_TRANSITION_REQUESTS_SCHEMA,
        executor=_make_list_transition_requests_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_quarantine_transition_request",
        description=(
            "查看单个转换请求的完整详情。只读操作，"
            "不包含脚本内容或原始路径。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=VIEW_TRANSITION_REQUEST_SCHEMA,
        executor=_make_view_transition_request_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="cancel_quarantine_transition_request",
        description=(
            "取消一个待处理的测试转换请求。仅将状态从 pending 改为 cancelled，"
            "不会修改能力、删除请求或影响活跃存储/索引。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=CANCEL_TRANSITION_REQUEST_SCHEMA,
        executor=_make_cancel_transition_request_executor(store),
        capability="capability_import_operator",
        risk_level="low",
    ))

    logger.info("Phase 7C quarantine transition request tools registered (request/list/view/cancel)")


# ── Phase 7D-A: Quarantine activation planner tool schema ─────────────


PLAN_ACTIVATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID",
        },
        "request_id": {
            "type": "string",
            "description": "Specific transition request ID (default: latest pending request)",
        },
        "target_scope": {
            "type": "string",
            "enum": ["user", "workspace", "session", "global"],
            "description": "Target scope override (default: from request)",
        },
        "created_by": {
            "type": "string",
            "description": "Identifier of the operator creating this plan (optional)",
        },
        "persist_plan": {
            "type": "boolean",
            "description": "If true (default), write plan JSON under quarantine dir",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, compute plan but write nothing (default: false)",
        },
    },
    "required": ["capability_id"],
}


# ── Phase 7D-A: Quarantine activation planner tool executor ───────────


def _make_plan_activation_executor(
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_activation_planner import (
                plan_quarantine_activation as _plan,
            )
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="plan_quarantine_activation requires capability_id",
                )

            req_id = str(args.get("request_id", "")).strip() or None
            target_scope = str(args.get("target_scope", "")).strip() or None
            created_by = str(args.get("created_by", "")).strip() or None
            persist_plan = bool(args.get("persist_plan", True))
            dry_run = bool(args.get("dry_run", False))

            result = _plan(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                request_id=req_id,
                target_scope=target_scope,
                evaluator=evaluator,
                policy=policy,
                created_by=created_by,
                persist_plan=persist_plan,
                dry_run=dry_run,
            )

            plan = result.get("plan", {})
            allowed = plan.get("allowed", False)

            return ToolExecutionResult(
                success=True,
                payload={
                    "would_activate": False,
                    "plan": plan,
                    "allowed": allowed,
                },
            )
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e), "blocking_reasons": [str(e)]},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("plan_quarantine_activation failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "plan_activation_failed", "detail": str(e)},
                reason=f"plan_quarantine_activation failed: {e}",
            )

    return executor


# ── Phase 7D-A: Quarantine activation planner tool registration ───────


def register_quarantine_activation_planning_tools(
    tool_registry,
    store: "CapabilityStore",
    evaluator: Any,
    policy: Any,
) -> None:
    """Register Phase 7D-A quarantine activation planning tool.

    One tool only: plan_quarantine_activation.
    Uses capability_import_operator tag — operator-only.
    No apply/activate/promote/run tools are created.
    """
    if store is None:
        logger.warning("register_quarantine_activation_planning_tools called with store=None, skipping")
        return
    if evaluator is None:
        logger.warning("register_quarantine_activation_planning_tools called with evaluator=None, skipping")
        return
    if policy is None:
        logger.warning("register_quarantine_activation_planning_tools called with policy=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="plan_quarantine_activation",
        description=(
            "为隔离能力计算激活计划。这是一个纯规划工具——不会激活、"
            "移动、提升或执行能力。验证所有门控条件（审查状态、审计报告、"
            "评估器/策略检查、目标域冲突），并生成允许或阻止的计划。"
            "如果 persist_plan=true（默认），计划 JSON 会写入隔离目录供审计。"
            "设置 dry_run=true 可仅计算计划而不写入任何文件。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=PLAN_ACTIVATION_SCHEMA,
        executor=_make_plan_activation_executor(store, evaluator, policy),
        capability="capability_import_operator",
        risk_level="low",
    ))

    logger.info("Phase 7D-A quarantine activation planning tool registered (plan_quarantine_activation)")


# ── Phase 7D-B: Quarantine activation apply tool schema ───────────────


APPLY_ACTIVATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "capability_id": {
            "type": "string",
            "description": "Quarantined capability ID to activate into testing",
        },
        "plan_id": {
            "type": "string",
            "description": "Specific activation plan ID (default: latest allowed plan)",
        },
        "request_id": {
            "type": "string",
            "description": "Specific transition request ID (default: latest pending request)",
        },
        "target_scope": {
            "type": "string",
            "enum": ["user", "workspace", "session", "global"],
            "description": "Target scope (must match plan/request if provided)",
        },
        "applied_by": {
            "type": "string",
            "description": "Identifier of the operator applying this activation",
        },
        "reason": {
            "type": "string",
            "description": "Required reason for activation",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, perform all gate checks but write nothing (default: false)",
        },
    },
    "required": ["capability_id", "reason"],
}


# ── Phase 7D-B: Quarantine activation apply tool executor ─────────────


def _make_apply_activation_executor(
    store: "CapabilityStore",
    index: Any,
    evaluator: Any,
    policy: Any,
):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            from src.capabilities.quarantine_activation_apply import (
                apply_quarantine_activation as _apply,
            )
            from src.capabilities.errors import CapabilityError

            args = request.arguments
            cap_id = str(args.get("capability_id", "")).strip()
            if not cap_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "capability_id is required"},
                    reason="apply_quarantine_activation requires capability_id",
                )

            reason = str(args.get("reason", "")).strip()
            if not reason:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "reason is required"},
                    reason="apply_quarantine_activation requires reason",
                )

            plan_id = str(args.get("plan_id", "")).strip() or None
            req_id = str(args.get("request_id", "")).strip() or None
            target_scope = str(args.get("target_scope", "")).strip() or None
            applied_by = str(args.get("applied_by", "")).strip() or None
            dry_run = bool(args.get("dry_run", False))

            result = _apply(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                plan_id=plan_id,
                request_id=req_id,
                target_scope=target_scope,
                applied_by=applied_by,
                reason=reason,
                evaluator=evaluator,
                policy=policy,
                index=index,
                dry_run=dry_run,
            )

            payload = result.to_dict()
            return ToolExecutionResult(
                success=True,
                payload=payload,
            )
        except CapabilityError as e:
            return ToolExecutionResult(
                success=False,
                payload={"error": str(e), "blocking_reasons": [str(e)]},
                reason=str(e),
            )
        except Exception as e:
            logger.debug("apply_quarantine_activation failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "apply_activation_failed", "detail": str(e)},
                reason=f"apply_quarantine_activation failed: {e}",
            )

    return executor


# ── Phase 7D-B: Quarantine activation apply tool registration ─────────


def register_quarantine_activation_apply_tools(
    tool_registry,
    store: "CapabilityStore",
    index: Any,
    evaluator: Any,
    policy: Any,
) -> None:
    """Register Phase 7D-B quarantine activation apply tool.

    One tool only: apply_quarantine_activation.
    Uses capability_import_operator tag — operator-only.
    No activate/promote/run/execute tools are created.
    """
    if store is None:
        logger.warning("register_quarantine_activation_apply_tools called with store=None, skipping")
        return
    if evaluator is None:
        logger.warning("register_quarantine_activation_apply_tools called with evaluator=None, skipping")
        return
    if policy is None:
        logger.warning("register_quarantine_activation_apply_tools called with policy=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="apply_quarantine_activation",
        description=(
            "将已批准的隔离能力激活计划应用到测试环境。这是一个显式的操作员工具"
            "——将隔离能力复制到活跃的目标域（maturity=testing, status=active）。"
            "验证所有门控条件（计划已批准、请求待处理、审查/审计仍有效、"
            "评估器/策略检查通过、无目标域冲突），然后执行复制。"
            "高风险能力在 Phase 7D-B 中被阻止。"
            "设置 dry_run=true 可仅检查门控条件而不写入任何文件。"
            "原始隔离副本保持不变（quarantined/draft）。"
            "需要 capability_import_operator 权限。"
        ),
        json_schema=APPLY_ACTIVATION_SCHEMA,
        executor=_make_apply_activation_executor(store, index, evaluator, policy),
        capability="capability_import_operator",
        risk_level="high",
    ))

    logger.info("Phase 7D-B quarantine activation apply tool registered (apply_quarantine_activation)")


# ── Phase 8B-3: Trust root operator tool schemas ─────────────────────


LIST_TRUST_ROOTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["active", "disabled", "revoked"],
            "description": "Filter by trust root status",
        },
        "scope": {
            "type": "string",
            "description": "Filter by scope (global, project, user)",
        },
        "include_expired": {
            "type": "boolean",
            "description": "Include expired trust roots (default true)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Max results (default 50, max 200)",
        },
    },
}

VIEW_TRUST_ROOT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trust_root_id": {
            "type": "string",
            "description": "Trust root ID to view",
        },
    },
    "required": ["trust_root_id"],
}

ADD_TRUST_ROOT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trust_root_id": {
            "type": "string",
            "description": "Unique trust root identifier (filesystem-safe, no path separators or ..)",
        },
        "name": {
            "type": "string",
            "description": "Human-readable name for this trust root",
        },
        "key_type": {
            "type": "string",
            "description": "Key algorithm type (e.g., ed25519, rsa-2048)",
        },
        "public_key_fingerprint": {
            "type": "string",
            "description": "Public key fingerprint (e.g., sha256:abcdef)",
        },
        "owner": {
            "type": "string",
            "description": "Owner identifier (optional)",
        },
        "scope": {
            "type": "string",
            "description": "Trust scope: global, project, user (optional)",
        },
        "expires_at": {
            "type": "string",
            "description": "ISO 8601 expiry timestamp (optional)",
        },
        "metadata": {
            "type": "object",
            "description": "Additional metadata (optional dict)",
        },
    },
    "required": ["trust_root_id", "name", "key_type", "public_key_fingerprint"],
}

DISABLE_TRUST_ROOT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trust_root_id": {
            "type": "string",
            "description": "Trust root ID to disable",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for disabling (optional)",
        },
    },
    "required": ["trust_root_id"],
}

REVOKE_TRUST_ROOT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trust_root_id": {
            "type": "string",
            "description": "Trust root ID to revoke",
        },
        "reason": {
            "type": "string",
            "description": "Human-readable reason for revocation (required)",
        },
    },
    "required": ["trust_root_id", "reason"],
}


# ── Phase 8B-3: Trust root tool executors ────────────────────────────


def _trust_root_compact_summary(root, store) -> dict:
    """Return a compact, secret-free summary of a CapabilityTrustRoot."""
    return {
        "trust_root_id": root.trust_root_id,
        "name": root.name,
        "key_type": root.key_type,
        "public_key_fingerprint": root.public_key_fingerprint,
        "owner": root.owner,
        "scope": root.scope,
        "status": root.status,
        "created_at": root.created_at,
        "expires_at": root.expires_at,
        "is_active": store.is_trust_root_active(root.trust_root_id),
    }


def _make_list_trust_roots_executor(store):
    from datetime import datetime, timezone

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            status = args.get("status")
            scope = args.get("scope")
            include_expired = bool(args.get("include_expired", True))
            limit = min(int(args.get("limit", 50)), 200)

            roots = store.list_trust_roots(status=status, scope=scope)

            # Filter out expired if requested
            if not include_expired:
                now = datetime.now(timezone.utc)
                active_roots = []
                for root in roots:
                    if root.expires_at:
                        try:
                            expires = datetime.fromisoformat(root.expires_at)
                            if expires.tzinfo is None:
                                expires = expires.replace(tzinfo=timezone.utc)
                            if expires <= now:
                                continue
                        except (ValueError, TypeError):
                            continue
                    active_roots.append(root)
                roots = active_roots

            # Apply limit
            results = roots[:limit]

            return ToolExecutionResult(
                success=True,
                payload={
                    "trust_roots": [_trust_root_compact_summary(r, store) for r in results],
                    "count": len(results),
                },
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("list_capability_trust_roots failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "trust_root_list_failed", "detail": str(e)},
                reason=f"list_capability_trust_roots failed: {e}",
            )

    return executor


def _make_view_trust_root_executor(store):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            trust_root_id = str(args.get("trust_root_id", "")).strip()
            if not trust_root_id:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "trust_root_id is required"},
                    reason="view_capability_trust_root requires trust_root_id",
                )

            root = store.get_trust_root(trust_root_id)
            if root is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "detail": f"Trust root {trust_root_id!r} not found"},
                    reason=f"Trust root {trust_root_id!r} not found",
                )

            return ToolExecutionResult(
                success=True,
                payload={
                    "trust_root_id": root.trust_root_id,
                    "name": root.name,
                    "key_type": root.key_type,
                    "public_key_fingerprint": root.public_key_fingerprint,
                    "owner": root.owner,
                    "scope": root.scope,
                    "status": root.status,
                    "created_at": root.created_at,
                    "expires_at": root.expires_at,
                    "is_active": store.is_trust_root_active(trust_root_id),
                    "metadata": root.metadata,
                },
            )
        except Exception as e:
            logger.debug("view_capability_trust_root failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "view_trust_root_failed", "detail": str(e)},
                reason=f"view_capability_trust_root failed: {e}",
            )

    return executor


def _make_add_trust_root_executor(store):
    from src.capabilities.signature import CapabilityTrustRoot

    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            trust_root_id = str(args.get("trust_root_id", "")).strip()
            name = str(args.get("name", "")).strip()
            key_type = str(args.get("key_type", "")).strip()
            public_key_fingerprint = str(args.get("public_key_fingerprint", "")).strip()

            if not all([trust_root_id, name, key_type, public_key_fingerprint]):
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "trust_root_id, name, key_type, and public_key_fingerprint are required"},
                    reason="add_capability_trust_root missing required fields",
                )

            owner = args.get("owner")
            scope = args.get("scope")
            expires_at = args.get("expires_at")
            metadata = args.get("metadata", {})

            trust_root = CapabilityTrustRoot(
                trust_root_id=trust_root_id,
                name=name,
                key_type=key_type,
                public_key_fingerprint=public_key_fingerprint,
                owner=str(owner) if owner else None,
                scope=str(scope) if scope else None,
                status="active",
                created_at="",
                expires_at=str(expires_at) if expires_at else None,
                metadata=metadata if isinstance(metadata, dict) else {},
            )

            created = store.create_trust_root(trust_root)
            return ToolExecutionResult(
                success=True,
                payload=_trust_root_compact_summary(created, store),
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("add_capability_trust_root failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "add_trust_root_failed", "detail": str(e)},
                reason=f"add_capability_trust_root failed: {e}",
            )

    return executor


def _make_disable_trust_root_executor(store):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            trust_root_id = str(args.get("trust_root_id", "")).strip()
            reason = args.get("reason")

            root = store.get_trust_root(trust_root_id)
            if root is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "detail": f"Trust root {trust_root_id!r} not found"},
                    reason=f"Trust root {trust_root_id!r} not found",
                )

            # Already revoked -> stays revoked (documented rule)
            if root.status == "revoked":
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "already_revoked", "detail": f"Trust root {trust_root_id!r} is revoked; cannot be disabled"},
                    reason=f"Trust root {trust_root_id!r} is already revoked",
                )

            updated = store.disable_trust_root(trust_root_id, reason=str(reason) if reason else None)
            return ToolExecutionResult(
                success=True,
                payload=_trust_root_compact_summary(updated, store),
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("disable_capability_trust_root failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "disable_trust_root_failed", "detail": str(e)},
                reason=f"disable_capability_trust_root failed: {e}",
            )

    return executor


def _make_revoke_trust_root_executor(store):
    async def executor(
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            args = request.arguments
            trust_root_id = str(args.get("trust_root_id", "")).strip()
            reason = str(args.get("reason", "")).strip()

            if not reason:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "reason is required for revocation"},
                    reason="revoke_capability_trust_root requires a reason",
                )

            root = store.get_trust_root(trust_root_id)
            if root is None:
                return ToolExecutionResult(
                    success=False,
                    payload={"error": "not_found", "detail": f"Trust root {trust_root_id!r} not found"},
                    reason=f"Trust root {trust_root_id!r} not found",
                )

            updated = store.revoke_trust_root(trust_root_id, reason)
            return ToolExecutionResult(
                success=True,
                payload=_trust_root_compact_summary(updated, store),
            )
        except ValueError as e:
            return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))
        except Exception as e:
            logger.debug("revoke_capability_trust_root failed", exc_info=True)
            return ToolExecutionResult(
                success=False,
                payload={"error": "revoke_trust_root_failed", "detail": str(e)},
                reason=f"revoke_capability_trust_root failed: {e}",
            )

    return executor


# ── Phase 8B-3: Trust root operator tool registration ────────────────


def register_capability_trust_root_tools(
    tool_registry,
    trust_root_store,
) -> None:
    """Register Phase 8B-3 trust root operator tools.

    Five tools: list_capability_trust_roots, view_capability_trust_root,
    add_capability_trust_root, disable_capability_trust_root,
    revoke_capability_trust_root.

    All use capability_trust_operator tag — operator-only, not granted
    to standard/default/chat/local_execution/browser/identity/
    import/lifecycle/curator/candidate profiles.
    """
    if trust_root_store is None:
        logger.warning("register_capability_trust_root_tools called with trust_root_store=None, skipping")
        return

    tool_registry.register(ToolSpec(
        name="list_capability_trust_roots",
        description=(
            "列出本地信任根配置。返回紧凑摘要，可按 status/scope 过滤。"
            "可通过 include_expired 排除已过期的信任根。"
            "永远不会返回私钥字段或密钥材料。"
            "需要 capability_trust_operator 权限。"
        ),
        json_schema=LIST_TRUST_ROOTS_SCHEMA,
        executor=_make_list_trust_roots_executor(trust_root_store),
        capability="capability_trust_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="view_capability_trust_root",
        description=(
            "查看单个信任根的完整元数据。返回除私钥字段外的所有字段。"
            "包含 is_active 状态（考虑 status 和 expires_at）。"
            "需要 capability_trust_operator 权限。"
        ),
        json_schema=VIEW_TRUST_ROOT_SCHEMA,
        executor=_make_view_trust_root_executor(trust_root_store),
        capability="capability_trust_operator",
        risk_level="low",
    ))

    tool_registry.register(ToolSpec(
        name="add_capability_trust_root",
        description=(
            "向本地信任根库添加新的信任根。创建元数据条目（status=active），"
            "拒绝私钥/密钥材料、路径遍历 ID 和重复 ID。"
            "不执行任何加密验证，不访问网络，不修改任何能力出处或信任状态。"
            "需要 capability_trust_operator 权限。"
        ),
        json_schema=ADD_TRUST_ROOT_SCHEMA,
        executor=_make_add_trust_root_executor(trust_root_store),
        capability="capability_trust_operator",
        risk_level="medium",
    ))

    tool_registry.register(ToolSpec(
        name="disable_capability_trust_root",
        description=(
            "禁用指定的信任根（status → disabled）。已撤销的信任根保持撤销状态。"
            "仅写入 status 元数据，不删除文件，不修改能力出处或信任状态。"
            "需要 capability_trust_operator 权限。"
        ),
        json_schema=DISABLE_TRUST_ROOT_SCHEMA,
        executor=_make_disable_trust_root_executor(trust_root_store),
        capability="capability_trust_operator",
        risk_level="medium",
    ))

    tool_registry.register(ToolSpec(
        name="revoke_capability_trust_root",
        description=(
            "撤销指定的信任根（status → revoked）。需要提供撤销原因。"
            "仅写入 status 元数据，不删除文件，不修改能力出处或信任状态。"
            "需要 capability_trust_operator 权限。"
        ),
        json_schema=REVOKE_TRUST_ROOT_SCHEMA,
        executor=_make_revoke_trust_root_executor(trust_root_store),
        capability="capability_trust_operator",
        risk_level="high",
    ))

    logger.info("Phase 8B-3 trust root operator tools registered (list/view/add/disable/revoke)")
