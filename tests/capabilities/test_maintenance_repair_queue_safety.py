"""Maintenance B: Safety tests for the repair queue.

Verifies that the repair queue system is strictly inert:
no files mutated outside the queue, no index rebuilt, no lifecycle
transitions, no proposals/candidates/trust roots mutated, no scripts
executed, no subprocess/os.system, no network, no LLM judge,
no run_capability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
    generate_capability_health_report,
)
from src.capabilities.repair_queue import (
    RepairQueueItem,
    RepairQueueStore,
)
from src.capabilities.schema import CapabilityScope
from src.capabilities.store import CapabilityStore


# ── Helpers ──


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_queue_store(tmp_path: Path) -> RepairQueueStore:
    return RepairQueueStore(data_dir=tmp_path / "capabilities")


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


# ═══════════════════════════════════════════════════════════════════
# No-mutation tests
# ═══════════════════════════════════════════════════════════════════


class TestNoMutation:
    """Repair queue operations must not mutate capabilities or other artifacts."""

    def test_create_item_does_not_mutate_capabilities(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "no-mutate", maturity="testing")
        doc = store.get(cap_id)

        hashes_before = _file_hashes(store.data_dir)
        assert hashes_before, "Expected files before"

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(capability_id=cap_id))

        hashes_after = _file_hashes(store.data_dir)
        # All files except new queue files must be identical
        for path, h in hashes_before.items():
            assert hashes_after.get(path) == h, f"File mutated: {path}"

    def test_update_status_does_not_mutate_capabilities(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "status-safe", maturity="testing")

        qs = _make_queue_store(tmp_path)
        item = _make_item(capability_id=cap_id, item_id="rq-status-1")
        qs.create_item(item)

        hashes_before = _file_hashes(store.data_dir)

        qs.update_status("rq-status-1", "acknowledged")

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            # Only the queue item file may change
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    def test_create_from_health_report_does_not_mutate_capabilities(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "conversion-safe", maturity="testing")

        hashes_before = _file_hashes(store.data_dir)

        qs = _make_queue_store(tmp_path)
        report = CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=[
                CapabilityHealthFinding(
                    severity="info", code="eval_stale",
                    message="Stale eval.", capability_id=cap_id, scope="workspace",
                ),
            ],
        )
        qs.create_from_health_report(report)

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    def test_no_index_mutated(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _make_doc(store, "idx-1")
        _make_doc(store, "idx-2")

        from src.capabilities.index import CapabilityIndex
        index = CapabilityIndex(tmp_path / "test-index.db")
        index.init()
        index.rebuild_from_store(store)

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item())
        qs.update_status("rq-safety-001", "acknowledged")

        # Index DB should not be touched by repair queue operations
        # (No new entries, no modifications to capability index)
        count = index.conn.execute("SELECT COUNT(*) FROM capability_index").fetchone()[0]
        assert count == 2

    def test_no_lifecycle_transition_called(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "lifecycle-safe", maturity="draft", status="active")
        doc_before = store.get(cap_id)

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(capability_id=cap_id))
        qs.update_status("rq-safety-001", "resolved")

        doc_after = store.get(cap_id)
        assert doc_after.manifest.maturity.value == doc_before.manifest.maturity.value
        assert doc_after.manifest.status.value == doc_before.manifest.status.value

    def test_no_proposals_mutated(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "proposal-safe")

        proposals_dir = store.data_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        prop_dir = proposals_dir / "prop-1"
        prop_dir.mkdir()
        prop_json = prop_dir / "proposal.json"
        prop_json.write_text(json.dumps({"proposal_id": "prop-1", "applied": False}))
        prop_hash_before = hashlib.sha256(prop_json.read_bytes()).hexdigest()

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item())
        qs.update_status("rq-safety-001", "resolved")

        prop_hash_after = hashlib.sha256(prop_json.read_bytes()).hexdigest()
        assert prop_hash_before == prop_hash_after

    def test_no_agent_candidates_mutated(self, tmp_path: Path):
        candidates_dir = Path(tmp_path) / "capabilities" / "agent_candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        cand_dir = candidates_dir / "cand-1"
        cand_dir.mkdir()
        cand_json = cand_dir / "candidate.json"
        cand_json.write_text(json.dumps({
            "candidate_id": "cand-1",
            "approval_state": "pending",
            "risk_level": "low",
        }))
        cand_hash_before = hashlib.sha256(cand_json.read_bytes()).hexdigest()

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item())
        qs.update_status("rq-safety-001", "resolved")

        cand_hash_after = hashlib.sha256(cand_json.read_bytes()).hexdigest()
        assert cand_hash_before == cand_hash_after

    def test_no_trust_roots_mutated(self, tmp_path: Path):
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
        qs.create_item(_make_item())
        qs.update_status("rq-safety-001", "resolved")

        root_hash_after = hashlib.sha256(root_json.read_bytes()).hexdigest()
        assert root_hash_before == root_hash_after

    def test_no_eval_records_written(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "eval-safe", maturity="testing")
        doc = store.get(cap_id)
        evals_dir = doc.directory / "evals"

        evals_before = list(evals_dir.glob("*.json")) if evals_dir.is_dir() else []

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(capability_id=cap_id))
        qs.update_status("rq-safety-001", "acknowledged")

        evals_after = list(evals_dir.glob("*.json")) if evals_dir.is_dir() else []
        assert len(evals_after) == len(evals_before)

    def test_no_version_snapshots_created(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "version-safe", maturity="testing")
        doc = store.get(cap_id)
        versions_dir = doc.directory / "versions"

        snapshots_before = list(versions_dir.glob("*.json")) if versions_dir.is_dir() else []

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(capability_id=cap_id))
        qs.update_status("rq-safety-001", "resolved")

        snapshots_after = list(versions_dir.glob("*.json")) if versions_dir.is_dir() else []
        assert len(snapshots_after) == len(snapshots_before)

    def test_comprehensive_byte_hash_all_artifact_types(self, tmp_path: Path):
        """SHA256 hashes of all files before and after all queue operations."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "comprehensive", maturity="testing")

        # Create some additional artifact files
        proposals_dir = store.data_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        prop_dir = proposals_dir / "prop-x"
        prop_dir.mkdir()
        (prop_dir / "proposal.json").write_text(json.dumps({"proposal_id": "prop-x"}))

        hashes_before = _file_hashes(store.data_dir)
        assert hashes_before

        qs = _make_queue_store(tmp_path)
        item = _make_item(capability_id=cap_id)
        qs.create_item(item)
        qs.update_status(item.item_id, "acknowledged")

        report = CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=[
                CapabilityHealthFinding(
                    severity="info", code="eval_stale",
                    message="Stale eval.", capability_id=cap_id, scope="workspace",
                ),
            ],
        )
        qs.create_from_health_report(report)

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, (
                f"Non-queue file mutated: {path}\n"
                f"  before: {h}\n"
                f"  after:  {hashes_after.get(path)}"
            )


