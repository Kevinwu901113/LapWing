"""Maintenance A: Capability Health Report tests.

Tests for the read-only health report generation. Verifies correct counts,
finding detection, and deterministic behavior.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.capabilities.eval_records import write_eval_record
from src.capabilities.evaluator import CapabilityEvaluator, EvalRecord
from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
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
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.proposal import CapabilityProposal, persist_proposal
from src.capabilities.provenance import write_provenance, compute_capability_tree_hash
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityScope,
    CapabilityStatus,
    CapabilityRiskLevel,
)
from src.capabilities.store import CapabilityStore
from src.capabilities.trace_summary import TraceSummary
from src.capabilities.trust_roots import TrustRootStore
from src.capabilities.signature import CapabilityTrustRoot
from src.eval.axes import AxisResult, AxisStatus, EvalAxis


# ── Helpers ──


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_doc(
    store: CapabilityStore,
    name: str = "Test Capability",
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
    maturity: str = "draft",
    status: str = "active",
    risk_level: str = "low",
) -> str:
    """Create a draft capability and return its ID."""
    doc = store.create_draft(
        scope=scope,
        name=name,
        description=f"Description for {name}.",
        type="skill",
        risk_level=risk_level,
    )
    cap_dir = doc.directory
    manifest = doc.manifest

    # Patch manifest if non-default maturity/status requested
    if maturity != "draft" or status != "active":
        new_manifest = manifest.model_copy(update={
            "maturity": CapabilityMaturity(maturity),
            "status": CapabilityStatus(status),
        })
        doc.manifest = new_manifest
        store._sync_manifest_json(cap_dir, doc)
        store._parser.parse(cap_dir)

    return doc.id


def _passing_axes() -> dict[str, AxisResult]:
    return {
        EvalAxis.FUNCTIONAL.value: AxisResult(EvalAxis.FUNCTIONAL, AxisStatus.PASS),
        EvalAxis.SAFETY.value: AxisResult(EvalAxis.SAFETY, AxisStatus.PASS),
        EvalAxis.PRIVACY.value: AxisResult(EvalAxis.PRIVACY, AxisStatus.PASS),
        EvalAxis.REVERSIBILITY.value: AxisResult(EvalAxis.REVERSIBILITY, AxisStatus.PASS),
    }


# ── Tests: empty system ──


def test_empty_system_returns_valid_report(tmp_path: Path):
    store = _make_store(tmp_path)
    report = generate_capability_health_report(store)

    assert report.total_capabilities == 0
    assert report.by_status == {}
    assert report.by_maturity == {}
    assert report.by_scope == {}
    assert report.quarantined_count == 0
    assert report.stable_count == 0
    assert report.testing_count == 0
    assert report.findings == []
    assert isinstance(report.recommendations, list)
    assert report.generated_at  # has timestamp


# ── Tests: status / maturity / scope counts ──


def test_counts_statuses_correctly(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "active-a", status="active")
    _make_doc(store, "disabled-a", status="disabled")

    report = generate_capability_health_report(store)
    assert report.by_status.get("active", 0) == 1
    assert report.by_status.get("disabled", 0) == 1


def test_counts_maturities_correctly(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "draft-a", maturity="draft")
    _make_doc(store, "testing-a", maturity="testing")
    _make_doc(store, "stable-a", maturity="stable")

    report = generate_capability_health_report(store)
    assert report.by_maturity.get("draft", 0) == 1
    assert report.by_maturity.get("testing", 0) == 1
    assert report.by_maturity.get("stable", 0) == 1
    assert report.testing_count == 1
    assert report.stable_count == 1


def test_counts_scopes_correctly(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "global-a", scope=CapabilityScope.GLOBAL)
    _make_doc(store, "user-a", scope=CapabilityScope.USER)
    _make_doc(store, "workspace-a", scope=CapabilityScope.WORKSPACE)

    report = generate_capability_health_report(store)
    assert report.by_scope.get("global", 0) == 1
    assert report.by_scope.get("user", 0) == 1
    assert report.by_scope.get("workspace", 0) == 1


def test_counts_broken_and_repairing(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "broken-a", maturity="broken")
    _make_doc(store, "repairing-a", maturity="repairing")

    report = generate_capability_health_report(store)
    assert report.broken_count == 1
    assert report.repairing_count == 1


# ── Tests: missing provenance ──


def test_detects_missing_provenance(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "no-prov")
    doc = store.get(cap_id)
    # Ensure no provenance.json exists
    prov_path = doc.directory / "provenance.json"
    if prov_path.exists():
        prov_path.unlink()

    findings = check_missing_provenance(store)
    assert len(findings) >= 1
    code = findings[0]
    assert code.code == "missing_provenance_legacy"
    assert code.severity == "info"
    assert code.capability_id == cap_id


def test_detects_missing_provenance_on_quarantined(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "quar-no-prov", status="quarantined")

    findings = check_missing_provenance(store)
    quar_findings = [f for f in findings if f.capability_id == cap_id]
    assert len(quar_findings) == 1
    assert quar_findings[0].severity == "error"
    assert quar_findings[0].code == "missing_provenance_quarantined"


def test_no_finding_when_provenance_present(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "has-prov")
    doc = store.get(cap_id)
    write_provenance(
        doc.directory,
        capability_id=cap_id,
        source_type="manual_draft",
        trust_level="trusted_local",
    )

    findings = check_missing_provenance(store)
    ids = [f.capability_id for f in findings if f.capability_id == cap_id]
    assert len(ids) == 0


# ── Tests: integrity mismatch ──


def test_detects_integrity_mismatch(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "mismatch-cap")
    doc = store.get(cap_id)

    # Write provenance with a fake hash
    write_provenance(
        doc.directory,
        capability_id=cap_id,
        source_type="manual_draft",
        source_content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        trust_level="trusted_local",
    )

    findings = check_integrity_mismatch(store)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1
    assert ids[0].code == "integrity_mismatch"
    assert ids[0].severity == "warning"


def test_no_integrity_mismatch_when_hash_matches(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "match-cap")
    doc = store.get(cap_id)
    current_hash = compute_capability_tree_hash(doc.directory)

    write_provenance(
        doc.directory,
        capability_id=cap_id,
        source_type="manual_draft",
        source_content_hash=current_hash,
        trust_level="trusted_local",
    )

    findings = check_integrity_mismatch(store)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 0


def test_no_integrity_mismatch_without_provenance(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "no-prov-cap")

    findings = check_integrity_mismatch(store)
    assert len(findings) == 0


# ── Tests: stale eval records ──


def test_detects_missing_eval_for_testing(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "test-no-eval", maturity="testing")

    findings = check_stale_eval_records(store, stale_days=30)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1
    assert ids[0].code == "needs_eval"
    assert ids[0].severity == "warning"


def test_detects_missing_eval_for_stable(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "stable-no-eval", maturity="stable")

    findings = check_stale_eval_records(store, stale_days=30)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1
    assert ids[0].code == "needs_eval"


def test_no_stale_eval_for_draft(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "draft-no-eval", maturity="draft")

    findings = check_stale_eval_records(store, stale_days=30)
    assert len(findings) == 0


def test_stale_eval_detected_when_old(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "stale-eval", maturity="testing")
    doc = store.get(cap_id)

    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    record = EvalRecord(
        capability_id=cap_id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        created_at=old_time,
        passed=True,
        score=1.0,
        axes=_passing_axes(),
    )
    write_eval_record(record, doc)

    findings = check_stale_eval_records(store, stale_days=30)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1
    assert ids[0].code == "eval_stale"


def test_recent_eval_not_stale(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "recent-eval", maturity="testing")
    doc = store.get(cap_id)

    recent_time = datetime.now(timezone.utc).isoformat()
    record = EvalRecord(
        capability_id=cap_id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        created_at=recent_time,
        passed=True,
        score=1.0,
        axes=_passing_axes(),
    )
    write_eval_record(record, doc)

    findings = check_stale_eval_records(store, stale_days=30)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 0


# ── Tests: stale trust roots ──


def test_detects_revoked_trust_root(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    root = store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="revoked-root",
        name="Revoked Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:abc123",
        owner="test",
    ))
    store.revoke_trust_root("revoked-root", "test revocation")

    findings = check_stale_trust_roots(store)
    ids = [f for f in findings if f.code == "trust_root_revoked"]
    assert len(ids) == 1
    assert ids[0].details["trust_root_id"] == "revoked-root"


def test_detects_disabled_trust_root(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="disabled-root",
        name="Disabled Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:abc123",
        owner="test",
    ))
    store.disable_trust_root("disabled-root", "test disable")

    findings = check_stale_trust_roots(store)
    ids = [f for f in findings if f.code == "trust_root_disabled"]
    assert len(ids) == 1


def test_detects_expired_trust_root(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="expired-root",
        name="Expired Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:abc123",
        owner="test",
        expires_at=expired,
    ))

    findings = check_stale_trust_roots(store)
    ids = [f for f in findings if f.code == "trust_root_expired"]
    assert len(ids) == 1


def test_no_warning_for_future_expiry(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    far_future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    store.create_trust_root(CapabilityTrustRoot(
        trust_root_id="far-future-root",
        name="Far Future Root",
        key_type="ed25519",
        public_key_fingerprint="sha256:abc123",
        owner="test",
        expires_at=far_future,
    ))

    findings = check_stale_trust_roots(store, expiry_warn_days=30)
    ids = [f for f in findings if f.details.get("trust_root_id") == "far-future-root"]
    assert len(ids) == 0


def test_no_error_for_none_store():
    findings = check_stale_trust_roots(None)
    assert findings == []


# ── Tests: quarantine backlog ──


def test_detects_quarantine_without_audit(tmp_path: Path):
    store = _make_store(tmp_path)
    # Quarantine caps live in data_dir/quarantine/<id>/, not in the store's scope dirs.
    # Create a quarantine directory manually.
    quar_dir = store.data_dir / "quarantine" / "quar_no_audit"
    quar_dir.mkdir(parents=True)
    (quar_dir / "CAPABILITY.md").write_text("---\nid: quar_no_audit\nname: Q No Audit\ndescription: Test.\ntype: skill\nscope: user\n---\n\nBody.", encoding="utf-8")
    (quar_dir / "manifest.json").write_text(json.dumps({
        "id": "quar_no_audit", "name": "Q No Audit", "description": "Test.",
        "type": "skill", "scope": "user", "maturity": "draft", "status": "quarantined",
        "risk_level": "low",
    }))

    findings = check_quarantine_backlog(store.data_dir)
    ids = [f for f in findings if f.capability_id == "quar_no_audit"]
    assert len(ids) == 1
    assert ids[0].code == "quarantine_no_audit"


def test_detects_quarantine_audit_pending_review(tmp_path: Path):
    store = _make_store(tmp_path)
    # Create quarantine directory with audit but no review
    quar_dir = store.data_dir / "quarantine" / "quar_audit_only"
    quar_dir.mkdir(parents=True)
    (quar_dir / "CAPABILITY.md").write_text("---\nid: quar_audit_only\nname: Q Audit Only\ndescription: Test.\ntype: skill\nscope: user\n---\n\nBody.", encoding="utf-8")
    (quar_dir / "manifest.json").write_text(json.dumps({
        "id": "quar_audit_only", "name": "Q Audit Only", "description": "Test.",
        "type": "skill", "scope": "user", "maturity": "draft", "status": "quarantined",
        "risk_level": "low",
    }))
    audit_dir = quar_dir / "quarantine_audit_reports"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "audit_001.json").write_text(json.dumps({"passed": True}))

    findings = check_quarantine_backlog(store.data_dir)
    ids = [f for f in findings if f.capability_id == "quar_audit_only"]
    assert len(ids) == 1
    assert ids[0].code == "quarantine_audit_pending_review"


def test_no_quarantine_backlog_without_quarantine_dir(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "active-cap", status="active")

    findings = check_quarantine_backlog(store.data_dir)
    assert len(findings) == 0


# ── Tests: proposal backlog ──


def test_detects_proposal_backlog(tmp_path: Path):
    data_dir = tmp_path / "capabilities"
    store = CapabilityStore(data_dir=data_dir)

    ts = TraceSummary(
        trace_id=None,
        user_request="Do something reusable.",
        final_result=None,
        task_type=None,
        context=None,
        tools_used=["tool_a"],
        files_touched=[],
        commands_run=[],
        errors_seen=[],
        failed_attempts=[],
        successful_steps=[],
        verification=[],
        user_feedback=None,
        existing_capability_id=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )
    proposal = CapabilityProposal(
        proposal_id="prop-001",
        source_trace_id=ts.trace_id,
        proposed_capability_id="test-cap",
        name="Test Proposal",
        description="A test proposal.",
        type="skill",
        scope="workspace",
        required_approval=False,
        risk_level="low",
    )
    persist_proposal(proposal, ts, data_dir)

    findings = check_proposal_backlog(data_dir, stale_days=90)
    ids = [f for f in findings if f.code == "proposal_pending"]
    assert len(ids) == 1


def test_detects_stale_proposal(tmp_path: Path):
    data_dir = tmp_path / "capabilities"
    CapabilityStore(data_dir=data_dir)

    old_time = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    ts = TraceSummary(
        trace_id=None,
        user_request="Old reusable task.",
        final_result=None,
        task_type=None,
        context=None,
        tools_used=["tool_a"],
        files_touched=[],
        commands_run=[],
        errors_seen=[],
        failed_attempts=[],
        successful_steps=[],
        verification=[],
        user_feedback=None,
        existing_capability_id=None,
        created_at=old_time,
        metadata={},
    )
    proposal = CapabilityProposal(
        proposal_id="prop-stale",
        source_trace_id=ts.trace_id,
        proposed_capability_id="stale-cap",
        name="Stale Proposal",
        description="A very old proposal.",
        type="skill",
        scope="workspace",
        created_at=old_time,
    )
    persist_proposal(proposal, ts, data_dir)

    findings = check_proposal_backlog(data_dir, stale_days=90)
    ids = [f for f in findings if f.code == "proposal_stale"]
    assert len(ids) == 1


def test_no_proposal_backlog_without_proposals_dir(tmp_path: Path):
    data_dir = tmp_path / "caps"
    _make_store(tmp_path)
    # No proposals dir created

    findings = check_proposal_backlog(data_dir)
    assert findings == []


# ── Tests: index drift ──


def test_detects_missing_index_entries(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "no-index-cap")

    index_path = tmp_path / "index.db"
    index = CapabilityIndex(db_path=index_path)
    index.init()

    findings = check_index_drift(store, index)
    missing = [f for f in findings if f.code == "index_missing_row"]
    assert len(missing) == 1


def test_detects_stale_index_entries(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "stale-idx-cap")

    index_path = tmp_path / "index.db"
    index = CapabilityIndex(db_path=index_path)
    index.init()
    # Upsert so the index has the row
    doc = store.get(cap_id)
    index.upsert(doc)
    # Remove the store directory to create a stale index row
    import shutil
    shutil.rmtree(doc.directory)

    findings = check_index_drift(store, index)
    stale = [f for f in findings if f.code == "index_stale_row"]
    assert len(stale) == 1


def test_no_index_drift_when_none(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "no-idx-cap")

    findings = check_index_drift(store, None)
    assert findings == []


# ── Tests: orphaned artifacts ──


def test_detects_corrupt_trust_root(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    # Write a corrupt JSON file directly
    roots_dir = store.roots_dir
    roots_dir.mkdir(parents=True, exist_ok=True)
    (roots_dir / "corrupt.json").write_text("not valid json", encoding="utf-8")

    findings = check_orphaned_artifacts(data_dir, store)
    corrupt = [f for f in findings if f.code == "orphaned_corrupt_trust_root"]
    assert len(corrupt) >= 1


def test_detects_orphaned_quarantine_directory(tmp_path: Path):
    data_dir = tmp_path / "caps"
    _make_store(tmp_path)

    # Create a quarantine dir without CAPABILITY.md but with artifacts
    quar_dir = data_dir / "quarantine" / "orphaned-cap"
    quar_dir.mkdir(parents=True)
    (quar_dir / "quarantine_audit_reports").mkdir()
    (quar_dir / "quarantine_audit_reports" / "audit_001.json").write_text("{}")

    findings = check_orphaned_artifacts(data_dir)
    ids = [f for f in findings if f.code == "orphaned_quarantine_artifacts"]
    assert len(ids) == 1


def test_no_orphaned_findings_on_clean_system(tmp_path: Path):
    data_dir = tmp_path / "caps"
    _make_store(tmp_path)

    findings = check_orphaned_artifacts(data_dir)
    assert findings == []


# ── Tests: agent candidate backlog ──


def test_detects_agent_candidate_backlog(tmp_path: Path):
    from src.agents.candidate import AgentCandidate
    from src.agents.candidate_store import AgentCandidateStore

    cs = AgentCandidateStore(base_dir=tmp_path / "agent_candidates")
    cand = AgentCandidate(
        candidate_id="cand-test-001",
        name="Test Candidate",
        description="A test candidate.",
        approval_state="pending",
        risk_level="high",
    )
    cs.create_candidate(cand)

    findings = check_agent_candidate_backlog(cs)
    ids = [f for f in findings if f.details.get("candidate_id") == "cand-test-001"]
    assert len(ids) == 1
    assert ids[0].code == "candidate_high_risk_no_evidence"


def test_no_candidate_backlog_when_none():
    findings = check_agent_candidate_backlog(None)
    assert findings == []


# ── Tests: recommendations ──


def test_recommendations_are_text_only(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "broken-a", maturity="broken")

    report = generate_capability_health_report(store)
    assert isinstance(report.recommendations, list)
    assert len(report.recommendations) > 0
    for rec in report.recommendations:
        assert isinstance(rec, str)
        # Recommendations should NOT contain executable code patterns
        assert "exec(" not in rec
        assert "eval(" not in rec
        assert "os.system" not in rec
        assert "subprocess" not in rec.lower()


def test_recommendations_not_executable(tmp_path: Path):
    """Recommendations are descriptive text, not Python code or commands."""
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "test-rec", maturity="testing")
    doc = store.get(cap_id)

    # Create a mismatch to trigger more recommendations
    write_provenance(
        doc.directory,
        capability_id=cap_id,
        source_type="manual_draft",
        source_content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        trust_level="trusted_local",
    )

    report = generate_capability_health_report(store)
    for rec in report.recommendations:
        # Check it's descriptive, not a command
        assert isinstance(rec, str)
        assert not rec.startswith("$")
        assert not rec.startswith(">>>")
        # Should not contain importable Python
        assert "import " not in rec or "import " in rec[:20]


# ── Tests: determinism ──


def test_deterministic_same_inputs_same_findings(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "cap-a")
    _make_doc(store, "cap-b", maturity="testing")

    report1 = generate_capability_health_report(store)
    report2 = generate_capability_health_report(store)

    # Same findings (ignoring generated_at)
    assert len(report1.findings) == len(report2.findings)
    for f1, f2 in zip(
        sorted(report1.findings, key=lambda f: f.code),
        sorted(report2.findings, key=lambda f: f.code),
    ):
        assert f1.code == f2.code
        assert f1.severity == f2.severity
        assert f1.message == f2.message

    assert report1.total_capabilities == report2.total_capabilities
    assert report1.by_status == report2.by_status
    assert report1.by_maturity == report2.by_maturity
    assert report1.recommendations == report2.recommendations


# ── Tests: full report integration ──


def test_full_report_includes_all_check_results(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "cap1")
    _make_doc(store, "cap2", maturity="testing")
    _make_doc(store, "cap3", maturity="stable")

    report = generate_capability_health_report(store)

    assert report.total_capabilities == 3
    assert report.stable_count == 1
    assert report.testing_count == 1
    assert isinstance(report.findings, list)
    assert isinstance(report.recommendations, list)
    assert report.generated_at


# ── Corruption tolerance tests ──


def test_corrupt_manifest_json_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "corrupt-manifest")
    doc = store.get(cap_id)
    (doc.directory / "manifest.json").write_text("{{{not valid json", encoding="utf-8")

    # Should not raise; corrupt caps are skipped by store.list()
    report = generate_capability_health_report(store)
    assert report.total_capabilities >= 0


def test_corrupt_provenance_json_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "corrupt-prov")
    doc = store.get(cap_id)
    (doc.directory / "provenance.json").write_text("{{{bad json", encoding="utf-8")

    # Should not raise; corrupt provenance returns None from read_provenance
    findings = check_missing_provenance(store)
    # The cap has a "provenance.json" file but it's corrupt, so read_provenance returns None
    # This is treated as missing provenance
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1


def test_corrupt_proposal_json_handled(tmp_path: Path):
    data_dir = tmp_path / "capabilities"
    CapabilityStore(data_dir=data_dir)
    prop_dir = data_dir / "proposals" / "corrupt-prop"
    prop_dir.mkdir(parents=True)
    (prop_dir / "proposal.json").write_text("{{{corrupt json", encoding="utf-8")

    findings = check_proposal_backlog(data_dir)
    corrupt = [f for f in findings if f.code == "proposal_corrupt"]
    assert len(corrupt) >= 1


def test_corrupt_trust_root_json_handled(tmp_path: Path):
    data_dir = tmp_path / "caps"
    store = TrustRootStore(data_dir=data_dir)
    roots_dir = store.roots_dir
    roots_dir.mkdir(parents=True, exist_ok=True)
    (roots_dir / "corrupt.json").write_text("{bad json!", encoding="utf-8")

    findings = check_orphaned_artifacts(data_dir, store)
    corrupt = [f for f in findings if f.code == "orphaned_corrupt_trust_root"]
    assert len(corrupt) >= 1


def test_missing_capability_md_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    # Create a dir with manifest.json but no CAPABILITY.md
    broken_dir = store.data_dir / "workspace" / "no-cap-md"
    broken_dir.mkdir(parents=True)
    (broken_dir / "manifest.json").write_text(json.dumps({
        "id": "no-cap-md", "name": "NCMD", "description": "Test.",
    }))

    # Should not raise; store.list() skips dirs without CAPABILITY.md
    report = generate_capability_health_report(store)
    assert report.total_capabilities >= 0


def test_missing_manifest_json_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "no-manifest")
    doc = store.get(cap_id)
    (doc.directory / "manifest.json").unlink()

    # Without manifest.json, store.list() should skip the invalid dir
    report = generate_capability_health_report(store)
    assert report.total_capabilities >= 0


def test_missing_index_db_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "no-idx-db")

    index_path = tmp_path / "nonexistent" / "index.db"
    index = CapabilityIndex(db_path=index_path)
    # Don't init — so the conn is None
    # check_index_drift should handle this gracefully
    findings = check_index_drift(store, index)
    # No conn = no index entries to compare = no findings
    assert isinstance(findings, list)


def test_empty_directory_in_quarantine_handled(tmp_path: Path):
    store = _make_store(tmp_path)
    quar_dir = store.data_dir / "quarantine" / "empty-dir"
    quar_dir.mkdir(parents=True)
    # No CAPABILITY.md — completely empty

    findings = check_quarantine_backlog(store.data_dir)
    # Empty dirs without CAPABILITY.md are skipped by _quarantine_cap_dirs
    # But might be caught by check_orphaned_artifacts
    assert isinstance(findings, list)


def test_unreadable_file_handled(tmp_path: Path):
    """Corrupt files that cause read errors should not crash checks."""
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "junk-file")
    doc = store.get(cap_id)
    # Write binary garbage as a provenance-like file
    (doc.directory / "provenance.json").write_bytes(b"\x00\x01\x02\xff\xfe\xfd")

    # Should not raise; read_provenance returns None for corrupt files
    findings = check_missing_provenance(store)
    assert isinstance(findings, list)
    # The corrupt provenance file is treated as missing provenance (read returns None)
    ids = [f for f in findings if f.capability_id == cap_id]
    assert len(ids) == 1


# ── Counting correctness ──


def test_status_counts_sum_to_total(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "s-active", status="active")
    _make_doc(store, "s-disabled", status="disabled")
    _make_doc(store, "s-archived", status="archived")

    report = generate_capability_health_report(store)
    assert sum(report.by_status.values()) == report.total_capabilities


def test_maturity_counts_sum_to_total(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "m-draft", maturity="draft")
    _make_doc(store, "m-testing", maturity="testing")
    _make_doc(store, "m-stable", maturity="stable")
    _make_doc(store, "m-broken", maturity="broken")
    _make_doc(store, "m-repairing", maturity="repairing")

    report = generate_capability_health_report(store)
    assert sum(report.by_maturity.values()) == report.total_capabilities


def test_scope_counts_sum_to_total(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "sc-global", scope=CapabilityScope.GLOBAL)
    _make_doc(store, "sc-user", scope=CapabilityScope.USER)
    _make_doc(store, "sc-workspace", scope=CapabilityScope.WORKSPACE)

    report = generate_capability_health_report(store)
    assert sum(report.by_scope.values()) == report.total_capabilities


def test_quarantine_count_excludes_active_copies(tmp_path: Path):
    """Quarantine count comes from status, not from quarantine directory."""
    store = _make_store(tmp_path)
    _make_doc(store, "q-status-active", status="quarantined")
    _make_doc(store, "a-status-active", status="active")

    report = generate_capability_health_report(store)
    assert report.quarantined_count == 1
    assert report.by_status.get("quarantined", 0) == 1


def test_distinct_maturity_counts(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "broken-only", maturity="broken")
    _make_doc(store, "repairing-only", maturity="repairing")

    report = generate_capability_health_report(store)
    assert report.broken_count == 1
    assert report.repairing_count == 1


def test_proposals_counted_independently(tmp_path: Path):
    data_dir = tmp_path / "capabilities"
    CapabilityStore(data_dir=data_dir)

    ts = TraceSummary(
        trace_id=None, user_request="Req.", final_result=None, task_type=None,
        context=None, tools_used=[], files_touched=[], commands_run=[],
        errors_seen=[], failed_attempts=[], successful_steps=[],
        verification=[], user_feedback=None, existing_capability_id=None,
        created_at=datetime.now(timezone.utc).isoformat(), metadata={},
    )
    persist_proposal(CapabilityProposal(
        proposal_id="prop-independent",
        source_trace_id=None, proposed_capability_id="ind-cap",
        name="Independent", description="Ind.",
        type="skill", scope="workspace",
    ), ts, data_dir)

    store = CapabilityStore(data_dir=data_dir)
    report = generate_capability_health_report(store, data_dir=data_dir)
    # proposals_count counts from filesystem, independent of store caps
    assert report.proposals_count >= 1


# ── Integrity / provenance hardening ──


def test_volatile_dirs_excluded_from_integrity(tmp_path: Path):
    """Volatile dirs (evals, traces, versions) don't affect integrity checks."""
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "volatile-test")
    doc = store.get(cap_id)

    # Compute hash before adding volatile content
    hash_before = compute_capability_tree_hash(doc.directory)

    # Add content to volatile dirs
    (doc.directory / "evals").mkdir(exist_ok=True)
    (doc.directory / "evals" / "eval_test.json").write_text(json.dumps({"score": 0.5}))
    (doc.directory / "traces").mkdir(exist_ok=True)
    (doc.directory / "traces" / "trace.json").write_text("trace data")
    (doc.directory / "versions").mkdir(exist_ok=True)
    (doc.directory / "versions" / "v1.json").write_text("version data")

    # Hash after should be the same (volatile dirs excluded)
    hash_after = compute_capability_tree_hash(doc.directory)
    assert hash_before == hash_after


