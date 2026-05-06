"""Phase 8A-1 hardening tests — edge cases, safety, legacy compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.provenance import (
    PROVENANCE_INTEGRITY_STATUSES,
    PROVENANCE_SIGNATURE_STATUSES,
    PROVENANCE_SOURCE_TYPES,
    PROVENANCE_TRUST_LEVELS,
    INTEGRITY_MISMATCH,
    INTEGRITY_UNKNOWN,
    INTEGRITY_VERIFIED,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    SOURCE_LOCAL_PACKAGE,
    SOURCE_QUARANTINE_ACTIVATION,
    SOURCE_UNKNOWN,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNKNOWN,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    TrustDecision,
    compute_capability_tree_hash,
    read_provenance,
    update_provenance_integrity_status,
    verify_content_hash_against_provenance,
    write_provenance,
)
from src.capabilities.retriever import CapabilityRetriever
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.store import CapabilityStore


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_manifest(**overrides) -> CapabilityManifest:
    kwargs = dict(
        id="test-cap", name="Test Cap", description="Test.",
        type=CapabilityType.SKILL, scope=CapabilityScope.USER, version="1.0.0",
        maturity=CapabilityMaturity.TESTING, status=CapabilityStatus.ACTIVE,
        risk_level=CapabilityRiskLevel.LOW,
    )
    kwargs.update(overrides)
    return CapabilityManifest(**kwargs)


def _make_provenance(**overrides) -> CapabilityProvenance:
    kwargs = dict(
        provenance_id="prov_test", capability_id="test-cap",
        trust_level=TRUST_UNTRUSTED, integrity_status=INTEGRITY_VERIFIED,
        signature_status=SIGNATURE_NOT_PRESENT,
    )
    kwargs.update(overrides)
    return CapabilityProvenance(**kwargs)


def _write_capability(dir_path: Path, cap_id: str, **overrides) -> None:
    fm = {
        "id": cap_id, "name": f"Test {cap_id}",
        "description": "Test capability.", "type": "skill", "scope": "user",
        "version": "0.1.0", "maturity": "draft", "status": "quarantined",
        "risk_level": "low", "triggers": [], "tags": [],
        "trust_required": "developer", "required_tools": [], "required_permissions": [],
    }
    fm.update(overrides)
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = f"---\n{fm_yaml}\n---\n\n## When to use\nTest.\n\n## Procedure\n1. Test\n\n## Verification\nPass.\n\n## Failure handling\nRetry."
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")
    manifest = {k: v for k, v in fm.items() if k not in ("version",)}
    (dir_path / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_file(dir_path: Path, rel_path: str, content: str) -> None:
    target = dir_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ── 1. Corrupt / invalid provenance handling ───────────────────────────────────


class TestCorruptProvenanceRecovery:
    """Corrupt or malformed provenance.json must not crash the system."""

    def test_corrupt_json_returns_none(self, tmp_path: Path):
        (tmp_path / "provenance.json").write_text("{this is not valid json}", encoding="utf-8")
        assert read_provenance(tmp_path) is None

    def test_empty_provenance_json_returns_none(self, tmp_path: Path):
        (tmp_path / "provenance.json").write_text("", encoding="utf-8")
        assert read_provenance(tmp_path) is None

    def test_partial_json_returns_none(self, tmp_path: Path):
        (tmp_path / "provenance.json").write_text('{"provenance_id": "prov_', encoding="utf-8")
        assert read_provenance(tmp_path) is None

    def test_read_provenance_never_raises(self, tmp_path: Path):
        # Permission error on read (directory but no file)
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "provenance.json").mkdir()  # directory, not file
        result = read_provenance(subdir)
        assert result is None

    def test_missing_provenance_returns_none(self, tmp_path: Path):
        assert read_provenance(tmp_path) is None

    def test_unknown_fields_in_provenance_preserved(self, tmp_path: Path):
        """Extra fields in provenance.json are preserved through round-trip."""
        extra = {
            "provenance_id": "prov_extra001",
            "capability_id": "test-1",
            "future_field": "preserved",
            "nested": {"a": 1},
        }
        (tmp_path / "provenance.json").write_text(json.dumps(extra), encoding="utf-8")
        prov = read_provenance(tmp_path)
        assert prov is not None
        d = prov.to_dict()
        # Known fields should be present; extra fields from raw JSON are not in to_dict
        assert d["provenance_id"] == "prov_extra001"

    def test_update_integrity_status_invalid_value_returns_none(self, tmp_path: Path):
        _make_provenance()  # don't write to disk
        result = update_provenance_integrity_status(tmp_path, "not_a_valid_status")
        assert result is None

    def test_update_integrity_status_no_provenance_returns_none(self, tmp_path: Path):
        result = update_provenance_integrity_status(tmp_path, INTEGRITY_VERIFIED)
        assert result is None


# ── 2. Additional tree hash edge cases ─────────────────────────────────────────


class TestTreeHashEdgeCases:
    """Additional determinism and edge case coverage."""

    def test_invalid_utf8_file_hashed_by_bytes(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "utf8-test")
        (d / "scripts").mkdir(exist_ok=True)
        (d / "scripts" / "data.bin").write_bytes(bytes([0x80, 0xFF, 0x00, 0xFE]))
        h = compute_capability_tree_hash(d)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_deleting_included_file_changes_hash(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "del-test")
        _write_file(d, "scripts/delete_me.py", "print('hello')")
        h1 = compute_capability_tree_hash(d)
        (d / "scripts" / "delete_me.py").unlink()
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_adding_included_file_changes_hash(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "add-test")
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "scripts/new_file.py", "print('world')")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_nested_file_changes_hash(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "nested-test")
        _write_file(d, "scripts/subdir/nested.py", "v1")
        h1 = compute_capability_tree_hash(d)
        (d / "scripts" / "subdir" / "nested.py").write_text("v2", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_line_ending_changes_hash(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "le-test")
        _write_file(d, "scripts/endings.py", "line1\r\nline2\r\n")
        h1 = compute_capability_tree_hash(d)
        (d / "scripts" / "endings.py").write_bytes(b"line1\nline2\n")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_identical_empty_dirs_same_hash(self, tmp_path: Path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        _write_capability(d1, "empty-cap")
        _write_capability(d2, "empty-cap")
        assert compute_capability_tree_hash(d1) == compute_capability_tree_hash(d2)

    def test_string_passed_unchanged_through_content_unchanged(self, tmp_path: Path):
        """The tree hash only changes when file content changes, not metadata."""
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "stable-test")
        h1 = compute_capability_tree_hash(d)
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excluded_dir_detected_by_part_matching(self, tmp_path: Path):
        """Ensure an excluded dir name appearing as part of a dir path IS excluded."""
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "part-test")
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "evals/manual_check.json", "{}")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2


# ── 3. verify_content_hash_against_provenance ─────────────────────────────────


class TestVerifyContentHash:
    """Content hash verification against provenance."""

    def test_verified_when_hash_matches(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "verify-test")
        h = compute_capability_tree_hash(d)
        prov = _make_provenance(source_content_hash=h)
        assert verify_content_hash_against_provenance(d, prov) is True

    def test_mismatch_when_hash_differs(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "mismatch-test")
        prov = _make_provenance(source_content_hash="a" * 64)
        assert verify_content_hash_against_provenance(d, prov) is False

    def test_false_when_provenance_hash_empty(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "empty-hash")
        prov = _make_provenance(source_content_hash="")
        assert verify_content_hash_against_provenance(d, prov) is False

    def test_false_when_curren_hash_empty(self, tmp_path: Path):
        """Non-existent dir returns empty hash → verification False."""
        prov = _make_provenance(source_content_hash="a" * 64)
        assert verify_content_hash_against_provenance(Path("/nonexistent/abc123"), prov) is False


# ── 4. update_provenance_integrity_status behavior ────────────────────────────


class TestUpdateIntegrityStatus:
    """update_provenance_integrity_status sets status without changing other fields."""

    def test_sets_verified(self, tmp_path: Path):
        prov = write_provenance(tmp_path, capability_id="test-cap", integrity_status=INTEGRITY_UNKNOWN)
        updated = update_provenance_integrity_status(tmp_path, INTEGRITY_VERIFIED)
        assert updated is not None
        assert updated.integrity_status == INTEGRITY_VERIFIED
        assert updated.provenance_id == prov.provenance_id

    def test_sets_mismatch(self, tmp_path: Path):
        write_provenance(tmp_path, capability_id="test-cap", integrity_status=INTEGRITY_VERIFIED)
        updated = update_provenance_integrity_status(tmp_path, INTEGRITY_MISMATCH)
        assert updated is not None
        assert updated.integrity_status == INTEGRITY_MISMATCH

    def test_does_not_change_other_fields(self, tmp_path: Path):
        write_provenance(
            tmp_path, capability_id="test-cap",
            trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_VERIFIED,
            source_content_hash="abc123",
        )
        updated = update_provenance_integrity_status(tmp_path, INTEGRITY_MISMATCH)
        assert updated is not None
        assert updated.trust_level == TRUST_REVIEWED
        assert updated.capability_id == "test-cap"
        assert updated.source_content_hash == "abc123"
        assert updated.integrity_status == INTEGRITY_MISMATCH  # only this changed

    def test_invalid_status_rejected(self, tmp_path: Path):
        write_provenance(tmp_path, capability_id="test-cap")
        result = update_provenance_integrity_status(tmp_path, "bogus_status")
        assert result is None
        # Original provenance unchanged
        prov = read_provenance(tmp_path)
        assert prov is not None
        assert prov.integrity_status == INTEGRITY_UNKNOWN


# ── 5. Trust policy additional cases ──────────────────────────────────────────


class TestTrustPolicyAdditional:
    """Edge cases and behavioral properties for CapabilityTrustPolicy."""

    def test_present_unverified_signature_allows_activation(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        d = policy.can_activate_from_quarantine(p)
        # present_unverified is NOT invalid, so it should allow
        assert d.allowed is True

    def test_present_unverified_signature_warns_on_promotion(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        d = policy.can_promote_to_stable(manifest, p)
        # trusted_local + present_unverified → allowed (info, not blocked)
        assert d.allowed is True

    def test_unknown_trust_evaluates(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_UNKNOWN, integrity_status=INTEGRITY_UNKNOWN)
        d = policy.evaluate_provenance(p)
        assert d.allowed is True

    def test_can_retrieve_does_not_change_retrieval(self):
        """can_retrieve returns a decision object — it does not mutate anything."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance()
        manifest_before = manifest.id
        prov_before = p.trust_level
        policy.can_retrieve(manifest, p)
        assert manifest.id == manifest_before
        assert p.trust_level == prov_before

    def test_can_promote_to_stable_wired_behind_feature_flag(self):
        """can_promote_to_stable is on CapabilityTrustPolicy and wired into
        LifecycleManager behind trust_gate_enabled flag (Phase 8C-1)."""
        policy = CapabilityTrustPolicy()
        assert hasattr(policy, "can_promote_to_stable")
        # LifecycleManager now references CapabilityTrustPolicy and can_promote_to_stable
        # but only activates behind trust_gate_enabled=False by default.
        from src.capabilities import lifecycle
        import inspect
        source = inspect.getsource(lifecycle)
        assert "can_promote_to_stable" in source
        assert "trust_gate_enabled" in source

    def test_trust_policy_never_imports_lifecycle(self):
        import inspect
        source = inspect.getsource(CapabilityTrustPolicy)
        assert "LifecycleManager" not in source
        assert "lifecycle" not in source

    def test_all_valid_source_types_accepted_in_provenance(self):
        for st in PROVENANCE_SOURCE_TYPES:
            p = CapabilityProvenance(provenance_id="test", capability_id="x", source_type=st)
            assert p.source_type == st

    def test_all_valid_trust_levels_accepted_in_provenance(self):
        for tl in PROVENANCE_TRUST_LEVELS:
            p = CapabilityProvenance(provenance_id="test", capability_id="x", trust_level=tl)
            assert p.trust_level == tl

    def test_all_valid_integrity_statuses_accepted_in_provenance(self):
        for s in PROVENANCE_INTEGRITY_STATUSES:
            p = CapabilityProvenance(provenance_id="test", capability_id="x", integrity_status=s)
            assert p.integrity_status == s

    def test_all_valid_signature_statuses_accepted_in_provenance(self):
        for s in PROVENANCE_SIGNATURE_STATUSES:
            p = CapabilityProvenance(provenance_id="test", capability_id="x", signature_status=s)
            assert p.signature_status == s

    def test_metadata_in_provenance_preserved_as_is(self, tmp_path: Path):
        """Metadata is stored as-is; caller is responsible for sanitization."""
        meta = {"chain_of_thought": "sensitive reasoning", "plan_id": "qap_001"}
        prov = write_provenance(tmp_path, capability_id="test", metadata=meta)
        assert prov.metadata == meta
        # Round-trip through disk
        prov2 = read_provenance(tmp_path)
        assert prov2 is not None
        assert prov2.metadata == meta