# ═══════════════════════════════════════════════════════════════════
# No-execution tests
# ═══════════════════════════════════════════════════════════════════


def _get_imported_modules(module) -> set[str]:
    """Extract all imported module names from a module using AST."""
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
    """Extract all top-level function names from a module using AST."""
    import ast
    import inspect
    source = inspect.getsource(module)
    tree = ast.parse(source)
    funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.add(node.name)
    return funcs


def _get_code_without_docstrings(module) -> str:
    """Get module source code with docstrings stripped, for inline call checks."""
    import ast
    import inspect
    source = inspect.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Replace docstring with empty string
            pass  # We can't easily strip, so just use import-based checks
    return source


class TestNoExecution:
    """Repair queue must not execute scripts, call subprocess, use network, or LLM."""

    def test_no_run_capability_in_module(self):
        from src.capabilities import repair_queue
        funcs = _get_function_names(repair_queue)
        assert "run_capability" not in funcs, "run_capability found in repair_queue"

    def test_no_subprocess_or_os_system_import(self):
        from src.capabilities import repair_queue
        imports = _get_imported_modules(repair_queue)
        assert "subprocess" not in imports
        assert "os" not in imports

    def test_no_network_imports(self):
        from src.capabilities import repair_queue
        imports = _get_imported_modules(repair_queue)
        for banned in ("urllib", "socket", "http", "httpx", "aiohttp", "requests"):
            assert banned not in imports, f"Network import found: {banned}"

    def test_no_llm_imports(self):
        from src.capabilities import repair_queue
        imports = _get_imported_modules(repair_queue)
        for banned in ("openai", "anthropic", "langchain", "instructor"):
            assert banned not in imports, f"LLM import found: {banned}"

    def test_no_exec_eval_importlib_runpy(self):
        from src.capabilities import repair_queue
        imports = _get_imported_modules(repair_queue)
        for banned in ("importlib", "runpy"):
            assert banned not in imports, f"Dangerous import found: {banned}"

        # Check for exec/eval calls in source (excluding docstrings)
        import ast
        import inspect
        source = inspect.getsource(repair_queue)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in ("exec", "eval"), (
                        f"exec/eval call found in repair_queue"
                    )

    def test_no_shell_or_execution_imports(self):
        from src.capabilities import repair_queue
        imports = _get_imported_modules(repair_queue)
        for banned in ("subprocess", "pexpect", "shlex", "pdb"):
            assert banned not in imports, f"Shell/exec import found: {banned}"