def test_provenance_integrity_status_not_updated_by_check(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "int-status-immutable")
    doc = store.get(cap_id)
    write_provenance(
        doc.directory, capability_id=cap_id,
        source_type="manual_draft",
        source_content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        trust_level="trusted_local",
    )

    status_before = read_provenance_file_status(doc.directory)
    check_integrity_mismatch(store)
    status_after = read_provenance_file_status(doc.directory)
    assert status_before == status_after


def read_provenance_file_status(directory: Path) -> str | None:
    """Read integrity_status from provenance.json without using the provenance module."""
    prov_path = directory / "provenance.json"
    if not prov_path.exists():
        return None
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
        return data.get("integrity_status")
    except (json.JSONDecodeError, OSError):
        return None


def test_provenance_not_rewritten_by_health(tmp_path: Path):
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "prov-rewrite-test")
    doc = store.get(cap_id)
    write_provenance(
        doc.directory, capability_id=cap_id,
        source_type="manual_draft", trust_level="trusted_local",
    )

    bytes_before = (doc.directory / "provenance.json").read_bytes()
    generate_capability_health_report(store)
    bytes_after = (doc.directory / "provenance.json").read_bytes()
    assert bytes_before == bytes_after


# ── Backlog hardening ──


def test_all_quarantine_pipeline_stages_detected(tmp_path: Path):
    """Verify each quarantine pipeline stage gap is detected."""
    store = _make_store(tmp_path)

    # Stage 1: no audit
    _create_quarantine_cap(store.data_dir, "q-stage1", has_audit=False, has_review=False,
                           has_request=False, has_plan=False)
    # Stage 2: audit only
    _create_quarantine_cap(store.data_dir, "q-stage2", has_audit=True, has_review=False,
                           has_request=False, has_plan=False)
    # Stage 3: review only
    _create_quarantine_cap(store.data_dir, "q-stage3", has_audit=True, has_review=True,
                           has_request=False, has_plan=False)
    # Stage 4: request only
    _create_quarantine_cap(store.data_dir, "q-stage4", has_audit=True, has_review=True,
                           has_request=True, has_plan=False)

    findings = check_quarantine_backlog(store.data_dir)

    codes = {f.code for f in findings}
    assert "quarantine_no_audit" in codes
    assert "quarantine_audit_pending_review" in codes
    assert "quarantine_review_pending_request" in codes
    assert "quarantine_request_pending_plan" in codes