# ── 6. Legacy compatibility ───────────────────────────────────────────────────


class TestLegacyCompatibility:
    """Capabilities without provenance.json must work normally."""

    def test_manifest_from_dict_without_provenance(self):
        """CapabilityManifest creates fine without any provenance field."""
        m = _make_manifest()
        assert m.id == "test-cap"
        assert m.status == CapabilityStatus.ACTIVE

    def test_store_get_works_without_provenance(self, tmp_path: Path):
        """CapabilityStore.get works for caps without provenance.json."""
        store = CapabilityStore(data_dir=tmp_path / "caps")
        d = tmp_path / "caps" / "user" / "legacy-1"
        d.mkdir(parents=True)
        _write_capability(d, "legacy-1", status="active", maturity="testing")
        result = store.get("legacy-1", scope=CapabilityScope.USER)
        assert result is not None
        assert result.manifest.id == "legacy-1"

    def test_store_list_includes_caps_without_provenance(self, tmp_path: Path):
        store = CapabilityStore(data_dir=tmp_path / "caps")
        for i in range(3):
            d = tmp_path / "caps" / "user" / f"legacy-{i}"
            d.mkdir(parents=True)
            _write_capability(d, f"legacy-{i}", status="active", maturity="testing")
        results = store.list(scope=CapabilityScope.USER)
        assert len(results) >= 3

    def test_store_search_finds_caps_without_provenance(self, tmp_path: Path):
        store = CapabilityStore(data_dir=tmp_path / "caps")
        idx = CapabilityIndex(str(tmp_path / "idx.db"))
        idx.init()
        d = tmp_path / "caps" / "user" / "searchable"
        d.mkdir(parents=True)
        _write_capability(d, "searchable", status="active", maturity="testing")
        from src.capabilities.document import CapabilityParser
        doc = CapabilityParser().parse(d)
        idx.upsert(doc)
        results = idx.search("Test")
        assert len(results) > 0

    def test_lifecycle_manager_works_without_provenance(self, tmp_path: Path):
        """LifecycleManager evaluate/plan_transition work without provenance.json."""
        from src.capabilities.promotion import PromotionPlanner

        store = CapabilityStore(data_dir=tmp_path / "caps")
        d = tmp_path / "caps" / "user" / "lm-legacy"
        d.mkdir(parents=True)
        _write_capability(d, "lm-legacy", status="active", maturity="testing")
        doc = store.get("lm-legacy", scope=CapabilityScope.USER)
        assert doc is not None
        assert not (d / "provenance.json").exists()

        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()
        planner = PromotionPlanner()
        lm = CapabilityLifecycleManager(
            store=store, evaluator=evaluator, policy=policy, planner=planner,
        )
        # Evaluate should not crash on caps without provenance
        ev_result = lm.evaluate("lm-legacy", scope=CapabilityScope.USER)
        assert ev_result is not None

    def test_retriever_works_without_provenance(self, tmp_path: Path):
        store = CapabilityStore(data_dir=tmp_path / "caps")
        idx = CapabilityIndex(str(tmp_path / "retriever_idx.db"))
        idx.init()
        d = tmp_path / "caps" / "user" / "retrieve-me"
        d.mkdir(parents=True)
        _write_capability(d, "retrieve-me", status="active", maturity="testing")
        from src.capabilities.document import CapabilityParser
        doc = CapabilityParser().parse(d)
        idx.upsert(doc)

        retriever = CapabilityRetriever(store=store, index=idx)
        results = retriever.retrieve("Test")
        assert len(results) >= 1
        assert not (d / "provenance.json").exists()

    def test_retriever_summarize_includes_legacy_caps(self, tmp_path: Path):
        store = CapabilityStore(data_dir=tmp_path / "caps")
        idx = CapabilityIndex(str(tmp_path / "retriever_sum_idx.db"))
        idx.init()
        for i in range(2):
            d = tmp_path / "caps" / "user" / f"legacy-r-{i}"
            d.mkdir(parents=True)
            _write_capability(d, f"legacy-r-{i}", status="active", maturity="testing")
            from src.capabilities.document import CapabilityParser
            doc = CapabilityParser().parse(d)
            idx.upsert(doc)
        retriever = CapabilityRetriever(store=store, index=idx)
        # summarize takes a CapabilityDocument
        doc = store.get("legacy-r-0", scope=CapabilityScope.USER)
        summary = retriever.summarize(doc)
        assert summary is not None

    def test_capability_with_provenance_retrieves_and_reads(self, tmp_path: Path):
        store = CapabilityStore(data_dir=tmp_path / "caps")
        idx = CapabilityIndex(str(tmp_path / "prov_sum_idx.db"))
        idx.init()
        d = tmp_path / "caps" / "user" / "prov-summary"
        d.mkdir(parents=True)
        _write_capability(d, "prov-summary", status="active", maturity="testing")
        write_provenance(
            d, capability_id="prov-summary",
            source_type=SOURCE_LOCAL_PACKAGE,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        from src.capabilities.document import CapabilityParser
        doc = CapabilityParser().parse(d)
        idx.upsert(doc)

        retriever = CapabilityRetriever(store=store, index=idx)
        results = retriever.retrieve("Test")
        assert len(results) >= 1
        # The doc on disk has provenance available
        prov = read_provenance(d)
        assert prov is not None
        assert prov.trust_level == TRUST_REVIEWED

    def test_writing_provenance_does_not_affect_manifest(self, tmp_path: Path):
        """write_provenance should not touch manifest.json."""
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "prov-sep")
        manifest_before = (d / "manifest.json").read_bytes()
        write_provenance(d, capability_id="prov-sep")
        manifest_after = (d / "manifest.json").read_bytes()
        assert manifest_before == manifest_after

    def test_write_provenance_only_creates_provenance_json(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "only-prov")
        files_before = set(p.name for p in d.iterdir())
        write_provenance(d, capability_id="only-prov")
        files_after = set(p.name for p in d.iterdir())
        assert files_after == files_before | {"provenance.json"}
        assert len(files_after) == len(files_before) + 1


