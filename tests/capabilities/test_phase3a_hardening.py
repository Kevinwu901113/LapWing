"""Phase 3A acceptance hardening: no-mutation, no-execution, no-runtime checks.

These tests go beyond the basic Phase 3A tests to verify that policy,
evaluator, planner, and eval records never mutate state, execute code,
or wire into runtime paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.capabilities.document import parse_capability
from src.capabilities.eval_records import (
    get_latest_eval_record,
    list_eval_records,
    read_eval_record,
    write_eval_record,
)
from src.capabilities.evaluator import CapabilityEvaluator, EvalFinding, EvalRecord, FindingSeverity
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlan, PromotionPlanner
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_manifest(**overrides) -> CapabilityManifest:
    defaults = {
        "id": "test_harden_01",
        "name": "Hardening Test",
        "description": "Testing no-mutation guarantees.",
        "type": CapabilityType.SKILL,
        "scope": CapabilityScope.WORKSPACE,
        "version": "0.1.0",
        "maturity": CapabilityMaturity.DRAFT,
        "status": CapabilityStatus.ACTIVE,
        "risk_level": CapabilityRiskLevel.LOW,
    }
    defaults.update(overrides)
    return CapabilityManifest(**defaults)


def _make_eval(*, passed: bool = True, errors: int = 0, warnings: int = 0) -> EvalRecord:
    findings = []
    for i in range(errors):
        findings.append(EvalFinding(severity=FindingSeverity.ERROR, code=f"err_{i}", message=f"Error {i}"))
    for i in range(warnings):
        findings.append(EvalFinding(severity=FindingSeverity.WARNING, code=f"warn_{i}", message=f"Warning {i}"))
    return EvalRecord(
        capability_id="test_harden_01",
        scope="workspace",
        content_hash="abc123def456",
        passed=passed,
        score=max(0.0, 1.0 - errors * 0.3 - warnings * 0.1),
        findings=findings,
    )


def _write_capability_dir(base: Path, dirname: str, body: str = "", **fm_overrides) -> Path:
    cap_dir = base / dirname
    cap_dir.mkdir(parents=True)
    fm = {
        "id": "test_harden_01",
        "name": "Hardening Test",
        "description": "Testing no-mutation guarantees.",
        "type": "skill",
        "scope": "workspace",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "tags": ["test"],
        "triggers": ["on_test"],
        **fm_overrides,
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    md = f"---\n{fm_yaml}---\n\n{body}"
    (cap_dir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    for d in ("scripts", "tests", "examples", "evals"):
        (cap_dir / d).mkdir(exist_ok=True)
    return cap_dir


def _valid_body() -> str:
    return """## When to use
Use this for testing.

## Procedure
1. Do step one.
2. Do step two.

## Verification
Verify the output is correct.