def _create_quarantine_cap(
    data_dir: Path, cap_id: str, *,
    has_audit: bool, has_review: bool, has_request: bool, has_plan: bool,
) -> None:
    quar_dir = data_dir / "quarantine" / cap_id
    quar_dir.mkdir(parents=True)
    (quar_dir / "CAPABILITY.md").write_text(
        f"---\nid: {cap_id}\nname: {cap_id}\ndescription: T.\ntype: skill\nscope: user\n---\n\nBody."
    )
    (quar_dir / "manifest.json").write_text(json.dumps({
        "id": cap_id, "name": cap_id, "description": "T.",
        "type": "skill", "scope": "user", "maturity": "draft", "status": "quarantined",
        "risk_level": "low",
    }))
    if has_audit:
        d = quar_dir / "quarantine_audit_reports"
        d.mkdir(exist_ok=True)
        (d / "audit_001.json").write_text('{"passed": true}')
    if has_review:
        d = quar_dir / "quarantine_reviews"
        d.mkdir(exist_ok=True)
        (d / "review_001.json").write_text('{"review_status": "approved_for_testing"}')
    if has_request:
        d = quar_dir / "quarantine_transition_requests"
        d.mkdir(exist_ok=True)
        (d / "request_001.json").write_text('{"status": "pending"}')
    if has_plan:
        d = quar_dir / "quarantine_activation_plans"
        d.mkdir(exist_ok=True)
        (d / "plan_001.json").write_text('{"allowed": true}')