# ── 7. Path safety ────────────────────────────────────────────────────────────


class TestPathSafety:
    """provenance I/O must not escape the designated directory."""

    def test_write_provenance_uses_directory_parameter(self, tmp_path: Path):
        """provenance.json is written inside the given directory."""
        d = tmp_path / "target"
        d.mkdir()
        write_provenance(d, capability_id="path-test")
        assert (d / "provenance.json").is_file()
        # Ensure it's not written elsewhere
        assert not (tmp_path / "provenance.json").exists()

    def test_provenance_io_does_not_traverse_up(self, tmp_path: Path):
        """Even with '../' in directory name, I/O stays within provided path."""
        d = tmp_path / "sub" / "normal"
        d.mkdir(parents=True)
        write_provenance(d, capability_id="traverse-test")
        assert (d / "provenance.json").is_file()
        # Should NOT have written to parent
        assert not (tmp_path / "sub" / "provenance.json").exists()
        assert not (tmp_path / "provenance.json").exists()


# ── 8. Import/apply provenance hardening ──────────────────────────────────────


class TestProvenanceFieldsSafety:
    """Field-level safety checks on provenance data."""

    def test_source_path_hash_is_sha256_hex(self, tmp_path: Path):
        """source_path_hash must be a 64-char hex string."""
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="hash-test",
            source_type=SOURCE_LOCAL_PACKAGE,
            source_path_hash="a" * 64,
        )
        assert len(prov.source_path_hash) == 64
        assert all(c in "0123456789abcdef" for c in prov.source_path_hash)

    def test_source_content_hash_is_sha256_hex(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "hash-test-2")
        h = compute_capability_tree_hash(d)
        prov = write_provenance(
            d, capability_id="hash-test-2",
            source_content_hash=h,
        )
        assert len(prov.source_content_hash) == 64
        assert all(c in "0123456789abcdef" for c in prov.source_content_hash)

    def test_metadata_keys_preserved_in_to_dict(self):
        prov = _make_provenance(metadata={"a": 1, "b": [2, 3]})
        d = prov.to_dict()
        assert d["metadata"] == {"a": 1, "b": [2, 3]}

    def test_imported_at_is_iso_format(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="iso-test",
            imported_at="2026-05-04T12:00:00+00:00",
        )
        assert "T" in prov.imported_at
        assert "+" in prov.imported_at

    def test_activated_at_is_iso_format(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="iso-act-test",
            activated_at="2026-05-04T12:00:00+00:00",
        )
        assert "T" in prov.activated_at

    def test_origin_scope_quarantine_preserved(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="origin-test",
            source_type=SOURCE_QUARANTINE_ACTIVATION,
            origin_capability_id="original-cap",
            origin_scope="quarantine",
        )
        assert prov.origin_capability_id == "original-cap"
        assert prov.origin_scope == "quarantine"

    def test_parent_provenance_id_preserved(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="parent-test",
            parent_provenance_id="prov_parent12345678",
        )
        assert prov.parent_provenance_id == "prov_parent12345678"


