"""Maintenance C: Safety tests for repair queue operator tools.

Verifies hard constraints:
- No capability mutation
- No index rebuild
- No lifecycle transition
- No proposal/candidate/trust-root mutation
- No artifact deletion
- No script execution / subprocess / network / LLM
- Recommendations/action_payload not executed
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
)
from src.capabilities.repair_queue import RepairQueueItem, RepairQueueStore
from src.capabilities.schema import CapabilityScope
from src.capabilities.store import CapabilityStore
from src.tools.repair_queue_tools import register_repair_queue_tools
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


# ── Fake registry ─────────────────────────────────────────────────────────

class _FakeRegistry:
    def __init__(self):
        self._t: dict[str, object] = {}

    def register(self, spec):
        self._t[spec.name] = spec

    def get(self, name: str):
        return self._t.get(name)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_queue_store(tmp_path: Path) -> RepairQueueStore:
    return RepairQueueStore(data_dir=tmp_path / "capabilities")


def _make_item(**overrides) -> RepairQueueItem:
    defaults = {
        "item_id": "rq-safety-001",
        "created_at": "2026-05-05T10:00:00+00:00",
        "source": "health_report",
        "finding_code": "missing_provenance_legacy",
        "severity": "info",
        "status": "open",
        "title": "Safety test item",
        "description": "Safety test",
        "recommended_action": "add_provenance",
        "capability_id": "test-cap-1",
        "scope": "workspace",
    }
    defaults.update(overrides)
    return RepairQueueItem(**defaults)


def _make_doc(store: CapabilityStore, name: str, **kwargs) -> str:
    doc = store.create_draft(
        scope=kwargs.get("scope", CapabilityScope.WORKSPACE),
        name=name,
        description=f"Description for {name}.",
        type="skill",
    )
    cap_dir = doc.directory
    maturity = kwargs.get("maturity", "draft")
    status = kwargs.get("status", "active")
    if maturity != "draft" or status != "active":
        from src.capabilities.schema import CapabilityMaturity, CapabilityStatus
        new_manifest = doc.manifest.model_copy(update={
            "maturity": CapabilityMaturity(maturity),
            "status": CapabilityStatus(status),
        })
        doc.manifest = new_manifest
        store._sync_manifest_json(cap_dir, doc)
        store._parser.parse(cap_dir)
    return doc.id


def _file_hashes(data_dir: Path) -> dict[str, str]:
    """Compute SHA256 hashes of all files under a directory."""
    result = {}
    for fpath in sorted(data_dir.rglob("*")):
        if fpath.is_file():
            result[str(fpath)] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    return result


def _make_context():
    return ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp")


@pytest.fixture
def registry():
    return _FakeRegistry()


@pytest.fixture
def queue_store(tmp_path):
    return _make_queue_store(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# No-mutation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNoMutation:
    """Repair queue tool operations must not mutate capabilities or other artifacts."""

    async def test_create_from_health_writes_only_queue_items(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "safety-health", maturity="testing")
        doc = store.get(cap_id)

        hashes_before = _file_hashes(store.data_dir)
        assert hashes_before

        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_acknowledge_only_mutates_queue_item(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "safety-ack", maturity="testing")

        queue_store.create_item(_make_item(item_id="rq-sf-ack", capability_id=cap_id))
        hashes_before = _file_hashes(store.data_dir)

        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("acknowledge_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-ack"}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_resolve_only_mutates_queue_item(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "safety-res", maturity="testing")

        queue_store.create_item(_make_item(item_id="rq-sf-res", capability_id=cap_id))
        hashes_before = _file_hashes(store.data_dir)

        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("resolve_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-res"}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_dismiss_only_mutates_queue_item(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "safety-dis", maturity="testing")

        queue_store.create_item(_make_item(item_id="rq-sf-dis", capability_id=cap_id))
        hashes_before = _file_hashes(store.data_dir)

        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-dis"}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_no_index_rebuild(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "idx-1")
        _make_doc(store, "idx-2")

        from src.capabilities.index import CapabilityIndex
        index = CapabilityIndex(tmp_path / "test-index.db")
        index.init()
        index.rebuild_from_store(store)

        queue_store.create_item(_make_item(item_id="rq-sf-idx"))
        register_repair_queue_tools(registry, queue_store)

        # Run all mutation tools
        for tool_name in ("acknowledge_repair_queue_item", "resolve_repair_queue_item"):
            spec = registry.get(tool_name)
            await spec.executor(
                ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-idx"}),
                _make_context(),
            )

        count = index.conn.execute("SELECT COUNT(*) FROM capability_index").fetchone()[0]
        assert count == 2

    async def test_no_lifecycle_transition(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "lifecycle-safe", maturity="draft", status="active")
        doc_before = store.get(cap_id)

        queue_store.create_item(_make_item(item_id="rq-sf-life", capability_id=cap_id))
        register_repair_queue_tools(registry, queue_store)

        spec = registry.get("resolve_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-life"}),
            _make_context(),
        )

        doc_after = store.get(cap_id)
        assert doc_after.manifest.maturity.value == doc_before.manifest.maturity.value
        assert doc_after.manifest.status.value == doc_before.manifest.status.value

    async def test_no_proposals_mutated(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "proposal-safe")

        proposals_dir = store.data_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        prop_dir = proposals_dir / "prop-1"
        prop_dir.mkdir()
        prop_json = prop_dir / "proposal.json"
        prop_json.write_text(json.dumps({"proposal_id": "prop-1", "applied": False}))
        prop_hash_before = hashlib.sha256(prop_json.read_bytes()).hexdigest()

        queue_store.create_item(_make_item(item_id="rq-sf-prop", capability_id=cap_id))
        register_repair_queue_tools(registry, queue_store)

        spec = registry.get("resolve_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-prop"}),
            _make_context(),
        )

        prop_hash_after = hashlib.sha256(prop_json.read_bytes()).hexdigest()
        assert prop_hash_before == prop_hash_after

    async def test_no_agent_candidates_mutated(self, registry, tmp_path):
        candidates_dir = Path(tmp_path) / "capabilities" / "agent_candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        cand_dir = candidates_dir / "cand-1"
        cand_dir.mkdir()
        cand_json = cand_dir / "candidate.json"
        cand_json.write_text(json.dumps({
            "candidate_id": "cand-1",
            "approval_state": "pending",
        }))
        cand_hash_before = hashlib.sha256(cand_json.read_bytes()).hexdigest()

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-sf-cand"))
        register_repair_queue_tools(registry, qs)

        spec = registry.get("acknowledge_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-cand"}),
            _make_context(),
        )

        cand_hash_after = hashlib.sha256(cand_json.read_bytes()).hexdigest()
        assert cand_hash_before == cand_hash_after

    async def test_no_trust_roots_mutated(self, registry, tmp_path):
        roots_dir = Path(tmp_path) / "capabilities" / "trust_roots"
        roots_dir.mkdir(parents=True, exist_ok=True)
        root_json = roots_dir / "tr-1.json"
        root_json.write_text(json.dumps({
            "trust_root_id": "tr-1",
            "name": "test-root",
            "status": "active",
        }))
        root_hash_before = hashlib.sha256(root_json.read_bytes()).hexdigest()

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-sf-root"))
        register_repair_queue_tools(registry, qs)

        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-root"}),
            _make_context(),
        )

        root_hash_after = hashlib.sha256(root_json.read_bytes()).hexdigest()
        assert root_hash_before == root_hash_after

    async def test_no_artifact_deletion(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "delete-safe", maturity="testing")

        queue_store.create_item(_make_item(item_id="rq-sf-del", capability_id=cap_id))
        hashes_before = _file_hashes(store.data_dir)

        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-sf-del"}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        # No files should be missing
        for path in hashes_before:
            if "repair_queue" in path:
                continue
            assert path in hashes_after, f"File deleted: {path}"

    async def test_comprehensive_byte_hash_all_ops(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "comprehensive", maturity="testing")

        proposals_dir = store.data_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        prop_dir = proposals_dir / "prop-x"
        prop_dir.mkdir()
        (prop_dir / "proposal.json").write_text(json.dumps({"proposal_id": "prop-x"}))

        hashes_before = _file_hashes(store.data_dir)
        assert hashes_before

        queue_store.create_item(_make_item(item_id="rq-comp-1", capability_id=cap_id))
        queue_store.create_item(_make_item(item_id="rq-comp-2", capability_id=cap_id))

        register_repair_queue_tools(registry, queue_store, capability_store=store)

        # Run all ops
        for tool_name in ("acknowledge_repair_queue_item", "resolve_repair_queue_item"):
            spec = registry.get(tool_name)
            await spec.executor(
                ToolExecutionRequest(name="test", arguments={"item_id": "rq-comp-1"}),
                _make_context(),
            )

        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-comp-2"}),
            _make_context(),
        )

        # create_from_health
        spec = registry.get("create_repair_queue_from_health")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"


# ═══════════════════════════════════════════════════════════════════════════
# No-execution tests
# ═══════════════════════════════════════════════════════════════════════════

def _get_imported_modules(module) -> set[str]:
    import ast
    import inspect
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def _get_function_names(module) -> set[str]:
    import ast
    import inspect
    source = inspect.getsource(module)
    tree = ast.parse(source)
    funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.add(node.name)
    return funcs


class TestNoExecution:
    """Repair queue tools must not execute scripts, call subprocess, use network, or LLM."""

    def test_no_run_capability_in_tools_module(self):
        from src.tools import repair_queue_tools
        funcs = _get_function_names(repair_queue_tools)
        assert "run_capability" not in funcs

    def test_no_repair_capability_in_tools_module(self):
        from src.tools import repair_queue_tools
        funcs = _get_function_names(repair_queue_tools)
        for banned in ("repair_capability", "auto_repair_capability", "execute_repair",
                       "apply_repair_queue_item", "rebuild_index_from_health",
                       "promote_from_health"):
            assert banned not in funcs, f"Banned function found: {banned}"

    def test_no_subprocess_or_os_system_import(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        assert "subprocess" not in imports
        assert "os" not in imports

    def test_no_network_imports(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        for banned in ("urllib", "socket", "http", "httpx", "aiohttp", "requests"):
            assert banned not in imports, f"Network import found: {banned}"

    def test_no_llm_imports(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        for banned in ("openai", "anthropic", "langchain", "instructor"):
            assert banned not in imports, f"LLM import found: {banned}"

    def test_no_exec_eval_importlib_runpy(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        for banned in ("importlib", "runpy"):
            assert banned not in imports, f"Dangerous import found: {banned}"

        import ast
        import inspect
        source = inspect.getsource(repair_queue_tools)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in ("exec", "eval"), "exec/eval call found"

    def test_no_shell_or_execution_imports(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        for banned in ("subprocess", "pexpect", "shlex", "pdb"):
            assert banned not in imports, f"Shell/exec import found: {banned}"

    def test_tools_module_does_not_import_brain(self):
        from src.tools import repair_queue_tools
        imports = _get_imported_modules(repair_queue_tools)
        for banned in ("Brain", "TaskRuntime", "StateView"):
            assert banned not in imports, f"Runtime import found: {banned}"


# ═══════════════════════════════════════════════════════════════════════════
# Recommendation safety tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRecommendationsNotExecuted:
    """recommended_action and action_payload must be inert strings/data, never executed."""

    async def test_recommended_action_is_string_only(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "rec-safe")
        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        for item in result.payload.get("items", []):
            assert isinstance(item["recommended_action"], str)
            assert item["recommended_action"] not in ("execute", "run", "repair")

    async def test_action_payload_is_not_executed(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "payload-safe")
        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        # create_from_health only returns compact summaries (no action_payload)
        for item in result.payload.get("items", []):
            assert "action_payload" not in item