def test_all_trust_root_states_detected_but_not_mutated(tmp_path: Path):
    data_dir = tmp_path / "caps"
    ts = TrustRootStore(data_dir=data_dir)
    ts.create_trust_root(CapabilityTrustRoot(
        trust_root_id="active-tr", name="Active", key_type="ed25519",
        public_key_fingerprint="sha256:aaa", owner="test", status="active",
    ))
    ts.create_trust_root(CapabilityTrustRoot(
        trust_root_id="disabled-tr", name="Disabled", key_type="ed25519",
        public_key_fingerprint="sha256:bbb", owner="test", status="active",
    ))
    ts.create_trust_root(CapabilityTrustRoot(
        trust_root_id="revoked-tr", name="Revoked", key_type="ed25519",
        public_key_fingerprint="sha256:ccc", owner="test", status="active",
    ))
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ts.create_trust_root(CapabilityTrustRoot(
        trust_root_id="expired-tr", name="Expired", key_type="ed25519",
        public_key_fingerprint="sha256:ddd", owner="test",
        expires_at=expired, status="active",
    ))
    ts.disable_trust_root("disabled-tr", "test")
    ts.revoke_trust_root("revoked-tr", "test")

    findings = check_stale_trust_roots(ts)

    codes = {f.code for f in findings}
    assert "trust_root_disabled" in codes
    assert "trust_root_revoked" in codes
    assert "trust_root_expired" in codes

    # Verify no mutation
    assert ts.get_trust_root("active-tr").status == "active"
    assert ts.get_trust_root("disabled-tr").status == "disabled"
    assert ts.get_trust_root("revoked-tr").status == "revoked"