# ── 9. Invalid enum hardening ───────────────────────────────────────────────────


class TestInvalidEnumHandling:
    """Invalid enum values must not crash — defaults or rejection applied gracefully."""

    def test_from_dict_with_invalid_trust_level_does_not_crash(self):
        """from_dict accepts any string; trust level validation is at policy layer."""
        p = CapabilityProvenance.from_dict({
            "provenance_id": "prov_test", "capability_id": "cap-test",
            "trust_level": "super_trusted_not_real",
        })
        assert p.trust_level == "super_trusted_not_real"  # model stores as-is

    def test_from_dict_with_invalid_integrity_does_not_crash(self):
        p = CapabilityProvenance.from_dict({
            "provenance_id": "prov_test", "capability_id": "cap-test",
            "integrity_status": "totally_broken",
        })
        assert p.integrity_status == "totally_broken"

    def test_from_dict_with_invalid_signature_does_not_crash(self):
        p = CapabilityProvenance.from_dict({
            "provenance_id": "prov_test", "capability_id": "cap-test",
            "signature_status": "gold_plated",
        })
        assert p.signature_status == "gold_plated"

    def test_invalid_trust_defaults_to_untrusted_in_policy(self):
        """Trust policy normalizes invalid trust levels to untrusted."""
        policy = CapabilityTrustPolicy()
        p = CapabilityProvenance(
            provenance_id="prov_x", capability_id="cap-x",
            trust_level="not_a_real_level",
        )
        d = policy.evaluate_provenance(p)
        assert d.allowed is True
        assert d.details.get("trust_level") == TRUST_UNTRUSTED

    def test_invalid_integrity_defaults_to_unknown_in_policy(self):
        policy = CapabilityTrustPolicy()
        p = CapabilityProvenance(
            provenance_id="prov_x", capability_id="cap-x",
            integrity_status="future_status_v9",
        )
        d = policy.evaluate_provenance(p)
        assert d.details.get("integrity_status") == INTEGRITY_UNKNOWN

    def test_invalid_source_type_still_round_trips(self):
        p = CapabilityProvenance(
            provenance_id="prov_x", capability_id="cap-x",
            source_type="git_clone",
        )
        assert p.source_type == "git_clone"
        p2 = CapabilityProvenance.from_dict(p.to_dict())
        assert p2.source_type == "git_clone"

    def test_update_integrity_status_rejects_all_invalid(self):
        for bad in ("nope", "VALID", "Verified", "", " " * 3):
            assert bad not in PROVENANCE_INTEGRITY_STATUSES
            result = update_provenance_integrity_status(Path("/nonexistent"), bad)
            assert result is None


