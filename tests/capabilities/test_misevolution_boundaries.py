"""Misevolution hardening contract tests for capability boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import parse_capability
from src.capabilities.errors import InvalidEnumValueError
from src.capabilities.eval_records import (
    get_latest_valid_eval_record,
    write_eval_record,
)
from src.capabilities.evaluator import (
    EVAL_SCHEMA_VERSION,
    EVALUATOR_VERSION,
    CapabilityEvaluator,
    EvalRecord,
    FindingSeverity,
)
from src.capabilities.retriever import CapabilityRetriever, RetrievalContext
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.eval.axes import AxisResult, AxisStatus, EvalAxis
from src.core.state_serializer import serialize
from src.core.state_view import (
    AttentionContext,
    CapabilitySummary,
    IdentityDocs,
    MemorySnippets,
    StateView,
    TrajectoryWindow,
)


def _write_capability(base: Path, *, risk_level: str = "low", **overrides) -> Path:
    cap_dir = base / "cap"
    cap_dir.mkdir(parents=True)
    fm = {
        "id": "boundary_cap_01",
        "name": "Boundary Cap",
        "description": "A capability used for boundary tests.",
        "type": "skill",
        "scope": "workspace",
        "version": "0.1.0",
        "maturity": "testing",
        "status": "active",
        "risk_level": risk_level,
        "tags": ["test"],
        "triggers": ["boundary"],
        **overrides,
    }
    body = """## When to use

Use for boundary tests.

## Procedure

Do the safe thing.

## Verification

Check the output.

## Failure handling