def test_high_risk_proposal_detected(tmp_path: Path):
    data_dir = tmp_path / "capabilities"
    CapabilityStore(data_dir=data_dir)
    ts = TraceSummary(
        trace_id=None, user_request="HR.", final_result=None, task_type=None,
        context=None, tools_used=[], files_touched=[], commands_run=[],
        errors_seen=[], failed_attempts=[], successful_steps=[],
        verification=[], user_feedback=None, existing_capability_id=None,
        created_at=datetime.now(timezone.utc).isoformat(), metadata={},
    )
    persist_proposal(CapabilityProposal(
        proposal_id="prop-high-risk",
        source_trace_id=None, proposed_capability_id="hr-cap",
        name="High Risk", description="HR.", type="skill", scope="workspace",
        risk_level="high", required_approval=True,
    ), ts, data_dir)

    findings = check_proposal_backlog(data_dir, stale_days=90)
    codes = {f.code for f in findings}
    assert "proposal_high_risk_pending" in codes


def test_pending_candidate_detected(tmp_path: Path):
    from src.agents.candidate import AgentCandidate
    from src.agents.candidate_store import AgentCandidateStore

    cs = AgentCandidateStore(base_dir=tmp_path / "agent_candidates")
    cs.create_candidate(AgentCandidate(
        candidate_id="cand-pending-low", name="Pending Low",
        description="PL.", approval_state="pending", risk_level="low",
    ))

    findings = check_agent_candidate_backlog(cs)
    codes = {f.code for f in findings}
    assert "candidate_pending" in codes