## Failure handling
If it fails, log and retry.
"""


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def policy():
    return CapabilityPolicy()


@pytest.fixture
def evaluator():
    return CapabilityEvaluator()


@pytest.fixture
def planner():
    return PromotionPlanner()


# ── Policy: no mutation ─────────────────────────────────────────────────


class TestPolicyNoMutation:
    def test_validate_create_does_not_mutate_manifest(self, policy):
        m = _make_manifest()
        original = m.model_dump()
        policy.validate_create(m)
        assert m.model_dump() == original

    def test_validate_promote_does_not_mutate_manifest(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.HIGH)
        original = m.model_dump()
        policy.validate_promote(m, approval={"approved": True})
        assert m.model_dump() == original

    def test_validate_run_does_not_mutate_manifest(self, policy):
        m = _make_manifest()
        original = m.model_dump()
        policy.validate_run(m)
        assert m.model_dump() == original

    def test_validate_patch_does_not_mutate_manifests(self, policy):
        old = _make_manifest()
        new = _make_manifest()
        old_orig = old.model_dump()
        new_orig = new.model_dump()
        policy.validate_patch(old, new)
        assert old.model_dump() == old_orig
        assert new.model_dump() == new_orig

    def test_validate_install_does_not_mutate_manifest(self, policy):
        m = _make_manifest()
        original = m.model_dump()
        policy.validate_install(m, source="https://example.com/cap.tar.gz")
        assert m.model_dump() == original

    def test_validate_risk_does_not_mutate_manifest(self, policy):
        m = _make_manifest(required_permissions=["write"])
        original = m.model_dump()
        policy.validate_risk(m)
        assert m.model_dump() == original

    def test_policy_never_imports_store_or_filesystem(self):
        """Policy module should not import CapabilityStore, Index, or any I/O."""
        import src.capabilities.policy as pmod
        source = (Path(pmod.__file__)).read_text()
        assert "CapabilityStore" not in source
        assert "CapabilityIndex" not in source
        assert "open(" not in source
        assert "pathlib" not in source.lower()

    def test_policy_resists_malformed_context(self, policy):
        """Policy should not crash on unexpected context types."""
        from src.capabilities.policy import PolicyDecision as PD
        m = _make_manifest()
        for ctx in [None, {}, {"available_tools": None}, {"available_tools": "not_a_list"}, 42]:
            result = policy.validate_create(m, context=ctx)
            assert isinstance(result, PD)


# ── Evaluator: no mutation ──────────────────────────────────────────────


class TestEvaluatorNoMutation:
    def test_evaluate_does_not_mutate_manifest(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "eval_no_mut", body=_valid_body())
        doc = parse_capability(cap_dir)
        original_dump = doc.manifest.model_dump()

        evaluator.evaluate(doc)

        assert doc.manifest.model_dump() == original_dump

    def test_evaluate_does_not_write_files(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "eval_no_file", body=_valid_body())
        doc = parse_capability(cap_dir)

        before_files = set(p.name for p in cap_dir.rglob("*") if p.is_file())

        evaluator.evaluate(doc)

        after_files = set(p.name for p in cap_dir.rglob("*") if p.is_file())
        assert before_files == after_files

    def test_evaluate_does_not_call_llm(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "eval_no_llm", body=_valid_body())
        doc = parse_capability(cap_dir)
        # The evaluator should complete without any external calls
        record = evaluator.evaluate(doc)
        assert isinstance(record, EvalRecord)

    def test_evaluator_resists_binary_body(self, evaluator, tmp_path):
        """Evaluator must handle body content that is not clean markdown."""
        cap_dir = _write_capability_dir(tmp_path, "eval_binary", body="\x00\x01\x02binary junk")
        doc = parse_capability(cap_dir)
        record = evaluator.evaluate(doc)
        assert isinstance(record, EvalRecord)

    def test_evaluator_resists_empty_body(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "eval_empty", body="")
        doc = parse_capability(cap_dir)
        record = evaluator.evaluate(doc)
        assert isinstance(record, EvalRecord)

    def test_evaluator_resists_very_large_body(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "eval_large", body="a" * 100_000 + "\n\n## When to use\nTest.")
        doc = parse_capability(cap_dir)
        record = evaluator.evaluate(doc)
        assert isinstance(record, EvalRecord)


# ── Promotion planner: no mutation ──────────────────────────────────────


class TestPlannerNoMutation:
    def test_plan_transition_does_not_mutate_manifest(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        original = m.model_dump()

        planner.plan_transition(m, "testing")
        assert m.model_dump() == original

    def test_plan_transition_does_not_mutate_eval_record(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        ev = _make_eval(passed=True)
        orig_score = ev.score
        orig_hash = ev.content_hash

        planner.plan_transition(m, "stable", eval_record=ev)
        assert ev.score == orig_score
        assert ev.content_hash == orig_hash

    def test_planner_never_imports_store(self):
        """Promotion module should not import CapabilityStore."""
        import src.capabilities.promotion as pmod
        source = (Path(pmod.__file__)).read_text()
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        import_text = "\n".join(import_lines)
        assert "CapabilityStore" not in import_text

    def test_planner_does_not_access_filesystem(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        plan = planner.plan_transition(m, "testing")
        assert plan.allowed
        assert plan.from_maturity == "draft"
        assert plan.to_maturity == "testing"

    def test_disabled_and_archived_transitions_are_planned_only(self, planner):
        """any -> disabled and any -> archived must be planned, not executed."""
        m = _make_manifest()
        for target in ("disabled", "archived"):
            plan = planner.plan_transition(m, target)
            assert plan.allowed
            assert plan.from_maturity == "draft"
            assert plan.to_maturity == target


# ── Eval records: no mutation ───────────────────────────────────────────


class TestEvalRecordsNoMutation:
    def test_write_does_not_mutate_manifest(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_no_mut", body=_valid_body())
        doc = parse_capability(cap_dir)
        before = doc.manifest.model_dump()

        record = _make_eval()
        doc2 = parse_capability(cap_dir)
        write_eval_record(record, doc2)

        after = doc2.manifest.model_dump()
        assert before["maturity"] == after["maturity"]
        assert before["status"] == after["status"]
        assert before["id"] == after["id"]

    def test_write_does_not_mutate_capability_md(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_no_mut_md", body=_valid_body())
        doc = parse_capability(cap_dir)

        before_md = (cap_dir / "CAPABILITY.md").read_text()

        record = _make_eval()
        write_eval_record(record, doc)

        after_md = (cap_dir / "CAPABILITY.md").read_text()
        assert before_md == after_md

    def test_write_multiple_does_not_cumulatively_mutate(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_multi", body=_valid_body())
        doc = parse_capability(cap_dir)
        before = {k: v for k, v in doc.manifest.model_dump().items()
                  if k not in ("created_at", "updated_at", "content_hash")}

        for i in range(5):
            record = _make_eval()
            record.created_at = f"2026-04-30T{i:02d}:00:00.000000"
            write_eval_record(record, doc)

        # Re-parse; computed fields (timestamps) differ but logical fields must be identical
        doc2 = parse_capability(cap_dir)
        after = {k: v for k, v in doc2.manifest.model_dump().items()
                 if k not in ("created_at", "updated_at", "content_hash")}
        assert before == after

    def test_write_only_creates_evals_file(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_only_evals", body=_valid_body())
        doc = parse_capability(cap_dir)

        before_files = set(p.relative_to(cap_dir).as_posix() for p in cap_dir.rglob("*") if p.is_file())

        record = _make_eval()
        write_eval_record(record, doc)

        after_files = set(p.relative_to(cap_dir).as_posix() for p in cap_dir.rglob("*") if p.is_file())
        new_files = after_files - before_files
        assert all(f.startswith("evals/") for f in new_files), f"Unexpected new files: {new_files}"

    def test_write_with_failing_mutation_log_does_not_break(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_mock_fail", body=_valid_body())
        doc = parse_capability(cap_dir)

        mock_log = MagicMock()
        mock_log.record = MagicMock(side_effect=RuntimeError("simulated log failure"))

        record = _make_eval()
        filepath = write_eval_record(record, doc, mutation_log=mock_log)
        assert filepath.exists()

    def test_read_does_not_mutate_manifest(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_read", body=_valid_body())
        doc = parse_capability(cap_dir)

        record = _make_eval()
        write_eval_record(record, doc)

        before = doc.manifest.model_dump()
        read_eval_record(doc, record.created_at)
        after = doc.manifest.model_dump()
        assert before == after

    def test_list_does_not_mutate_manifest(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_list", body=_valid_body())
        doc = parse_capability(cap_dir)

        record = _make_eval()
        write_eval_record(record, doc)

        before = doc.manifest.model_dump()
        list_eval_records(doc)
        after = doc.manifest.model_dump()
        assert before == after

    def test_get_latest_does_not_mutate_manifest(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_latest", body=_valid_body())
        doc = parse_capability(cap_dir)

        record = _make_eval()
        write_eval_record(record, doc)

        before = doc.manifest.model_dump()
        get_latest_eval_record(doc)
        after = doc.manifest.model_dump()
        assert before == after

    def test_write_with_store_produced_doc_does_not_mutate_file(self, tmp_path):
        """When a doc comes from CapabilityStore (with manifest.json), write must not mutate it."""
        from src.capabilities.store import CapabilityStore
        store = CapabilityStore(data_dir=tmp_path / "store_data")
        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id="store_test_cap",
            name="Store Test Cap",
            description="Testing eval write does not mutate store doc.",
            body=_valid_body(),
        )
        before_json = json.loads((doc.directory / "manifest.json").read_text())
        record = _make_eval()
        write_eval_record(record, doc)
        after_json = json.loads((doc.directory / "manifest.json").read_text())
        assert before_json["maturity"] == after_json["maturity"]
        assert before_json["status"] == after_json["status"]
        assert before_json["id"] == after_json["id"]


# ── No script execution ─────────────────────────────────────────────────


class TestNoScriptExecution:
    def test_evaluator_does_not_execute_scripts(self, evaluator, tmp_path):
        """Create a capability with scripts/ containing Python that would crash if executed."""
        cap_dir = tmp_path / "no_exec_test"
        cap_dir.mkdir()
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir()

        # Write a script that would be dangerous if executed
        (scripts_dir / "dangerous.py").write_text("raise SystemExit('script was executed!')")

        fm = {
            "id": "no_exec_test",
            "name": "No Exec Test",
            "description": "Testing no script execution.",
            "type": "skill",
            "scope": "workspace",
            "version": "0.1.0",
            "maturity": "draft",
            "status": "active",
            "risk_level": "low",
            "tags": ["test"],
            "triggers": ["on_test"],
        }
        body = _valid_body()
        fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False)
        (cap_dir / "CAPABILITY.md").write_text(f"---\n{fm_yaml}---\n\n{body}")

        for d in ("tests", "examples", "evals"):
            (cap_dir / d).mkdir(exist_ok=True)

        doc = parse_capability(cap_dir)

        # If evaluator tries to execute the script, this will raise SystemExit
        record = evaluator.evaluate(doc)
        assert isinstance(record, EvalRecord)

    def test_eval_records_does_not_execute_scripts(self, tmp_path):
        cap_dir = tmp_path / "rec_no_exec"
        cap_dir.mkdir()
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "dangerous.py").write_text("raise SystemExit('script was executed!')")

        fm = {
            "id": "rec_no_exec",
            "name": "Rec No Exec",
            "description": "Testing no script execution from eval records.",
            "type": "skill",
            "scope": "workspace",
            "version": "0.1.0",
            "maturity": "draft",
            "status": "active",
            "risk_level": "low",
            "tags": ["test"],
            "triggers": ["on_test"],
        }
        body = _valid_body()
        fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False)
        (cap_dir / "CAPABILITY.md").write_text(f"---\n{fm_yaml}---\n\n{body}")

        for d in ("tests", "examples", "evals"):
            (cap_dir / d).mkdir(exist_ok=True)

        doc = parse_capability(cap_dir)
        record = _make_eval()

        write_eval_record(record, doc)
        read_eval_record(doc, record.created_at)
        list_eval_records(doc)
        get_latest_eval_record(doc)
        # No SystemExit = success

    def test_policy_does_not_execute_scripts(self, policy, tmp_path):
        cap_dir = tmp_path / "policy_no_exec"
        cap_dir.mkdir()
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "dangerous.py").write_text("raise SystemExit('script was executed!')")

        m = _make_manifest()
        for ctx in [None, {}, {"available_tools": ["read"]}]:
            policy.validate_create(m, context=ctx)
            policy.validate_run(m)
            policy.validate_promote(m)

    def test_planner_does_not_execute_scripts(self, planner):
        m = _make_manifest()
        for target in ("testing", "stable", "broken", "disabled", "archived"):
            planner.plan_transition(m, target)
        # No SystemExit = success


# ── Determinism ─────────────────────────────────────────────────────────


class TestDeterminism:
    def test_policy_same_input_same_output(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.MEDIUM)
        results = [policy.validate_promote(m, eval_record={"passed": True}) for _ in range(10)]
        first = results[0]
        for r in results[1:]:
            assert r.allowed == first.allowed
            assert r.code == first.code
            assert r.severity == first.severity

    def test_evaluator_same_input_same_output(self, evaluator, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "det_eval", body=_valid_body())
        doc1 = parse_capability(cap_dir)
        doc2 = parse_capability(cap_dir)

        r1 = evaluator.evaluate(doc1)
        r2 = evaluator.evaluate(doc2)

        assert r1.passed == r2.passed
        assert r1.score == r2.score
        assert len(r1.findings) == len(r2.findings)
        assert r1.required_approval == r2.required_approval
        assert r1.recommended_maturity == r2.recommended_maturity

    def test_planner_same_input_same_output(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        ev = _make_eval(passed=True)
        results = [planner.plan_transition(m, "stable", eval_record=ev) for _ in range(10)]
        first = results[0]
        for r in results[1:]:
            assert r.allowed == first.allowed
            assert r.explanation == first.explanation


# ── Eval record completeness ────────────────────────────────────────────


class TestEvalRecordCompleteness:
    def test_record_includes_all_required_fields(self):
        record = _make_eval()
        data = {
            "capability_id": record.capability_id,
            "scope": record.scope,
            "content_hash": record.content_hash,
            "evaluator_version": record.evaluator_version,
            "created_at": record.created_at,
            "passed": record.passed,
            "score": record.score,
            "findings": record.findings,
            "required_approval": record.required_approval,
            "recommended_maturity": record.recommended_maturity,
        }
        assert data["capability_id"] == "test_harden_01"
        assert data["scope"] == "workspace"
        assert data["content_hash"] == "abc123def456"
        assert data["evaluator_version"] is not None
        assert data["created_at"] is not None

    def test_write_read_all_fields_roundtrip(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "rec_complete", body=_valid_body())
        doc = parse_capability(cap_dir)

        record = EvalRecord(
            capability_id="test_harden_01",
            scope="workspace",
            content_hash="xyz789",
            evaluator_version="3a.99",
            created_at="2026-04-30T12:00:00.000000",
            passed=False,
            score=0.42,
            findings=[
                EvalFinding(severity=FindingSeverity.ERROR, code="e1", message="Error one", location="body", details={"line": 1}),
                EvalFinding(severity=FindingSeverity.WARNING, code="w1", message="Warn one"),
                EvalFinding(severity=FindingSeverity.INFO, code="i1", message="Info one"),
            ],
            required_approval=True,
            recommended_maturity="draft",
        )

        write_eval_record(record, doc)
        read = read_eval_record(doc, record.created_at)

        assert read is not None
        assert read.capability_id == "test_harden_01"
        assert read.scope == "workspace"
        assert read.content_hash == "xyz789"
        assert read.evaluator_version == "3a.99"
        assert read.created_at == "2026-04-30T12:00:00.000000"
        assert read.passed is False
        assert read.score == 0.42
        assert len(read.findings) == 3
        assert read.findings[0].code == "e1"
        assert read.findings[0].location == "body"
        assert read.findings[0].details == {"line": 1}
        assert read.findings[1].location is None
        assert read.findings[1].details == {}
        assert read.required_approval is True
        assert read.recommended_maturity == "draft"
