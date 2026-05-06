"""Phase 1 tests: capability document parsing, schema validation, hashing.

Tests the non-runtime document model — no Brain, TaskRuntime, or
execution-path wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import CapabilityDocument, CapabilityParser, parse_capability
from src.capabilities.errors import (
    InvalidDocumentError,
    InvalidEnumValueError,
    InvalidManifestError,
    MalformedFrontMatterError,
    MissingFieldError,
)
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.hashing import compute_content_hash
from src.capabilities.ids import generate_capability_id, is_valid_capability_id


# ── Fixtures ───────────────────────────────────────────────────────────

_VALID_FRONT_MATTER: dict = {
    "id": "test_skill_01",
    "name": "Test Skill",
    "description": "A test capability.",
    "type": "skill",
    "scope": "workspace",
    "version": "0.1.0",
    "maturity": "draft",
    "status": "active",
    "risk_level": "low",
}


def _write_capability_dir(base: Path, dirname: str, front_matter: dict, body: str = "",
                          manifest: dict | None = None, mkdirs: list[str] | None = None) -> Path:
    """Create a minimal capability directory and return its path."""
    cap_dir = base / dirname
    cap_dir.mkdir(parents=True, exist_ok=True)

    # Write CAPABILITY.md
    fm_yaml = yaml.dump(front_matter, allow_unicode=True, sort_keys=False)
    md = f"---\n{fm_yaml}---\n\n{body}"
    (cap_dir / "CAPABILITY.md").write_text(md, encoding="utf-8")

    # Write manifest.json if provided
    if manifest is not None:
        (cap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Create standard subdirectories
    for d in (mkdirs or []):
        (cap_dir / d).mkdir()

    return cap_dir


# ── ID tests ────────────────────────────────────────────────────────────

class TestCapabilityIds:
    def test_generate_returns_scope_prefixed_id(self):
        cap_id = generate_capability_id("workspace")
        assert cap_id.startswith("workspace_")
        assert len(cap_id) > len("workspace_")

    def test_generate_produces_unique_ids(self):
        ids = {generate_capability_id("global") for _ in range(100)}
        assert len(ids) == 100

    def test_is_valid_accepts_generated_ids(self):
        for _ in range(10):
            cap_id = generate_capability_id("workspace")
            assert is_valid_capability_id(cap_id)

    def test_is_valid_rejects_bad_ids(self):
        assert not is_valid_capability_id("")
        assert not is_valid_capability_id("A_bad")
        assert not is_valid_capability_id("has spaces")
        assert not is_valid_capability_id("a")  # too short


# ── Hashing tests ───────────────────────────────────────────────────────

class TestContentHashing:
    def test_same_data_produces_same_hash(self):
        data = {"id": "test", "name": "Test", "type": "skill"}
        h1 = compute_content_hash(data)
        h2 = compute_content_hash(data)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_different_data_produces_different_hash(self):
        h1 = compute_content_hash({"id": "test", "name": "A"})
        h2 = compute_content_hash({"id": "test", "name": "B"})
        assert h1 != h2

    def test_body_changes_hash(self):
        data = {"id": "test", "name": "Test"}
        h1 = compute_content_hash(data, body="Body A")
        h2 = compute_content_hash(data, body="Body B")
        assert h1 != h2

    def test_computed_fields_are_ignored(self):
        """content_hash, created_at, updated_at must not affect the hash."""
        data = {"id": "test", "name": "Test"}
        h1 = compute_content_hash(data)
        data_with_computed = {**data, "content_hash": "abc123", "created_at": "2024-01-01",
                              "updated_at": "2024-01-02"}
        h2 = compute_content_hash(data_with_computed)
        assert h1 == h2

    def test_no_self_referential_churn(self):
        """Repeatedly computing and storing hash must stay stable."""
        data = {"id": "test", "name": "Test", "description": "A test"}
        h1 = compute_content_hash(data)
        # Simulate storing hash back
        data["content_hash"] = h1
        h2 = compute_content_hash(data)
        assert h1 == h2

    def test_dict_key_order_does_not_matter(self):
        from collections import OrderedDict
        d1: dict = {"name": "A", "id": "test", "type": "skill"}
        d2 = OrderedDict([("id", "test"), ("type", "skill"), ("name", "A")])
        assert compute_content_hash(d1) == compute_content_hash(dict(d2))

    def test_list_order_is_normalised(self):
        """Lists are sorted before hashing for stability."""
        h1 = compute_content_hash({"tags": ["b", "a", "c"]})
        h2 = compute_content_hash({"tags": ["a", "b", "c"]})
        assert h1 == h2


# ── Manifest-only parsing tests ─────────────────────────────────────────

class TestParseManifestJson:
    def test_valid_manifest_json(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        manifest={"_comment": "ignored"})
        doc = parse_capability(cap_dir)
        assert doc.id == "test_skill_01"
        assert doc.type == CapabilityType.SKILL

    def test_manifest_json_overrides_front_matter(self, tmp_path):
        cap_dir = _write_capability_dir(
            tmp_path, "cap", _VALID_FRONT_MATTER,
            manifest={"name": "Overridden Name"},
        )
        doc = parse_capability(cap_dir)
        assert doc.name == "Overridden Name"

    def test_invalid_json_raises(self, tmp_path):
        cap_dir = tmp_path / "bad_json"
        cap_dir.mkdir()
        (cap_dir / "CAPABILITY.md").write_text(
            "---\nid: test\nname: Test\ndescription: Desc\ntype: skill\n"
            "scope: workspace\nversion: 0.1.0\nmaturity: draft\nstatus: active\n"
            "risk_level: low\n---\n\nBody.",
            encoding="utf-8",
        )
        (cap_dir / "manifest.json").write_text("{not valid json}", encoding="utf-8")
        with pytest.raises(InvalidManifestError):
            parse_capability(cap_dir)

    def test_manifest_json_not_an_object_raises(self, tmp_path):
        cap_dir = tmp_path / "bad_type"
        cap_dir.mkdir()
        (cap_dir / "CAPABILITY.md").write_text(
            "---\nid: test\nname: Test\ndescription: Desc\ntype: skill\n"
            "scope: workspace\nversion: 0.1.0\nmaturity: draft\nstatus: active\n"
            "risk_level: low\n---\n\nBody.",
            encoding="utf-8",
        )
        (cap_dir / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(InvalidManifestError):
            parse_capability(cap_dir)


# ── CAPABILITY.md parsing tests ─────────────────────────────────────────

class TestParseCapabilityMd:
    def test_valid_capability_md(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        body="# Section\n\nContent here.")
        doc = parse_capability(cap_dir)
        assert doc.id == "test_skill_01"
        assert doc.name == "Test Skill"
        assert doc.type == CapabilityType.SKILL
        assert doc.scope == CapabilityScope.WORKSPACE
        assert doc.manifest.version == "0.1.0"
        assert doc.manifest.maturity == CapabilityMaturity.DRAFT
        assert doc.manifest.status == CapabilityStatus.ACTIVE
        assert doc.manifest.risk_level == CapabilityRiskLevel.LOW
        assert "# Section" in doc.body

    def test_missing_capability_md_raises(self, tmp_path):
        cap_dir = tmp_path / "no_md"
        cap_dir.mkdir()
        with pytest.raises(InvalidDocumentError, match="Missing CAPABILITY.md"):
            parse_capability(cap_dir)

    def test_not_a_directory_raises(self, tmp_path):
        f = tmp_path / "not_a_dir"
        f.write_text("hello")
        with pytest.raises(InvalidDocumentError, match="Not a directory"):
            parse_capability(f)

    def test_missing_front_matter_raises(self, tmp_path):
        cap_dir = tmp_path / "no_fm"
        cap_dir.mkdir()
        (cap_dir / "CAPABILITY.md").write_text("# Just markdown, no front matter.")
        with pytest.raises(MalformedFrontMatterError):
            parse_capability(cap_dir)

    def test_malformed_yaml_front_matter_raises(self, tmp_path):
        cap_dir = tmp_path / "bad_yaml"
        cap_dir.mkdir()
        (cap_dir / "CAPABILITY.md").write_text("---\n\tbad: [indent\n---\n\nBody.")
        with pytest.raises(MalformedFrontMatterError):
            parse_capability(cap_dir)

    def test_front_matter_not_a_dict_raises(self, tmp_path):
        cap_dir = tmp_path / "list_fm"
        cap_dir.mkdir()
        (cap_dir / "CAPABILITY.md").write_text("---\n- item1\n- item2\n---\n\nBody.")
        with pytest.raises(MalformedFrontMatterError, match="mapping"):
            parse_capability(cap_dir)

    def test_optional_fields_defaulted(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        assert doc.manifest.trust_required == "developer"
        assert doc.manifest.required_tools == []
        assert doc.manifest.required_permissions == []
        assert doc.manifest.triggers == []
        assert doc.manifest.tags == []

    def test_standard_dirs_recognised(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        mkdirs=["scripts", "tests", "evals"])
        doc = parse_capability(cap_dir)
        assert doc.standard_dirs == {"scripts", "tests", "evals"}

    def test_standard_dirs_empty_when_none_present(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        assert doc.standard_dirs == set()


# ── Validation: missing fields ──────────────────────────────────────────

class TestMissingFieldValidation:
    REQUIRED = ["id", "name", "description", "type", "scope", "version",
                "maturity", "status", "risk_level"]

    def _assert_missing(self, tmp_path, field: str):
        fm = dict(_VALID_FRONT_MATTER)
        del fm[field]
        cap_dir = _write_capability_dir(tmp_path, f"missing_{field}", fm)
        with pytest.raises(MissingFieldError, match=field):
            parse_capability(cap_dir)

    def test_missing_id_raises(self, tmp_path):
        self._assert_missing(tmp_path, "id")

    def test_missing_name_raises(self, tmp_path):
        self._assert_missing(tmp_path, "name")

    def test_missing_description_raises(self, tmp_path):
        self._assert_missing(tmp_path, "description")

    def test_missing_type_raises(self, tmp_path):
        self._assert_missing(tmp_path, "type")

    def test_missing_scope_raises(self, tmp_path):
        self._assert_missing(tmp_path, "scope")

    def test_missing_version_raises(self, tmp_path):
        self._assert_missing(tmp_path, "version")

    def test_missing_maturity_raises(self, tmp_path):
        self._assert_missing(tmp_path, "maturity")

    def test_missing_status_raises(self, tmp_path):
        self._assert_missing(tmp_path, "status")

    def test_missing_risk_level_raises(self, tmp_path):
        self._assert_missing(tmp_path, "risk_level")


# ── Validation: invalid enum values ─────────────────────────────────────

class TestInvalidEnumValidation:
    def test_invalid_type_raises(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "type": "not_a_type"}
        cap_dir = _write_capability_dir(tmp_path, "bad_type", fm)
        with pytest.raises(InvalidEnumValueError, match="type"):
            parse_capability(cap_dir)

    def test_invalid_scope_raises(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "scope": "galaxy"}
        cap_dir = _write_capability_dir(tmp_path, "bad_scope", fm)
        with pytest.raises(InvalidEnumValueError, match="scope"):
            parse_capability(cap_dir)

    def test_invalid_maturity_raises(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "maturity": "production"}
        cap_dir = _write_capability_dir(tmp_path, "bad_mat", fm)
        with pytest.raises(InvalidEnumValueError, match="maturity"):
            parse_capability(cap_dir)

    def test_invalid_status_raises(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "status": "deleted"}
        cap_dir = _write_capability_dir(tmp_path, "bad_status", fm)
        with pytest.raises(InvalidEnumValueError, match="status"):
            parse_capability(cap_dir)

    def test_invalid_risk_level_raises(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "risk_level": "extreme"}
        cap_dir = _write_capability_dir(tmp_path, "bad_risk", fm)
        with pytest.raises(InvalidEnumValueError, match="risk_level"):
            parse_capability(cap_dir)


# ── Content hash integration ────────────────────────────────────────────

class TestContentHashIntegration:
    def test_hash_stable_across_repeated_parses(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        body="Body content.")
        h1 = parse_capability(cap_dir).content_hash
        h2 = parse_capability(cap_dir).content_hash
        assert h1 == h2

    def test_hash_changes_when_body_changes(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        body="Original body.")
        h1 = parse_capability(cap_dir).content_hash
        # Change body
        (cap_dir / "CAPABILITY.md").write_text(
            "---\n" + yaml.dump(_VALID_FRONT_MATTER, allow_unicode=True, sort_keys=False) +
            "---\n\nModified body.",
            encoding="utf-8",
        )
        h2 = parse_capability(cap_dir).content_hash
        assert h1 != h2

    def test_hash_changes_when_metadata_changes(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        h1 = parse_capability(cap_dir).content_hash
        # Change name in manifest.json (overrides front matter)
        (cap_dir / "manifest.json").write_text(
            json.dumps({"name": "Changed Name"}), encoding="utf-8",
        )
        h2 = parse_capability(cap_dir).content_hash
        assert h1 != h2

    def test_hash_does_not_churn_on_reparse_with_hash_stored(self, tmp_path):
        """Simulate storing hash in manifest.json — must not affect re-parse."""
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc1 = parse_capability(cap_dir)
        h1 = doc1.content_hash

        # Write hash back into manifest.json (as if a tool wrote it)
        (cap_dir / "manifest.json").write_text(
            json.dumps({"content_hash": h1}), encoding="utf-8",
        )
        doc2 = parse_capability(cap_dir)
        h2 = doc2.content_hash
        assert h1 == h2


# ── Extensibility escape hatch ──────────────────────────────────────────

class TestExtensibilityEscapeHatch:
    def test_unknown_metadata_lands_in_extra(self, tmp_path):
        fm = {**_VALID_FRONT_MATTER, "custom_field": "custom_value",
              "another_one": 42}
        cap_dir = _write_capability_dir(tmp_path, "cap", fm)
        doc = parse_capability(cap_dir)
        assert doc.manifest.extra.get("custom_field") == "custom_value"
        assert doc.manifest.extra.get("another_one") == 42

    def test_computed_fields_removed_from_extra(self, tmp_path):
        """If someone puts content_hash or created_at in extra, they are stripped."""
        fm = {**_VALID_FRONT_MATTER, "content_hash": "evil_hash",
              "created_at": "2020-01-01", "extra_field": "ok"}
        cap_dir = _write_capability_dir(tmp_path, "cap", fm)
        doc = parse_capability(cap_dir)
        assert "content_hash" not in doc.manifest.extra
        assert "created_at" not in doc.manifest.extra
        assert "updated_at" not in doc.manifest.extra
        assert doc.manifest.extra.get("extra_field") == "ok"

    def test_unknown_fields_dont_crash_parser(self, tmp_path):
        """Unknown fields should not crash — they go to extra."""
        fm = {**_VALID_FRONT_MATTER, "unknown_nested": {"a": 1, "b": 2},
              "some_list": [1, 2, 3]}
        cap_dir = _write_capability_dir(tmp_path, "cap", fm)
        doc = parse_capability(cap_dir)
        assert "unknown_nested" in doc.manifest.extra
        assert "some_list" in doc.manifest.extra


# ── CapabilityDocument to_dict ──────────────────────────────────────────

class TestCapabilityDocumentToDict:
    def test_to_dict_includes_all_sections(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        body="Body.", mkdirs=["scripts"])
        doc = parse_capability(cap_dir)
        d = doc.to_dict()
        assert "manifest" in d
        assert "body" in d
        assert "directory" in d
        assert "standard_dirs" in d
        assert d["body"] == "Body."
        assert d["standard_dirs"] == ["scripts"]


# ── All capability types can be parsed ──────────────────────────────────

class TestAllCapabilityTypes:
    @pytest.mark.parametrize("cap_type", [
        "skill", "workflow", "dynamic_agent", "memory_pattern",
        "tool_wrapper", "project_playbook",
    ])
    def test_each_type_parses(self, tmp_path, cap_type):
        fm = {**_VALID_FRONT_MATTER, "type": cap_type, "id": f"test_{cap_type}"}
        cap_dir = _write_capability_dir(tmp_path, f"cap_{cap_type}", fm)
        doc = parse_capability(cap_dir)
        assert doc.type.value == cap_type


# ── All scopes can be parsed ────────────────────────────────────────────

class TestAllScopes:
    @pytest.mark.parametrize("scope", ["global", "user", "workspace", "session"])
    def test_each_scope_parses(self, tmp_path, scope):
        fm = {**_VALID_FRONT_MATTER, "scope": scope, "id": f"test_{scope}"}
        cap_dir = _write_capability_dir(tmp_path, f"cap_{scope}", fm)
        doc = parse_capability(cap_dir)
        assert doc.scope.value == scope


# ── All maturity values can be parsed ───────────────────────────────────

class TestAllMaturities:
    @pytest.mark.parametrize("maturity", ["draft", "testing", "stable", "broken", "repairing"])
    def test_each_maturity_parses(self, tmp_path, maturity):
        fm = {**_VALID_FRONT_MATTER, "maturity": maturity, "id": f"test_{maturity}"}
        cap_dir = _write_capability_dir(tmp_path, f"cap_{maturity}", fm)
        doc = parse_capability(cap_dir)
        assert doc.manifest.maturity.value == maturity