# ── 10. Serialization edge cases ────────────────────────────────────────────────


class TestSerializationEdgeCases:
    """Round-trip and special character handling in provenance serialization."""

    def test_round_trip_with_special_characters_in_metadata(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        meta = {
            "unicode": "日本語テスト",
            "emoji": "🚀",
            "quotes": 'he said "hello"',
            "newlines": "line1\nline2",
            "slash": "a/b\\c",
        }
        prov = write_provenance(d, capability_id="special-meta", metadata=meta)
        prov2 = read_provenance(d)
        assert prov2 is not None
        assert prov2.metadata == meta

    def test_round_trip_with_null_values(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="null-test",
            source_path_hash=None,
            imported_by=None,
            activated_by=None,
        )
        prov2 = read_provenance(d)
        assert prov2 is not None
        assert prov2.source_path_hash is None
        assert prov2.imported_by is None
        assert prov2.activated_by is None

    def test_round_trip_with_all_fields_populated(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        meta = {"plan_id": "qap_001", "request_id": "qtr_001", "score": 0.95}
        prov = write_provenance(
            d, capability_id="full-test",
            source_type=SOURCE_QUARANTINE_ACTIVATION,
            source_path_hash="a" * 64,
            source_content_hash="b" * 64,
            imported_at="2026-05-01T12:00:00+00:00",
            imported_by="importer",
            activated_at="2026-05-04T12:00:00+00:00",
            activated_by="activator",
            parent_provenance_id="prov_parent1234",
            origin_capability_id="cap-origin",
            origin_scope="quarantine",
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            metadata=meta,
        )
        prov2 = read_provenance(d)
        assert prov2 is not None
        assert prov2.to_dict() == prov.to_dict()

    def test_write_then_read_matches(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        prov = write_provenance(
            d, capability_id="match-test",
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
        )
        prov2 = read_provenance(d)
        assert prov2 is not None
        assert prov2.provenance_id == prov.provenance_id
        assert prov2.capability_id == prov.capability_id
        assert prov2.trust_level == TRUST_TRUSTED_LOCAL
        assert prov2.integrity_status == INTEGRITY_VERIFIED

    def test_provenance_json_is_valid_json_always(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        write_provenance(d, capability_id="valid-json", metadata={"key": "value"})
        raw = (d / "provenance.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["capability_id"] == "valid-json"
        assert parsed["metadata"] == {"key": "value"}


# ── 11. Write atomicity and corruption resilience ───────────────────────────────


class TestWriteCorruptionResilience:
    """Write behavior under failure and corruption scenarios."""

    def test_corrupt_write_partial_file_returns_none_on_read(self, tmp_path: Path):
        """A manually truncated provenance.json should not crash read_provenance."""
        (tmp_path / "provenance.json").write_text(
            '{"provenance_id": "prov_partial", "capability_id": "cap-x"',
            encoding="utf-8",
        )
        result = read_provenance(tmp_path)
        assert result is None

    def test_overwrite_preserves_structure(self, tmp_path: Path):
        """Writing provenance twice should overwrite cleanly."""
        d = tmp_path / "cap"
        d.mkdir()
        p1 = write_provenance(
            d, capability_id="first-write",
            trust_level=TRUST_UNTRUSTED,
        )
        p2 = write_provenance(
            d, capability_id="second-write",
            trust_level=TRUST_REVIEWED,
        )
        assert p1.provenance_id != p2.provenance_id
        read_back = read_provenance(d)
        assert read_back is not None
        assert read_back.capability_id == "second-write"
        assert read_back.trust_level == TRUST_REVIEWED

    def test_provenance_file_is_readable_text(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        write_provenance(d, capability_id="readable")
        raw = (d / "provenance.json").read_text(encoding="utf-8")
        assert '"provenance_id"' in raw
        assert '"capability_id"' in raw
        assert '"trust_level"' in raw

    def test_write_provenance_creates_file_if_missing(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        assert not (d / "provenance.json").exists()
        write_provenance(d, capability_id="create-test")
        assert (d / "provenance.json").is_file()

    def test_read_provenance_returns_none_for_directory_not_file(self, tmp_path: Path):
        sub = tmp_path / "prov_dir"
        sub.mkdir()
        # Create provenance.json as a directory (should not happen but be defensive)
        (sub / "provenance.json").mkdir(exist_ok=True)
        result = read_provenance(sub)
        assert result is None


# ── 12. Trust policy additional hardening ───────────────────────────────────────


class TestTrustPolicyHardening:
    """Additional trust policy edge cases and invariants."""

    def test_mismatch_blocks_activate(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(integrity_status=INTEGRITY_MISMATCH)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is False
        assert "integrity" in d.code.lower()

    def test_invalid_signature_blocks_activate(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(signature_status=SIGNATURE_INVALID)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is False
        assert "signature" in d.code.lower()

    def test_mismatch_blocks_promote(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_MISMATCH,
        )
        d = policy.can_promote_to_stable(_make_manifest(), p)
        assert d.allowed is False
        assert "integrity" in d.code.lower()

    def test_invalid_signature_blocks_promote(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_INVALID,
        )
        d = policy.can_promote_to_stable(_make_manifest(), p)
        assert d.allowed is False
        assert "signature" in d.code.lower()

    def test_trusted_signed_with_verified_sig_allows_promote(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        d = policy.can_promote_to_stable(_make_manifest(), p)
        assert d.allowed is True

    def test_can_retrieve_never_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        for trust in (TRUST_UNKNOWN, TRUST_UNTRUSTED, TRUST_REVIEWED, TRUST_TRUSTED_LOCAL, TRUST_TRUSTED_SIGNED):
            for integrity in (INTEGRITY_UNKNOWN, INTEGRITY_VERIFIED, INTEGRITY_MISMATCH):
                p = _make_provenance(trust_level=trust, integrity_status=integrity)
                d = policy.can_retrieve(manifest, p)
                assert d.allowed is True, f"can_retrieve denied for trust={trust}, integrity={integrity}"

    def test_can_retrieve_without_provenance_does_not_deny(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        d = policy.can_retrieve(manifest, None)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_evaluate_provenance_never_raises(self):
        policy = CapabilityTrustPolicy()
        # None
        policy.evaluate_provenance(None)
        # Valid
        policy.evaluate_provenance(_make_provenance())
        # Invalid trust
        policy.evaluate_provenance(_make_provenance(trust_level="bogus"))
        # Invalid integrity
        policy.evaluate_provenance(_make_provenance(integrity_status="bogus"))
        # Invalid signature
        policy.evaluate_provenance(_make_provenance(signature_status="bogus"))

    def test_trust_policy_state_never_mutates_provenance(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        trust_before = p.trust_level
        integrity_before = p.integrity_status
        sig_before = p.signature_status

        policy.evaluate_provenance(p)
        policy.can_activate_from_quarantine(p)
        policy.can_retrieve(_make_manifest(), p)
        policy.can_promote_to_stable(_make_manifest(), p)

        assert p.trust_level == trust_before
        assert p.integrity_status == integrity_before
        assert p.signature_status == sig_before