# ── Determinism hardening ──


def test_deterministic_on_corrupt_data(tmp_path: Path):
    """Same corrupt state produces identical report (except generated_at)."""
    store = _make_store(tmp_path)
    cap_id = _make_doc(store, "det-corrupt")
    doc = store.get(cap_id)
    (doc.directory / "provenance.json").write_text("{{{corrupt")

    report1 = generate_capability_health_report(store)
    report2 = generate_capability_health_report(store)

    assert report1.total_capabilities == report2.total_capabilities
    assert report1.by_status == report2.by_status
    assert report1.by_maturity == report2.by_maturity
    assert report1.missing_provenance_count == report2.missing_provenance_count
    assert report1.findings == report2.findings
    assert report1.recommendations == report2.recommendations


def test_finding_ordering_deterministic(tmp_path: Path):
    """Same state produces findings in same order."""
    store = _make_store(tmp_path)
    _make_doc(store, "cap-z")
    _make_doc(store, "cap-a", maturity="testing")
    _make_doc(store, "cap-m", maturity="stable")

    findings1 = generate_capability_health_report(store).findings
    findings2 = generate_capability_health_report(store).findings

    assert len(findings1) == len(findings2)
    for f1, f2 in zip(findings1, findings2):
        assert f1.code == f2.code
        assert f1.message == f2.message


def test_recommendation_ordering_deterministic(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "rec-order", maturity="broken")

    recs1 = generate_capability_health_report(store).recommendations
    recs2 = generate_capability_health_report(store).recommendations
    assert recs1 == recs2


# ── Recommendation serialization determinism ──


def test_recommendations_serialize_deterministically(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "ser-rec")

    report = generate_capability_health_report(store)
    d1 = report.to_dict()
    d2 = report.to_dict()
    assert d1 == d2


def test_report_to_dict_serializable(tmp_path: Path):
    store = _make_store(tmp_path)
    _make_doc(store, "serial-cap")

    report = generate_capability_health_report(store)
    d = report.to_dict()
    assert isinstance(d, dict)
    assert "generated_at" in d
    assert "findings" in d
    assert "recommendations" in d
    # Should be JSON-serializable
    json.dumps(d, default=str)
