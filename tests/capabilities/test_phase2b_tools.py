"""Phase 2B tests: Read-only capability tools (list_capabilities, search_capability, view_capability).

Feature-gated behind capabilities.enabled. No mutation, no execution, no automatic retrieval.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.capabilities.document import CapabilityDocument
from src.capabilities.index import CapabilityIndex
from src.capabilities.schema import CapabilityScope, CapabilityStatus
from src.capabilities.store import CapabilityStore
from src.tools.capability_tools import (
    LIST_CAPABILITIES_SCHEMA,
    SEARCH_CAPABILITY_SCHEMA,
    VIEW_CAPABILITY_SCHEMA,
    _compact_summary,
    _list_files,
    _make_list_capabilities_executor,
    _make_search_capability_executor,
    _make_view_capability_executor,
    _validate_enum,
    register_capability_tools,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


# ── Helpers ──────────────────────────────────────────────────────────

def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_doc(store, scope=CapabilityScope.WORKSPACE, **overrides) -> CapabilityDocument:
    return store.create_draft(
        scope=scope,
        name=overrides.pop("name", "Test Capability"),
        description=overrides.pop("description", "A test capability."),
        **overrides,
    )


def _make_ctx(**overrides) -> ToolExecutionContext:
    defaults = {
        "execute_shell": AsyncMock(),
        "shell_default_cwd": "/tmp",
    }
    defaults.update(overrides)
    return ToolExecutionContext(**defaults)


def _make_req(**args) -> ToolExecutionRequest:
    return ToolExecutionRequest(name="test", arguments=args)


async def _exec(executor_factory, store, index=None, **args):
    exec_fn = executor_factory(store, index)
    return await exec_fn(_make_req(**args), _make_ctx())


# ── Feature gate / registration tests ───────────────────────────────

class TestFeatureGate:
    def test_tools_not_registered_when_store_is_none(self):
        registry = MagicMock()
        register_capability_tools(registry, store=None)
        registry.register.assert_not_called()

    def test_read_only_tools_registered(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        registry = MagicMock()
        register_capability_tools(registry, store, store._index)
        assert registry.register.call_count == 4
        names = {c[0][0].name for c in registry.register.call_args_list}
        assert names == {
            "list_capabilities",
            "search_capability",
            "view_capability",
            "load_capability",
        }

    def test_no_mutation_tools_registered(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        registry = MagicMock()
        register_capability_tools(registry, store, store._index)
        names = {c[0][0].name for c in registry.register.call_args_list}
        forbidden = {"create_capability", "disable_capability", "archive_capability",
                      "promote_capability", "run_capability", "edit_capability",
                      "delete_capability", "execute_capability"}
        assert names.isdisjoint(forbidden)

    def test_all_tools_use_capability_read_tag(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        registry = MagicMock()
        register_capability_tools(registry, store, store._index)
        for c in registry.register.call_args_list:
            assert c[0][0].capability == "capability_read"

    def test_all_tools_low_risk(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        registry = MagicMock()
        register_capability_tools(registry, store, store._index)
        for c in registry.register.call_args_list:
            assert c[0][0].risk_level == "low"


# ── Schema validation tests ─────────────────────────────────────────

class TestSchemas:
    def test_list_schema_disallows_write_params(self):
        props = LIST_CAPABILITIES_SCHEMA["properties"]
        forbidden = {"body", "code", "script", "cap_id", "name", "description"}
        assert forbidden.isdisjoint(set(props.keys()))

    def test_search_schema_disallows_write_params(self):
        props = SEARCH_CAPABILITY_SCHEMA["properties"]
        forbidden = {"body", "code", "script", "cap_id", "name", "description"}
        assert forbidden.isdisjoint(set(props.keys()))

    def test_view_schema_requires_id(self):
        assert "id" in VIEW_CAPABILITY_SCHEMA.get("required", [])

    def test_view_schema_disallows_write_params(self):
        props = VIEW_CAPABILITY_SCHEMA["properties"]
        forbidden = {"body_field", "code", "script", "new_name", "new_description"}
        assert forbidden.isdisjoint(set(props.keys()))


# ── _validate_enum tests ────────────────────────────────────────────

class TestValidateEnum:
    def test_valid_value(self):
        assert _validate_enum("skill", {"skill", "workflow"}, "type") == "skill"

    def test_none_value(self):
        assert _validate_enum(None, {"skill"}, "type") is None

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            _validate_enum("invalid", {"skill", "workflow"}, "type")


# ── list_capabilities tests ─────────────────────────────────────────

class TestListCapabilities:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="a", name="Alpha", tags=["python"], type="skill")
        _create_doc(s, cap_id="b", name="Beta", type="workflow")
        _create_doc(s, cap_id="c", name="Gamma", scope=CapabilityScope.GLOBAL,
                    tags=["rust"], risk_level="high")
        return s

    @pytest.mark.asyncio
    async def test_lists_active_capabilities(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index)
        assert result.success
        assert result.payload["count"] == 3

    @pytest.mark.asyncio
    async def test_compact_summary_fields(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index)
        cap = result.payload["capabilities"][0]
        assert "id" in cap
        assert "name" in cap
        assert "description" in cap
        assert "type" in cap
        assert "scope" in cap
        assert "maturity" in cap
        assert "status" in cap
        assert "risk_level" in cap
        assert "tags" in cap
        assert "triggers" in cap
        assert "updated_at" in cap
        assert "body" not in cap
        assert "scripts" not in cap
        assert "files" not in cap

    @pytest.mark.asyncio
    async def test_excludes_disabled_by_default(self, store):
        store.disable("a", CapabilityScope.WORKSPACE)
        result = await _exec(_make_list_capabilities_executor, store, store._index)
        ids = {c["id"] for c in result.payload["capabilities"]}
        assert "a" not in ids

    @pytest.mark.asyncio
    async def test_includes_disabled_with_flag(self, store):
        store.disable("a", CapabilityScope.WORKSPACE)
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            include_disabled=True)
        ids = {c["id"] for c in result.payload["capabilities"]}
        assert "a" in ids

    @pytest.mark.asyncio
    async def test_excludes_archived_by_default(self, store):
        store.archive("a", CapabilityScope.WORKSPACE)
        result = await _exec(_make_list_capabilities_executor, store, store._index)
        ids = {c["id"] for c in result.payload["capabilities"]}
        assert "a" not in ids

    @pytest.mark.asyncio
    async def test_includes_archived_with_flag(self, store):
        store.archive("b", CapabilityScope.WORKSPACE)
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            include_archived=True)
        ids = {c["id"] for c in result.payload["capabilities"]}
        assert "b" in ids

    @pytest.mark.asyncio
    async def test_filters_by_scope(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            scope="global")
        assert result.payload["count"] == 1
        assert result.payload["capabilities"][0]["id"] == "c"

    @pytest.mark.asyncio
    async def test_filters_by_type(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            type="workflow")
        assert result.payload["count"] == 1
        assert result.payload["capabilities"][0]["id"] == "b"

    @pytest.mark.asyncio
    async def test_filters_by_maturity(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            maturity="draft")
        assert result.payload["count"] == 3

    @pytest.mark.asyncio
    async def test_filters_by_tags(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            tags=["python"])
        assert result.payload["count"] == 1
        assert result.payload["capabilities"][0]["id"] == "a"

    @pytest.mark.asyncio
    async def test_limit_enforced(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index, limit=1)
        assert len(result.payload["capabilities"]) == 1

    @pytest.mark.asyncio
    async def test_limit_capped_at_100(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index, limit=200)
        assert len(result.payload["capabilities"]) <= 100

    @pytest.mark.asyncio
    async def test_invalid_scope_returns_error(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            scope="invalid_scope")
        assert not result.success
        assert "error" in result.payload

    @pytest.mark.asyncio
    async def test_invalid_type_returns_error(self, store):
        result = await _exec(_make_list_capabilities_executor, store, store._index,
                            type="invalid_type")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_store(self, tmp_path):
        store = _make_store(tmp_path)
        result = await _exec(_make_list_capabilities_executor, store)
        assert result.success
        assert result.payload["capabilities"] == []
        assert result.payload["count"] == 0

    @pytest.mark.asyncio
    async def test_works_without_index(self, tmp_path):
        store = _make_store(tmp_path)
        _create_doc(store, cap_id="no_idx")
        result = await _exec(_make_list_capabilities_executor, store)
        assert result.success
        assert result.payload["count"] == 1


# ── _compact_summary helper tests ───────────────────────────────────

class TestCompactSummary:
    def test_fields_match_spec(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_doc(store, cap_id="cs_test", name="CS Test",
                          triggers=["on_push"], tags=["a", "b"])
        summary = _compact_summary(doc)
        expected_keys = {"id", "name", "description", "type", "scope", "maturity",
                         "status", "risk_level", "tags", "triggers",
                         "do_not_apply_when", "sensitive_contexts", "updated_at"}
        assert set(summary.keys()) == expected_keys
        assert summary["id"] == "cs_test"
        assert summary["name"] == "CS Test"
        assert summary["triggers"] == ["on_push"]
        assert summary["tags"] == ["a", "b"]


# ── search_capability tests ─────────────────────────────────────────

class TestSearchCapability:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="http_client", name="Python HTTP Client",
                    description="Makes HTTP requests", tags=["python", "web"],
                    triggers=["on_http"], required_tools=["execute_shell"])
        _create_doc(s, cap_id="cli_tool", name="Rust CLI Tool",
                    description="Command-line utility", tags=["rust", "cli"],
                    triggers=["on_cli"], required_tools=["read_file"])
        _create_doc(s, cap_id="data_tool", name="Python Data Tool",
                    description="Analyzes data", tags=["python", "data"],
                    required_tools=["execute_shell"])
        _create_doc(s, cap_id="dupe", scope=CapabilityScope.GLOBAL,
                    name="Global Dupe", tags=["global_only"])
        _create_doc(s, cap_id="dupe", scope=CapabilityScope.WORKSPACE,
                    name="WS Dupe", tags=["ws_only"])
        return s

    @pytest.mark.asyncio
    async def test_searches_by_name(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="python")
        assert result.success
        ids = {r["id"] for r in result.payload["results"]}
        assert ids == {"http_client", "data_tool"}

    @pytest.mark.asyncio
    async def test_searches_by_description(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="HTTP")
        ids = {r["id"] for r in result.payload["results"]}
        assert "http_client" in ids

    @pytest.mark.asyncio
    async def test_searches_by_trigger(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="on_cli")
        ids = {r["id"] for r in result.payload["results"]}
        assert ids == {"cli_tool"}

    @pytest.mark.asyncio
    async def test_searches_by_tag(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="rust")
        ids = {r["id"] for r in result.payload["results"]}
        assert ids == {"cli_tool"}

    @pytest.mark.asyncio
    async def test_filters_by_required_tools(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            required_tools=["execute_shell"])
        ids = {r["id"] for r in result.payload["results"]}
        assert ids == {"http_client", "data_tool"}

    @pytest.mark.asyncio
    async def test_respects_scope_precedence(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="dupe")
        assert result.success
        assert result.payload["count"] == 1
        assert result.payload["results"][0]["name"] == "WS Dupe"
        assert result.payload["results"][0]["scope"] == "workspace"

    @pytest.mark.asyncio
    async def test_include_all_scopes_returns_duplicates(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="dupe", include_all_scopes=True)
        assert result.success
        assert result.payload["count"] == 2
        scopes = {r["scope"] for r in result.payload["results"]}
        assert scopes == {"workspace", "global"}

    @pytest.mark.asyncio
    async def test_excludes_disabled_by_default(self, store):
        store.disable("http_client", CapabilityScope.WORKSPACE)
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="python")
        ids = {r["id"] for r in result.payload["results"]}
        assert "http_client" not in ids

    @pytest.mark.asyncio
    async def test_excludes_archived_by_default(self, store):
        store.archive("http_client", CapabilityScope.WORKSPACE)
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="python")
        ids = {r["id"] for r in result.payload["results"]}
        assert "http_client" not in ids

    @pytest.mark.asyncio
    async def test_limit_enforced(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index, limit=1)
        assert len(result.payload["results"]) == 1

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="nonexistent_xyz")
        assert result.success
        assert result.payload["results"] == []

    @pytest.mark.asyncio
    async def test_search_result_fields(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            query="http_client")
        r = result.payload["results"][0]
        expected = {"id", "name", "description", "type", "scope", "maturity",
                    "status", "risk_level", "trust_required", "triggers", "tags",
                    "required_tools", "do_not_apply_when", "sensitive_contexts",
                    "updated_at"}
        assert set(r.keys()) == expected
        assert r["id"] == "http_client"
        assert "body" not in r

    @pytest.mark.asyncio
    async def test_filters_by_scope(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            scope="global")
        ids = {r["id"] for r in result.payload["results"]}
        assert "dupe" in ids

    @pytest.mark.asyncio
    async def test_filters_by_maturity(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            maturity="draft")
        assert result.payload["count"] >= 3

    @pytest.mark.asyncio
    async def test_works_without_index(self, tmp_path):
        store = _make_store(tmp_path)
        _create_doc(store, cap_id="s1", name="Search One")
        result = await _exec(_make_search_capability_executor, store, query="search")
        assert result.success
        assert result.payload["count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_scope_returns_error(self, store):
        result = await _exec(_make_search_capability_executor, store, store._index,
                            scope="bogus")
        assert not result.success


# ── view_capability tests ───────────────────────────────────────────

class TestViewCapability:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="v1", name="View One",
                    description="First viewable capability.",
                    body="# Hello\n\nWorld.", tags=["demo"],
                    triggers=["on_view"])
        _create_doc(s, cap_id="v2", scope=CapabilityScope.GLOBAL,
                    name="Global View", description="Global scoped.")
        _create_doc(s, cap_id="dupe_view", scope=CapabilityScope.GLOBAL,
                    name="Global Dupe View")
        _create_doc(s, cap_id="dupe_view", scope=CapabilityScope.WORKSPACE,
                    name="WS Dupe View")
        return s

    @pytest.mark.asyncio
    async def test_views_by_explicit_scope(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v1", scope="workspace")
        assert result.success
        assert result.payload["id"] == "v1"
        assert result.payload["name"] == "View One"

    @pytest.mark.asyncio
    async def test_views_by_omitted_scope_uses_precedence(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="dupe_view")
        assert result.success
        assert result.payload["name"] == "WS Dupe View"
        assert result.payload["scope"] == "workspace"

    @pytest.mark.asyncio
    async def test_not_found_for_missing_id(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="nonexistent")
        assert not result.success
        assert result.payload["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_archived_not_returned_by_default(self, store):
        store.archive("v1", CapabilityScope.WORKSPACE)
        result = await _exec(_make_view_capability_executor, store, store._index, id="v1")
        assert not result.success
        assert "archived" in result.payload.get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_archived_returned_with_flag(self, store):
        store.archive("v1", CapabilityScope.WORKSPACE)
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v1", include_archived=True)
        assert result.success
        assert result.payload["id"] == "v1"

    @pytest.mark.asyncio
    async def test_returns_metadata_fields(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index, id="v1")
        assert result.success
        for field in ("id", "name", "description", "type", "scope", "version",
                      "maturity", "status", "risk_level", "trust_required",
                      "required_tools", "required_permissions", "triggers", "tags",
                      "created_at", "updated_at", "content_hash"):
            assert field in result.payload, f"Missing metadata field: {field}"

    @pytest.mark.asyncio
    async def test_returns_body_by_default(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index, id="v1")
        assert result.success
        assert "# Hello" in result.payload["body"]

    @pytest.mark.asyncio
    async def test_suppresses_body_when_include_body_false(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v1", include_body=False)
        assert result.success
        assert "body" not in result.payload

    @pytest.mark.asyncio
    async def test_returns_file_listings_by_default(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index, id="v1")
        assert result.success
        assert "files" in result.payload
        for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
            assert sub in result.payload["files"]

    @pytest.mark.asyncio
    async def test_suppresses_files_when_include_files_false(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v1", include_files=False)
        assert result.success
        assert "files" not in result.payload

    @pytest.mark.asyncio
    async def test_file_listings_are_names_only(self, store):
        doc = _create_doc(store, cap_id="file_test", name="File Test")
        scripts_dir = doc.directory / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "setup.sh").write_text("#!/bin/bash\necho hi")
        (scripts_dir / "teardown.sh").write_text("#!/bin/bash\necho bye")

        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="file_test")
        assert result.success
        assert set(result.payload["files"]["scripts"]) == {"setup.sh", "teardown.sh"}
        assert "#!/bin/bash" not in str(result.payload["files"]["scripts"])

    @pytest.mark.asyncio
    async def test_does_not_execute_scripts(self, store):
        doc = _create_doc(store, cap_id="no_exec", name="No Exec")
        scripts_dir = doc.directory / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "dangerous.py").write_text("import os; os.system('rm -rf /')")

        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="no_exec")
        assert result.success
        assert "dangerous.py" in result.payload["files"]["scripts"]

    @pytest.mark.asyncio
    async def test_missing_id_returns_error(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index)
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_id_returns_error(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index, id="")
        assert not result.success

    @pytest.mark.asyncio
    async def test_views_global_scoped_by_precedence_fallback(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v2")
        assert result.success
        assert result.payload["scope"] == "global"

    @pytest.mark.asyncio
    async def test_invalid_scope_returns_error(self, store):
        result = await _exec(_make_view_capability_executor, store, store._index,
                            id="v1", scope="bogus")
        assert not result.success


# ── _list_files helper tests ────────────────────────────────────────

class TestListFiles:
    def test_empty_dirs(self, tmp_path):
        cap_dir = tmp_path / "test_cap"
        cap_dir.mkdir()
        for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
            (cap_dir / sub).mkdir()
        result = _list_files(cap_dir)
        for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
            assert result[sub] == []

    def test_with_files(self, tmp_path):
        cap_dir = tmp_path / "test_cap"
        cap_dir.mkdir()
        (cap_dir / "scripts").mkdir()
        (cap_dir / "scripts" / "run.sh").write_text("echo hi")
        (cap_dir / "tests").mkdir()
        result = _list_files(cap_dir)
        assert result["scripts"] == ["run.sh"]
        assert result["tests"] == []

    def test_ignores_gitkeep(self, tmp_path):
        cap_dir = tmp_path / "test_cap"
        cap_dir.mkdir()
        (cap_dir / "scripts").mkdir()
        (cap_dir / "scripts" / ".gitkeep").write_text("")
        (cap_dir / "scripts" / "real.sh").write_text("echo real")
        result = _list_files(cap_dir)
        assert result["scripts"] == ["real.sh"]


# ── Body-as-data safety tests ───────────────────────────────────────

class TestBodyAsData:
    @pytest.mark.asyncio
    async def test_body_is_returned_as_payload_not_instructions(self, tmp_path):
        store = _make_store(tmp_path)
        _create_doc(store, cap_id="safe",
                    body="This is document content, not instructions.")
        result = await _exec(_make_view_capability_executor, store, id="safe")
        assert result.success
        assert "body" in result.payload
        assert "instructions" not in result.payload
        assert "system" not in result.payload

    @pytest.mark.asyncio
    async def test_body_with_yaml_frontmatter_is_treated_as_data(self, tmp_path):
        store = _make_store(tmp_path)
        body = "---\nmalicious: true\n---\n\nContent."
        _create_doc(store, cap_id="yaml_body", body=body)
        result = await _exec(_make_view_capability_executor, store, id="yaml_body")
        assert result.success
        assert "malicious" in result.payload["body"]

    @pytest.mark.asyncio
    async def test_list_does_not_return_body(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        _create_doc(store, cap_id="no_body_leak",
                    body="# Secret instructions\ndef run(): pass")
        result = await _exec(_make_list_capabilities_executor, store, store._index)
        assert result.success
        for cap in result.payload["capabilities"]:
            assert "body" not in cap


# ── Error propagation tests ─────────────────────────────────────────

class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_store_unavailable_returns_clean_error_list(self, tmp_path):
        bad_store = MagicMock()
        bad_store.list.side_effect = RuntimeError("disk full")
        result = await _exec(_make_list_capabilities_executor, bad_store)
        assert not result.success
        assert result.payload["error"] == "capability_store_unavailable"
        assert "Traceback" not in str(result.payload)

    @pytest.mark.asyncio
    async def test_store_unavailable_returns_clean_error_search(self, tmp_path):
        bad_store = MagicMock()
        bad_store.search.side_effect = RuntimeError("disk full")
        result = await _exec(_make_search_capability_executor, bad_store, query="test")
        assert not result.success
        assert result.payload["error"] == "capability_store_unavailable"

    @pytest.mark.asyncio
    async def test_store_unavailable_returns_clean_error_view(self, tmp_path):
        bad_store = MagicMock()
        bad_store.get.side_effect = RuntimeError("disk full")
        result = await _exec(_make_view_capability_executor, bad_store, id="test")
        assert not result.success
        assert result.payload["error"] == "capability_store_unavailable"


# ── Regression: Phase 0/1 and Phase 2A compatibility ────────────────

class TestPhase2BRegression:
    def test_capability_store_still_works_directly(self, tmp_path):
        store = _make_store(tmp_path)
        doc = _create_doc(store, cap_id="reg_test")
        assert doc.id == "reg_test"
        retrieved = store.get("reg_test", CapabilityScope.WORKSPACE)
        assert retrieved.name == "Test Capability"

    def test_capability_index_still_works_directly(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        _create_doc(store, cap_id="idx_test")
        row = store._index.get("idx_test", "workspace")
        assert row is not None
        assert row["name"] == "Test Capability"