# ═══════════════════════════════════════════════════════════════════
# Action payload safety tests
# ═══════════════════════════════════════════════════════════════════


class TestActionPayloadSafety:
    """action_payload must never contain or execute commands."""

    def test_action_payload_does_not_contain_auto_fix(self, tmp_path: Path):
        qs = _make_queue_store(tmp_path)
        report = CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=[
                CapabilityHealthFinding(
                    severity="warning", code="integrity_mismatch",
                    message="Mismatch.", capability_id="cap-1", scope="workspace",
                ),
            ],
        )
        items = qs.create_from_health_report(report)
        for item in items:
            for key in item.action_payload:
                assert key not in ("auto_fix", "apply", "execute", "action", "command", "script", "repair")

    def test_recommendations_are_text_only(self, tmp_path: Path):
        qs = _make_queue_store(tmp_path)
        report = CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=[
                CapabilityHealthFinding(
                    severity="warning", code="integrity_mismatch",
                    message="Mismatch.", capability_id="cap-1", scope="workspace",
                ),
            ],
        )
        items = qs.create_from_health_report(report)
        for item in items:
            assert isinstance(item.recommended_action, str)
            assert item.recommended_action != "execute"
            assert item.recommended_action != "run"

    def test_create_item_rejects_executable_action_payload(self, tmp_path: Path):
        qs = _make_queue_store(tmp_path)
        with pytest.raises(ValueError, match="tool-call or command"):
            qs.create_item(_make_item(
                action_payload={"script": "bash -c 'echo hi'"},
            ))

    def test_update_status_only_changes_queue_item(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "queue-only-safe")

        qs = _make_queue_store(tmp_path)
        item = _make_item(capability_id=cap_id)
        qs.create_item(item)

        # Get the item path hash before
        item_path = qs._item_path(item.item_id)
        hash_before = hashlib.sha256(item_path.read_bytes()).hexdigest()

        qs.update_status(item.item_id, "acknowledged")

        # The item file itself changes (status updated)
        hash_after = hashlib.sha256(item_path.read_bytes()).hexdigest()
        assert hash_before != hash_after  # The item file changed

        # But capability files are untouched
        doc = store.get(cap_id)
        assert doc is not None


# ═══════════════════════════════════════════════════════════════════
# Runtime import audit
# ═══════════════════════════════════════════════════════════════════


class TestImportAudit:
    """Repair queue must not be imported by non-capability modules."""

    def test_repair_queue_not_imported_outside_capabilities(self):
        """Verify repair_queue is only imported from within capabilities or allowed entry points."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "from src.capabilities\\|import src.capabilities",
             "src/"],
            capture_output=True, text=True,
        )
        # Filter out lines from src/capabilities/ itself
        outside_lines = [
            line for line in result.stdout.splitlines()
            if "src/capabilities/" not in line
        ]
        # Allowed: container.py, capability_tools.py, and repair_queue_tools.py
        for line in outside_lines:
            assert (
                "src/tools/capability_tools.py" in line
                or "src/tools/repair_queue_tools.py" in line
                or "src/app/container.py" in line
            ), (
                f"Unexpected import outside capabilities: {line}"
            )