Stop and report the failure.
"""
    (cap_dir / "CAPABILITY.md").write_text(
        f"---\n{yaml.dump(fm, sort_keys=False)}---\n\n{body}",
        encoding="utf-8",
    )
    (cap_dir / "evals").mkdir()
    return cap_dir


def test_manifest_boundary_fields_parse_and_round_trip(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        do_not_apply_when=["when user asks for legal advice"],
        sensitive_contexts=["legal", "private_project"],
        reuse_boundary="Only for local planning.",
        required_preflight_checks=["owner_confirmed_scope"],
        side_effects=["none"],
        rollback_available=None,
        rollback_mechanism=None,
    ))

    data = doc.manifest.model_dump(mode="json")
    assert data["do_not_apply_when"] == ["when user asks for legal advice"]
    assert data["sensitive_contexts"] == ["legal", "private_project"]
    assert data["side_effects"] == ["none"]
    assert data["rollback_available"] is None


def test_invalid_boundary_enum_raises_clear_error(tmp_path):
    cap_dir = _write_capability(tmp_path, side_effects=["telepathy"])
    with pytest.raises(InvalidEnumValueError, match="side_effects"):
        parse_capability(cap_dir)


def test_evaluator_preserves_unknown_vs_none_side_effects(tmp_path):
    unknown = parse_capability(_write_capability(
        tmp_path / "unknown",
        side_effects=[],
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
    ))
    explicit_none = parse_capability(_write_capability(
        tmp_path / "none",
        side_effects=["none"],
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
    ))

    unknown_codes = {f.code for f in CapabilityEvaluator().evaluate(unknown).findings}
    none_codes = {f.code for f in CapabilityEvaluator().evaluate(explicit_none).findings}
    assert "unknown_side_effects" in unknown_codes
    assert "unknown_side_effects" not in none_codes


def test_evaluator_rejects_none_combined_with_other_side_effect(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        side_effects=["none", "local_write"],
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
    ))
    record = CapabilityEvaluator().evaluate(doc)
    assert any(f.code == "invalid_side_effect_none_combination" for f in record.findings)
    assert not record.passed


def test_evaluator_requires_medium_high_boundary_fields(tmp_path):
    doc = parse_capability(_write_capability(tmp_path, risk_level="medium", side_effects=["none"]))
    record = CapabilityEvaluator().evaluate(doc)
    errors = {f.code for f in record.findings if f.severity == FindingSeverity.ERROR}
    assert "missing_boundary" in errors


def test_latest_valid_eval_record_refuses_old_evaluator_fallback(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        side_effects=["none"],
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
    ))
    old = EvalRecord(
        capability_id=doc.id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        evaluator_version="old",
        schema_version=EVAL_SCHEMA_VERSION,
        passed=True,
    )
    current = EvalRecord(
        capability_id=doc.id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        evaluator_version=EVALUATOR_VERSION,
        schema_version=EVAL_SCHEMA_VERSION,
        passed=True,
    )
    write_eval_record(old, doc)
    assert get_latest_valid_eval_record(doc) is None
    write_eval_record(current, doc)
    assert get_latest_valid_eval_record(doc).evaluator_version == EVALUATOR_VERSION


def test_retriever_filters_sensitive_context_without_approval():
    class Index:
        def search(self, query, filters, limit):
            return [{
                "id": "cap",
                "name": "Cap",
                "description": "Sensitive cap",
                "type": "skill",
                "scope": "workspace",
                "maturity": "stable",
                "status": "active",
                "risk_level": "low",
                "trust_required": "developer",
                "triggers_json": json.dumps([]),
                "tags_json": json.dumps([]),
                "required_tools_json": json.dumps([]),
                "sensitive_contexts_json": json.dumps(["credentials"]),
                "do_not_apply_when_json": json.dumps([]),
            }]

    retriever = CapabilityRetriever(store=object(), index=Index())
    blocked = retriever.retrieve(
        "cap",
        RetrievalContext(sensitive_contexts={"credentials"}),
    )
    allowed = retriever.retrieve(
        "cap",
        RetrievalContext(
            sensitive_contexts={"credentials"},
            approved_sensitive_contexts={"credentials"},
        ),
    )
    assert blocked == []
    assert len(allowed) == 1


def test_state_view_renders_boundary_fields_as_empty_lists():
    from datetime import datetime, timezone

    state = StateView(
        identity_docs=IdentityDocs(soul="", constitution="", voice=""),
        attention_context=AttentionContext(
            channel="desktop",
            actor_id=None,
            actor_name=None,
            auth_level=3,
            group_id=None,
            current_conversation=None,
            mode="conversing",
            now=datetime(2026, 5, 6, tzinfo=timezone.utc),
            offline_hours=None,
        ),
        trajectory_window=TrajectoryWindow(turns=()),
        memory_snippets=MemorySnippets(snippets=()),
        commitments_active=(),
        capability_summaries=(
            CapabilitySummary(
                id="cap",
                name="Cap",
                description="Desc",
                type="skill",
                scope="workspace",
                maturity="stable",
                risk_level="low",
            ),
        ),
    )
    prompt = serialize(state).system_prompt
    assert "do_not_apply_when: []" in prompt
    assert "sensitive_contexts: []" in prompt


def test_eval_axes_module_is_minimal():
    import src.eval.axes as axes

    assert hasattr(axes, "EvalAxis")
    assert hasattr(axes, "AxisStatus")
    assert hasattr(axes, "AxisResult")
    with pytest.raises(ModuleNotFoundError):
        __import__("src.eval.runner")


def test_evaluator_returns_axis_results(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
        side_effects=["none"],
    ))
    (doc.directory / "evals" / "boundary_cases.jsonl").write_text('{"case":"x"}\n', encoding="utf-8")
    (doc.directory / "evals" / "positive_cases.jsonl").write_text('{"case":"ok"}\n', encoding="utf-8")
    record = CapabilityEvaluator().evaluate(doc)
    assert record.axes[EvalAxis.FUNCTIONAL.value].status == AxisStatus.PASS
    assert record.axes[EvalAxis.SAFETY.value].status == AxisStatus.PASS


def test_old_passed_record_reads_with_unknown_safety_axes(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        do_not_apply_when=["not for writes"],
        reuse_boundary="Read-only only.",
        side_effects=["none"],
    ))
    record = EvalRecord(
        capability_id=doc.id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        evaluator_version=EVALUATOR_VERSION,
        schema_version=EVAL_SCHEMA_VERSION,
        passed=True,
        axes={},
    )
    write_eval_record(record, doc)
    read = get_latest_valid_eval_record(doc)
    assert read.axes[EvalAxis.FUNCTIONAL.value].status == AxisStatus.PASS
    assert read.axes[EvalAxis.SAFETY.value].status == AxisStatus.UNKNOWN


def test_testing_to_stable_requires_required_axis_pass():
    manifest = CapabilityManifest(
        id="cap",
        name="Cap",
        description="Desc",
        type=CapabilityType.SKILL,
        scope=CapabilityScope.WORKSPACE,
        version="0.1.0",
        maturity=CapabilityMaturity.TESTING,
        status=CapabilityStatus.ACTIVE,
        risk_level=CapabilityRiskLevel.LOW,
        side_effects=["none"],
    )
    record = EvalRecord(
        capability_id="cap",
        scope="workspace",
        content_hash="hash",
        passed=True,
        axes={
            EvalAxis.FUNCTIONAL.value: AxisResult(EvalAxis.FUNCTIONAL, AxisStatus.PASS),
            EvalAxis.SAFETY.value: AxisResult(EvalAxis.SAFETY, AxisStatus.UNKNOWN),
        },
    )
    plan = PromotionPlanner().plan_transition(manifest, "stable", eval_record=record)
    assert not plan.allowed
    assert plan.blocking_findings[0]["axis"] == "safety"


def test_evaluator_scans_executable_script_destructive_patterns(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        entry_type="executable_script",
        entry_script="scripts/main.sh",
        do_not_apply_when=["not for writes"],
        reuse_boundary="Only with explicit approval.",
        side_effects=["local_delete"],
        rollback_available=True,
        rollback_mechanism="backup_file",
    ))
    script = doc.directory / "scripts" / "main.sh"
    script.parent.mkdir()
    script.write_text("rm -rf /tmp/example\n", encoding="utf-8")
    (doc.directory / "evals" / "rollback_cases.jsonl").write_text('{"case":"rollback"}\n', encoding="utf-8")

    record = CapabilityEvaluator().evaluate(doc)

    assert any(f.code == "script_destructive_pattern" for f in record.findings)


def test_evaluator_scans_executable_script_secret_literals(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        entry_type="executable_script",
        entry_script="scripts/main.py",
        do_not_apply_when=["not for credentials"],
        reuse_boundary="Only in credential-safe contexts.",
        side_effects=["credential_access"],
    ))
    script = doc.directory / "scripts" / "main.py"
    script.parent.mkdir()
    script.write_text('api_key = "abc123"\ndef run():\n    return {}\n', encoding="utf-8")

    record = CapabilityEvaluator().evaluate(doc)

    assert any(f.code == "script_secret_literal" for f in record.findings)


def test_evaluator_rejects_script_detected_side_effect_not_declared(tmp_path):
    doc = parse_capability(_write_capability(
        tmp_path,
        entry_type="executable_script",
        entry_script="scripts/main.py",
        do_not_apply_when=["not for network"],
        reuse_boundary="Read-only only.",
        side_effects=["none"],
    ))
    script = doc.directory / "scripts" / "main.py"
    script.parent.mkdir()
    script.write_text('import requests\ndef run():\n    requests.post("https://example.com")\n', encoding="utf-8")

    record = CapabilityEvaluator().evaluate(doc)

    assert any(f.code == "script_undeclared_side_effects" for f in record.findings)
    assert not record.passed


def test_evaluator_rejects_absolute_and_traversal_entry_scripts(tmp_path):
    absolute = parse_capability(_write_capability(
        tmp_path / "absolute",
        entry_type="executable_script",
        entry_script="/tmp/main.py",
        do_not_apply_when=["not for scripts"],
        reuse_boundary="Only relative entrypoints.",
        side_effects=["none"],
    ))
    traversal = parse_capability(_write_capability(
        tmp_path / "traversal",
        entry_type="executable_script",
        entry_script="../main.py",
        do_not_apply_when=["not for scripts"],
        reuse_boundary="Only contained entrypoints.",
        side_effects=["none"],
    ))

    absolute_codes = {f.code for f in CapabilityEvaluator().evaluate(absolute).findings}
    traversal_codes = {f.code for f in CapabilityEvaluator().evaluate(traversal).findings}

    assert "absolute_entry_script" in absolute_codes
    assert "entry_script_path_traversal" in traversal_codes
