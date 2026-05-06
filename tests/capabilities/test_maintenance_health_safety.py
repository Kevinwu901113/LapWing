"""Maintenance A: Safety tests for the health report.

Verifies that the health report system is strictly read-only:
no files mutated, no index rebuilt, no lifecycle transitions,
no proposals/candidates/trust roots mutated, no scripts executed,
no subprocess/os.system, no network, no LLM judge, no run_capability.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.capabilities.evaluator import CapabilityEvaluator, EvalRecord
from src.capabilities.health import (
    check_agent_candidate_backlog,
    check_index_drift,
    check_integrity_mismatch,
    check_missing_provenance,
    check_orphaned_artifacts,
    check_proposal_backlog,
    check_quarantine_backlog,
    check_stale_eval_records,
    check_stale_trust_roots,
    generate_capability_health_report,
)
from src.capabilities.eval_records import write_eval_record
from src.capabilities.evaluator import CapabilityEvaluator, EvalRecord
from src.capabilities.trace_summary import TraceSummary
from src.capabilities.index import CapabilityIndex
from src.capabilities.provenance import write_provenance
from src.capabilities.schema import CapabilityScope
from src.capabilities.store import CapabilityStore
from src.capabilities.trust_roots import TrustRootStore
from src.capabilities.signature import CapabilityTrustRoot


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


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


# ── File mutation safety ──


def test_no_files_mutated_by_generate_report(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "no-mutate", maturity="testing")
    doc = store.get(cap_id)

    # Record file hashes before
    def _file_hashes(directory: Path) -> dict[str, str]:
        import hashlib
        result = {}
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                result[str(fpath)] = hashlib.sha256(fpath.read_bytes()).hexdigest()
        return result

    hashes_before = _file_hashes(store.data_dir)

    generate_capability_health_report(store)

    hashes_after = _file_hashes(store.data_dir)
    # No new files, no modified files
    assert set(hashes_before.keys()) == set(hashes_after.keys())
    for path in hashes_before:
        assert hashes_before[path] == hashes_after[path], f"File mutated: {path}"


def test_no_index_rebuilt_by_health_report(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "index-cap")
    _make_doc(store, "index-cap-2")

    index_path = tmp_path / "index.db"
    index = CapabilityIndex(db_path=index_path)
    index.init()

    # Don't populate index - verify count stays 0
    assert index.count() == 0

    generate_capability_health_report(store, index=index)

    # Index should still be empty (no rebuild)
    assert index.count() == 0


def test_no_lifecycle_transition_called_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "lifecycle-cap", maturity="draft")
    doc_before = store.get(cap_id)
    assert doc_before.manifest.maturity.value == "draft"

    generate_capability_health_report(store)

    doc_after = store.get(cap_id)
    assert doc_after.manifest.maturity.value == "draft"
    assert doc_after.manifest.status.value == "active"


def test_no_proposals_created_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "prop-cap")
    proposals_dir = store.data_dir / "proposals"

    assert not proposals_dir.is_dir()

    generate_capability_health_report(store)

    # No proposals directory should have been created
    assert not proposals_dir.is_dir()


def test_no_agent_candidates_mutated_by_health(tmp_path: Path):
    from src.agents.candidate import AgentCandidate
    from src.agents.candidate_store import AgentCandidateStore

    cs = AgentCandidateStore(base_dir=tmp_path / "agent_candidates")
    cand = AgentCandidate(
        candidate_id="cand-safety-test",
        name="Safety Candidate",
        description="Test.",
        approval_state="pending",
    )
    cs.create_candidate(cand)

    store = _make_store(tmp_path)
    _make_doc(store, "cand-cap")

    # Record state before
    cand_before = cs.get_candidate("cand-safety-test")
    state_before = cand_before.approval_state

    generate_capability_health_report(store, candidate_store=cs)

    # No change
    cand_after = cs.get_candidate("cand-safety-test")
    assert cand_after.approval_state == state_before
    assert len(cand_after.eval_evidence) == len(cand_before.eval_evidence)


def test_no_trust_roots_mutated_by_health(tmp_path: Path):
    data_dir = tmp_path / "caps"
    trust_store = TrustRootStore(data_dir=data_dir)
    trust_store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="safety-root",
        name="Safety Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:abc123",
        owner="test",
        status="active",
    ))

    store = _make_store(tmp_path)
    _make_doc(store, "trust-cap")

    root_before = trust_store.get_trust_root("safety-root")
    assert root_before.status == "active"

    generate_capability_health_report(store, trust_root_store=trust_store)

    root_after = trust_store.get_trust_root("safety-root")
    assert root_after.status == "active"
    assert not root_after.metadata.get("revoked_reason")


# ── No execution safety ──


def test_no_run_capability_exists_in_health_module():
    """Verify health.py does not define run_capability."""
    import inspect
    from src.capabilities import health

    functions = [name for name, obj in inspect.getmembers(health, inspect.isfunction)
                 if not name.startswith("_")]
    assert "run_capability" not in functions


def test_health_does_not_import_subprocess_or_os_system():
    """Verify health.py does not import dangerous execution modules."""
    import ast
    import inspect
    from src.capabilities import health

    source = inspect.getsource(health)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in ("subprocess", "os", "socket", "urllib", "http.client", "requests"), \
                    f"health.py imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module not in ("subprocess", "os", "socket", "urllib", "http.client", "requests"), \
                    f"health.py imports from {node.module}"


# ── No network safety ──


def test_no_network_in_health_module():
    """Verify health.py imports no networking libraries and makes no network calls."""
    import ast
    import inspect
    from src.capabilities import health

    source = inspect.getsource(health)
    tree = ast.parse(source)

    # Check imports — no networking libraries
    network_modules = {"urllib", "http", "socket", "requests", "httpx", "aiohttp", "ssl", "ftplib"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = ""
            if isinstance(node, ast.Import):
                module_name = " ".join(a.name for a in node.names)
            elif node.module:
                module_name = node.module
            top_level = module_name.split(".")[0]
            assert top_level not in network_modules, \
                f"health.py imports networking module: {module_name}"


# ── No LLM judge ──


def test_no_llm_judge_in_health_module():
    """Verify health.py does not use LLM or AI inference."""
    import ast
    import inspect
    from src.capabilities import health

    source = inspect.getsource(health)
    tree = ast.parse(source)

    llm_patterns = {"llm", "openai", "anthropic", "claude", "chatgpt", "gpt", "model",
                    "prompt", "completion", "inference", "predict", "generate_text"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = ""
            if isinstance(node, ast.Import):
                module_name = " ".join(a.name for a in node.names)
            elif node.module:
                module_name = node.module
            for pattern in llm_patterns:
                assert pattern not in module_name.lower(), \
                    f"health.py imports LLM-related module: {module_name}"


# ── No mutation by individual check functions ──


def test_check_functions_do_not_mutate_files(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "check-mutate", maturity="testing")

    import hashlib

    def _file_snapshot(directory: Path) -> dict[str, str]:
        result = {}
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                result[str(fpath)] = hashlib.sha256(fpath.read_bytes()).hexdigest()
        return result

    before = _file_snapshot(store.data_dir)

    check_missing_provenance(store)
    check_integrity_mismatch(store)
    check_stale_eval_records(store)
    check_quarantine_backlog(store.data_dir)
    check_proposal_backlog(store.data_dir)
    check_orphaned_artifacts(store.data_dir)
    check_agent_candidate_backlog(None)

    after = _file_snapshot(store.data_dir)
    assert set(before.keys()) == set(after.keys())
    for path in before:
        assert before[path] == after[path], f"File mutated by check function: {path}"


def test_check_index_drift_does_not_rebuild_index(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "drift-cap")

    index_path = tmp_path / "index.db"
    index = CapabilityIndex(db_path=index_path)
    index.init()

    assert index.count() == 0

    check_index_drift(store, index)

    # Index should NOT have been rebuilt
    assert index.count() == 0


def test_check_functions_never_raise(tmp_path: Path):
    """All check functions should be robust against edge cases and never raise."""
    store = _make_store(tmp_path)
    _make_doc(store, "raise-test")

    try:
        check_missing_provenance(store)
    except Exception as e:
        pytest.fail(f"check_missing_provenance raised: {e}")

    try:
        check_integrity_mismatch(store)
    except Exception as e:
        pytest.fail(f"check_integrity_mismatch raised: {e}")

    try:
        check_stale_eval_records(store)
    except Exception as e:
        pytest.fail(f"check_stale_eval_records raised: {e}")

    try:
        check_quarantine_backlog(store.data_dir)
    except Exception as e:
        pytest.fail(f"check_quarantine_backlog raised: {e}")

    try:
        check_proposal_backlog(store.data_dir)
    except Exception as e:
        pytest.fail(f"check_proposal_backlog raised: {e}")

    try:
        check_orphaned_artifacts(store.data_dir)
    except Exception as e:
        pytest.fail(f"check_orphaned_artifacts raised: {e}")

    try:
        check_agent_candidate_backlog(None)
    except Exception as e:
        pytest.fail(f"check_agent_candidate_backlog raised: {e}")

    try:
        check_stale_trust_roots(None)
    except Exception as e:
        pytest.fail(f"check_stale_trust_roots raised: {e}")

    try:
        check_index_drift(store, None)
    except Exception as e:
        pytest.fail(f"check_index_drift raised: {e}")


# ── Comprehensive byte-hash proof: all artifact types ──


def test_comprehensive_byte_hash_no_mutation_all_artifact_types(tmp_path: Path):
    """Set up a rich state with every artifact type, then verify zero mutation."""
    import hashlib

    data_dir = tmp_path / "caps"
    store = CapabilityStore(data_dir=data_dir)

    # Capabilities of various types
    cap_a = _make_doc(store, "draft-cap", maturity="draft")
    cap_b = _make_doc(store, "testing-cap", maturity="testing")
    cap_c = _make_doc(store, "stable-cap", maturity="stable")
    cap_d = _make_doc(store, "broken-cap", maturity="broken")
    cap_e = _make_doc(store, "disabled-cap", maturity="draft", status="disabled")
    cap_f = _make_doc(store, "archived-cap", maturity="draft", status="archived")

    # Provenance on some capabilities
    for cap_id in (cap_a, cap_b):
        doc = store.get(cap_id)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            source_type="manual_draft",
            trust_level="trusted_local",
        )

    # Eval record on testing cap
    doc_b = store.get(cap_b)
    write_eval_record(EvalRecord(
        capability_id=cap_b,
        scope=doc_b.manifest.scope.value,
        content_hash=doc_b.content_hash,
        created_at=datetime.now(timezone.utc).isoformat(),
        passed=True,
        score=1.0,
    ), doc_b)

    # Import report on a cap
    (store.get(cap_a).directory / "import_report.json").write_text(
        json.dumps({"source": "test", "imported_at": datetime.now(timezone.utc).isoformat()})
    )

    # Quarantine directory with audit artifacts
    quar_dir = data_dir / "quarantine" / "quar_rich"
    quar_dir.mkdir(parents=True)
    (quar_dir / "CAPABILITY.md").write_text(
        "---\nid: quar_rich\nname: QR\ndescription: T.\ntype: skill\nscope: user\n---\n\nBody."
    )
    (quar_dir / "manifest.json").write_text(json.dumps({
        "id": "quar_rich", "name": "QR", "description": "T.",
        "type": "skill", "scope": "user", "maturity": "draft", "status": "quarantined",
        "risk_level": "low",
    }))
    for subdir in ("quarantine_audit_reports", "quarantine_reviews",
                   "quarantine_transition_requests", "quarantine_activation_plans"):
        (quar_dir / subdir).mkdir(exist_ok=True)
        (quar_dir / subdir / "test.json").write_text('{"test": true}')

    # Proposals
    from src.capabilities.proposal import CapabilityProposal, persist_proposal
    ts = TraceSummary(
        trace_id=None, user_request="R.", final_result=None, task_type=None,
        context=None, tools_used=[], files_touched=[], commands_run=[],
        errors_seen=[], failed_attempts=[], successful_steps=[],
        verification=[], user_feedback=None, existing_capability_id=None,
        created_at=datetime.now(timezone.utc).isoformat(), metadata={},
    )
    prop = CapabilityProposal(
        proposal_id="prop-rich", source_trace_id=None,
        proposed_capability_id="rich-cap", name="Rich", description="Rich.",
        type="skill", scope="workspace",
    )
    persist_proposal(prop, ts, data_dir)

    # Agent candidates
    from src.agents.candidate import AgentCandidate
    from src.agents.candidate_store import AgentCandidateStore
    cs = AgentCandidateStore(base_dir=tmp_path / "agent_candidates")
    cs.create_candidate(AgentCandidate(
        candidate_id="cand-rich-001", name="Rich Candidate",
        description="Rich.", approval_state="pending", risk_level="medium",
    ))

    # Trust roots
    trust_store = TrustRootStore(data_dir=data_dir)
    trust_store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="rich-root",
        name="Rich Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:def456",
        owner="test",
        status="active",
    ))

    # Index with entries
    index_path = tmp_path / "index.db"
    index = CapabilityIndex(db_path=index_path)
    index.init()
    for cap_id in (cap_a, cap_b, cap_c):
        index.upsert(store.get(cap_id))

    # ── Hash ALL artifact sources ──
    def _hash_tree(directory: Path) -> dict[str, str]:
        result = {}
        if not directory.is_dir():
            return result
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                result[str(fpath)] = hashlib.sha256(fpath.read_bytes()).hexdigest()
        return result

    hashes_before = {
        "capabilities": _hash_tree(data_dir),
        "candidates": _hash_tree(cs._base_dir),
    }
    index_bytes_before = index_path.read_bytes() if index_path.exists() else b""

    # Run the full health report with all artifacts
    generate_capability_health_report(
        store, index=index, trust_root_store=trust_store, candidate_store=cs,
    )

    # ── Verify zero mutation ──
    hashes_after = {
        "capabilities": _hash_tree(data_dir),
        "candidates": _hash_tree(cs._base_dir),
    }
    index_bytes_after = index_path.read_bytes() if index_path.exists() else b""

    assert hashes_before["capabilities"] == hashes_after["capabilities"], \
        "Capability files mutated by health report"
    assert hashes_before["candidates"] == hashes_after["candidates"], \
        "Agent candidate files mutated by health report"
    assert index_bytes_before == index_bytes_after, \
        "Index DB mutated by health report"


# ── No eval records written ──


def test_no_eval_records_written_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "eval-immutable", maturity="testing")
    doc = store.get(cap_id)
    evals_dir = doc.directory / "evals"

    # The evals directory is a standard directory created by create_draft.
    # Health must not write any files into it.
    eval_files_before = set()
    if evals_dir.is_dir():
        eval_files_before = {f.name for f in evals_dir.iterdir() if f.is_file()}

    generate_capability_health_report(store)

    # No new eval files were written
    eval_files_after = set()
    if evals_dir.is_dir():
        eval_files_after = {f.name for f in evals_dir.iterdir() if f.is_file()}
    assert eval_files_before == eval_files_after


# ── No version snapshots created ──


def test_no_version_snapshots_created_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "version-immutable", maturity="draft")
    doc = store.get(cap_id)
    versions_dir = doc.directory / "versions"

    generate_capability_health_report(store)

    # Health report must not create version snapshots
    assert not versions_dir.is_dir() or not list(versions_dir.iterdir())


# ── No quarantine artifacts mutated ──


def test_no_quarantine_artifacts_mutated_by_health(tmp_path: Path):
    import hashlib
    store = _make_store(tmp_path)
    quar_dir = store.data_dir / "quarantine" / "quar_immutable"
    quar_dir.mkdir(parents=True)
    (quar_dir / "CAPABILITY.md").write_text(
        "---\nid: quar_immutable\nname: QI\ndescription: T.\ntype: skill\nscope: user\n---\n\nBody."
    )
    (quar_dir / "manifest.json").write_text(json.dumps({
        "id": "quar_immutable", "name": "QI", "description": "T.",
        "type": "skill", "scope": "user", "maturity": "draft", "status": "quarantined",
        "risk_level": "low",
    }))
    for subdir in ("quarantine_audit_reports", "quarantine_reviews",
                   "quarantine_transition_requests", "quarantine_activation_plans"):
        sdir = quar_dir / subdir
        sdir.mkdir(exist_ok=True)
        (sdir / "test.json").write_text('{"test": true}')

    def _hash_tree(d: Path) -> dict[str, str]:
        r = {}
        for fp in sorted(d.rglob("*")):
            if fp.is_file():
                r[str(fp)] = hashlib.sha256(fp.read_bytes()).hexdigest()
        return r

    before = _hash_tree(quar_dir)
    check_quarantine_backlog(store.data_dir)
    generate_capability_health_report(store)
    after = _hash_tree(quar_dir)
    assert before == after


# ── No provenance integrity status updated ──


def test_no_provenance_integrity_status_updated_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "prov-immutable")
    doc = store.get(cap_id)
    write_provenance(
        doc.directory, capability_id=cap_id,
        source_type="manual_draft",
        source_content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        trust_level="trusted_local",
    )

    prov_before = (doc.directory / "provenance.json").read_text()
    check_integrity_mismatch(store)
    generate_capability_health_report(store)
    prov_after = (doc.directory / "provenance.json").read_text()
    assert prov_before == prov_after


# ── No orphaned artifacts deleted ──


def test_no_orphaned_artifacts_deleted_by_health(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = _make_store(tmp_path)
    trust_store = TrustRootStore(data_dir=data_dir)
    roots_dir = trust_store.roots_dir
    roots_dir.mkdir(parents=True, exist_ok=True)
    (roots_dir / "corrupt_keep.json").write_text("not valid json")

    assert (roots_dir / "corrupt_keep.json").exists()
    check_orphaned_artifacts(data_dir, trust_store)
    generate_capability_health_report(store)
    # The corrupt file must still exist — health never deletes
    assert (roots_dir / "corrupt_keep.json").exists()


# ── No recursive mutation log entries ──


def test_no_lifecycle_mutation_log_entries_from_health(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "log-free")
    _make_doc(store, "log-free-2", maturity="stable")

    generate_capability_health_report(store)
    # Health report should not have created any capabilities, proposals, or
    # triggered any lifecycle mutation. We verify this indirectly: no new
    # directories appeared in the data dir beyond the expected scope dirs.
    expected_dirs = {s.value for s in CapabilityScope}
    for child in store.data_dir.iterdir():
        if child.is_dir():
            assert child.name in expected_dirs or child.name == "workspace", \
                f"Unexpected directory created in data dir: {child.name}"


# ── Recommendation safety: no execution fields ──


def test_recommendation_safety_no_execution_fields(tmp_path: Path):
    """Recommendations must not contain auto_fix, apply, execute, or action fields."""
    store = _make_store(tmp_path)
    _make_doc(store, "rec-safe", maturity="broken")
    report = generate_capability_health_report(store)
    for rec in report.recommendations:
        assert isinstance(rec, str)
        assert "auto_fix" not in rec.lower()
        assert "autofix" not in rec.lower()
        assert '"apply"' not in rec.lower()
        assert '"execute"' not in rec.lower()
        assert '"action"' not in rec.lower()
        assert not rec.startswith("!")


# ── Extended no-execution audit ──


def test_no_exec_eval_importlib_runpy_in_health():
    """Extended audit: health.py must not contain exec/eval/importlib/runpy/compile."""
    import ast
    import inspect
    from src.capabilities import health

    source = inspect.getsource(health)
    tree = ast.parse(source)

    dangerous_funcs = {"exec", "eval", "compile", "__import__"}
    dangerous_modules = {"importlib", "runpy", "codeop", "code"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in dangerous_funcs, \
                    f"health.py calls {node.func.id}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in dangerous_modules, \
                    f"health.py imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module not in dangerous_modules, \
                    f"health.py imports from {node.module}"


def test_generate_report_never_raises(tmp_path: Path):
    """generate_capability_health_report should never raise, even on empty/corrupt state."""
    store = _make_store(tmp_path)
    # Empty store
    try:
        report = generate_capability_health_report(store)
        assert report.total_capabilities == 0
    except Exception as e:
        pytest.fail(f"generate_capability_health_report raised on empty store: {e}")

    _make_doc(store, "some-cap", maturity="stable", status="disabled")

    try:
        report = generate_capability_health_report(store)
        assert report.total_capabilities == 1
    except Exception as e:
        pytest.fail(f"generate_capability_health_report raised on populated store: {e}")
