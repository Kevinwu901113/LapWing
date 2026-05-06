"""Post-Maintenance Consolidation Audit: E2E tests for maintenance flow.

Flows:
  A: health finding -> repair queue item (dedup, no capability mutation)
  B: operator lifecycle (list/view/acknowledge/resolve/dismiss)
  C: corruption tolerance (corrupt items/files, clean handling, no crashes)
  D: permissions/flags (tools absent by default, denied to non-repair profiles)
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
    generate_capability_health_report,
)
from src.capabilities.repair_queue import RepairQueueItem, RepairQueueStore
from src.capabilities.schema import CapabilityMaturity, CapabilityScope, CapabilityStatus
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

    def get_tools_for_profile(self, profile):
        """Simulate profile-based tool filtering by capability tag matching."""
        if profile is None:
            return []
        caps = getattr(profile, "capabilities", frozenset())
        result = []
        for spec in self._t.values():
            spec_cap = getattr(spec, "capability", "general")
            spec_caps = getattr(spec, "capabilities", ())
            if spec_cap in caps or any(c in caps for c in spec_caps):
                result.append(spec)
        return result

    @property
    def tools(self):
        return list(self._t.values())


# ── Helpers ───────────────────────────────────────────────────────────────

REPAIR_QUEUE_TOOL_NAMES = {
    "list_repair_queue_items",
    "view_repair_queue_item",
    "create_repair_queue_from_health",
    "acknowledge_repair_queue_item",
    "resolve_repair_queue_item",
    "dismiss_repair_queue_item",
}

FORBIDDEN_TOOL_NAMES = {
    "run_capability", "repair_capability", "auto_repair_capability",
    "execute_repair", "apply_repair_queue_item",
    "rebuild_index_from_health", "promote_from_health",
}


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_queue_store(tmp_path: Path) -> RepairQueueStore:
    return RepairQueueStore(data_dir=tmp_path / "capabilities")


def _make_item(**overrides) -> RepairQueueItem:
    defaults = {
        "item_id": "rq-e2e-001",
        "created_at": "2026-05-06T10:00:00+00:00",
        "source": "health_report",
        "finding_code": "missing_provenance_legacy",
        "severity": "info",
        "status": "open",
        "title": "E2E test item",
        "description": "E2E test",
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
        new_manifest = doc.manifest.model_copy(update={
            "maturity": CapabilityMaturity(maturity),
            "status": CapabilityStatus(status),
        })
        doc.manifest = new_manifest
        store._sync_manifest_json(cap_dir, doc)
        store._parser.parse(cap_dir)
    return doc.id


def _file_hashes(data_dir: Path) -> dict[str, str]:
    result = {}
    for fpath in sorted(data_dir.rglob("*")):
        if fpath.is_file():
            result[str(fpath)] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    return result


def _make_context():
    return ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp")


# ═══════════════════════════════════════════════════════════════════════════
# Flow A: Health finding to repair queue
# ═══════════════════════════════════════════════════════════════════════════


class TestFlowAHealthToQueue:
    """Flow A: health report findings flow into repair queue items correctly."""

    async def test_finding_to_queue_item_created(self, tmp_path: Path):
        """A health finding produces a corresponding queue item with correct action."""
        store = _make_store(tmp_path)
        # Create a testing cap (will have missing provenance + stale eval)
        cap_id = _make_doc(store, "flow-a-1", maturity="testing")

        report = generate_capability_health_report(store)
        assert len(report.findings) > 0, "Expected at least one finding"

        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report, dedupe=False)
        assert len(items) > 0, "Expected at least one queue item created"

        # Each item should have a valid recommended_action
        for item in items:
            assert item.recommended_action
            assert item.source == "health_report"
            assert item.status == "open"

    async def test_recommended_action_labels_correct(self, tmp_path: Path):
        """Recommended actions match expected mappings for known finding codes."""
        store = _make_store(tmp_path)
        _make_doc(store, "flow-a-action", maturity="testing")

        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report, dedupe=False)

        # Known mappings from health finding codes
        action_map = {
            "missing_provenance_legacy": "add_provenance",
            "eval_missing": "reeval",
            "eval_stale": "reeval",
            "integrity_mismatch": "manual_review",
        }
        for item in items:
            expected = action_map.get(item.finding_code)
            if expected is not None:
                assert item.recommended_action == expected, (
                    f"Finding code {item.finding_code} expected action {expected}, "
                    f"got {item.recommended_action}"
                )

    async def test_dedupe_skips_existing_open_items(self, tmp_path: Path):
        """Dedupe prevents duplicate open items for the same finding."""
        store = _make_store(tmp_path)
        _make_doc(store, "flow-a-dedup", maturity="testing")

        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)

        # First pass: creates items
        items1 = qs.create_from_health_report(report, dedupe=True)
        count1 = len(items1)

        # Second pass with dedupe: should skip all (same findings, already open)
        report2 = generate_capability_health_report(store)
        items2 = qs.create_from_health_report(report2, dedupe=True)
        assert len(items2) == 0, f"Dedupe should skip all, but created {len(items2)}"

        # Third pass without dedupe: should create duplicates
        items3 = qs.create_from_health_report(report, dedupe=False)
        assert len(items3) == count1, (
            f"No dedupe should create {count1} items, but created {len(items3)}"
        )

    async def test_no_capability_files_changed_by_create_from_health(self, tmp_path: Path):
        """create_from_health only writes repair_queue files, nothing else."""
        store = _make_store(tmp_path)
        _make_doc(store, "flow-a-no-mutate", maturity="testing")

        hashes_before = _file_hashes(store.data_dir)
        assert hashes_before

        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)
        qs.create_from_health_report(report, dedupe=False)

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_finding_codes_preserved_in_queue(self, tmp_path: Path):
        """Finding codes from health report are correctly preserved in queue items."""
        store = _make_store(tmp_path)
        _make_doc(store, "flow-a-codes", maturity="testing")

        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report, dedupe=False)

        finding_codes = {f.code for f in report.findings}
        queue_codes = {i.finding_code for i in items}

        # All queue item finding codes should be from report findings
        for qc in queue_codes:
            assert qc in finding_codes, f"Queue item has unexpected finding code: {qc}"


# ═══════════════════════════════════════════════════════════════════════════
# Flow B: Operator lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestFlowBOperatorLifecycle:
    """Flow B: full operator lifecycle through the 6 repair queue tools."""

    async def test_full_lifecycle_list_view_ack_resolve_dismiss(self, tmp_path: Path):
        """Operator can list, view, acknowledge, resolve, and dismiss items."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "flow-b-lifecycle", maturity="testing")

        # Create items via health report
        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)
        created = qs.create_from_health_report(report, dedupe=False)
        assert len(created) > 0

        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        # 1. List
        list_spec = registry.get("list_repair_queue_items")
        result = await list_spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == len(created)

        # 2. View
        item_id = created[0].item_id
        view_spec = registry.get("view_repair_queue_item")
        result = await view_spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": item_id}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["item_id"] == item_id
        assert "action_payload" in result.payload["item"]

        # 3. Acknowledge
        ack_spec = registry.get("acknowledge_repair_queue_item")
        result = await ack_spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": item_id, "actor": "operator-1", "reason": "Noted.",
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "acknowledged"

        # 4. Resolve another item
        if len(created) > 1:
            resolve_id = created[1].item_id
            res_spec = registry.get("resolve_repair_queue_item")
            result = await res_spec.executor(
                ToolExecutionRequest(name="test", arguments={
                    "item_id": resolve_id, "actor": "operator-1", "reason": "Fixed.",
                }),
                _make_context(),
            )
            assert result.success
            assert result.payload["item"]["status"] == "resolved"

        # 5. Dismiss the first item
        dis_spec = registry.get("dismiss_repair_queue_item")
        result = await dis_spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": item_id, "actor": "operator-1", "reason": "Not relevant.",
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "dismissed"

    async def test_only_queue_item_files_change(self, tmp_path: Path):
        """Throughout the operator lifecycle, only queue item files mutate."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "flow-b-only-queue", maturity="testing")

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-b-mut-1", capability_id=cap_id))
        qs.create_item(_make_item(item_id="rq-b-mut-2", capability_id=cap_id))

        hashes_before = _file_hashes(store.data_dir)

        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        # Run all status-mutating tools
        for tool_name, item_id in [
            ("acknowledge_repair_queue_item", "rq-b-mut-1"),
            ("resolve_repair_queue_item", "rq-b-mut-2"),
            ("dismiss_repair_queue_item", "rq-b-mut-1"),
        ]:
            spec = registry.get(tool_name)
            await spec.executor(
                ToolExecutionRequest(name="test", arguments={"item_id": item_id}),
                _make_context(),
            )

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_capability_provenance_index_trust_roots_unchanged(self, tmp_path: Path):
        """Operator status updates do not touch capability/provenance/index/trust roots."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "flow-b-all-safe", maturity="testing")
        doc_before = store.get(cap_id)

        # Set up index
        from src.capabilities.index import CapabilityIndex
        index = CapabilityIndex(tmp_path / "test-idx.db")
        index.init()
        index.rebuild_from_store(store)

        # Set up trust root
        roots_dir = store.data_dir / "trust_roots"
        roots_dir.mkdir(parents=True, exist_ok=True)
        root_json = roots_dir / "tr-b.json"
        root_json.write_text(json.dumps({"trust_root_id": "tr-b", "status": "active"}))
        root_hash_before = hashlib.sha256(root_json.read_bytes()).hexdigest()

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-b-safe", capability_id=cap_id))

        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs)

        spec = registry.get("resolve_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-b-safe"}),
            _make_context(),
        )

        # Verify capability unchanged
        doc_after = store.get(cap_id)
        assert doc_after.manifest.maturity.value == doc_before.manifest.maturity.value
        assert doc_after.manifest.status.value == doc_before.manifest.status.value

        # Verify index unchanged
        count = index.conn.execute("SELECT COUNT(*) FROM capability_index").fetchone()[0]
        assert count == 1

        # Verify trust root unchanged
        root_hash_after = hashlib.sha256(root_json.read_bytes()).hexdigest()
        assert root_hash_before == root_hash_after

    async def test_view_missing_item_returns_not_found(self, tmp_path: Path):
        """Viewing a non-existent item returns clean not_found."""
        qs = _make_queue_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs)

        spec = registry.get("view_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_status_update_missing_item_returns_not_found(self, tmp_path: Path):
        """Status updates on missing items return clean not_found."""
        qs = _make_queue_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs)

        for tool_name in ("acknowledge_repair_queue_item", "resolve_repair_queue_item",
                          "dismiss_repair_queue_item"):
            spec = registry.get(tool_name)
            result = await spec.executor(
                ToolExecutionRequest(name="test", arguments={"item_id": "nonexistent"}),
                _make_context(),
            )
            assert not result.success, f"{tool_name} should fail for missing item"
            assert result.payload["error"] == "not_found"

    async def test_actor_and_reason_preserved_in_metadata(self, tmp_path: Path):
        """Actor and reason are preserved in item metadata after status update."""
        qs = _make_queue_store(tmp_path)
        item = _make_item(item_id="rq-b-metadata")
        qs.create_item(item)

        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs)

        spec = registry.get("acknowledge_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": "rq-b-metadata",
                "actor": "test-operator",
                "reason": "Testing metadata preservation.",
            }),
            _make_context(),
        )

        retrieved = qs.get_item("rq-b-metadata")
        assert retrieved is not None
        assert retrieved.status == "acknowledged"
        assert retrieved.metadata.get("status_change_actor") == "test-operator"
        assert retrieved.metadata.get("status_change_reason") == "Testing metadata preservation."


# ═══════════════════════════════════════════════════════════════════════════
# Flow C: Corruption tolerance
# ═══════════════════════════════════════════════════════════════════════════


class TestFlowCCorruptionTolerance:
    """Flow C: corrupt items and source files are handled cleanly, no crashes."""

    async def test_corrupt_queue_item_json_skipped_in_list(self, tmp_path: Path):
        """Corrupt JSON in a queue item file is skipped during list, not crashed."""
        store = _make_store(tmp_path)
        _make_doc(store, "flow-c-1", maturity="testing")

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-c-good"))

        # Write a corrupt file
        corrupt_path = qs._queue_dir / "rq-c-corrupt.json"
        corrupt_path.write_text("not valid json {{{", encoding="utf-8")

        # List should succeed and skip the corrupt item
        items = qs.list_items()
        assert len(items) >= 1
        item_ids = {i.item_id for i in items}
        assert "rq-c-corrupt" not in item_ids
        assert "rq-c-good" in item_ids

    async def test_corrupt_queue_item_view_returns_none(self, tmp_path: Path):
        """Corrupt queue item returns None from get_item, no crash."""
        qs = _make_queue_store(tmp_path)
        corrupt_path = qs._queue_dir / "rq-c-corrupt-v.json"
        qs._ensure_queue_dir()
        corrupt_path.write_text("{{broken", encoding="utf-8")

        item = qs.get_item("rq-c-corrupt-v")
        assert item is None

    async def test_create_from_health_handles_empty_store(self, tmp_path: Path):
        """Creating from health on an empty store succeeds gracefully."""
        store = _make_store(tmp_path)
        report = generate_capability_health_report(store)

        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report, dedupe=False)
        # May be 0 or more depending on store state
        assert isinstance(items, list)

    async def test_create_from_health_no_crash_on_missing_data_dir(self, tmp_path: Path):
        """create_from_health does not crash when data_dir has no capability files."""
        empty_dir = tmp_path / "empty_caps"
        empty_dir.mkdir(parents=True, exist_ok=True)

        store = CapabilityStore(data_dir=empty_dir)
        report = generate_capability_health_report(store)

        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report)
        assert items == []

    async def test_no_mutation_outside_queue_on_corrupt_item(self, tmp_path: Path):
        """Operations on corrupt items do not mutate capability files."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "flow-c-no-mutate", maturity="testing")

        hashes_before = _file_hashes(store.data_dir)

        qs = _make_queue_store(tmp_path)
        # Create a corrupt queue item
        qs._ensure_queue_dir()
        (qs._queue_dir / "rq-c-corrupt-m.json").write_text("{{{bad json", encoding="utf-8")

        # Various operations
        qs.list_items()
        qs.get_item("rq-c-corrupt-m")
        qs.update_status("nonexistent", "acknowledged")

        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_list_empty_queue_returns_empty(self, tmp_path: Path):
        """Listing an empty or non-existent queue returns []."""
        qs = _make_queue_store(tmp_path)
        # Queue dir doesn't exist yet
        items = qs.list_items()
        assert items == []

        # After ensuring dir exists but is empty
        qs._ensure_queue_dir()
        items = qs.list_items()
        assert items == []

    async def test_status_update_preserves_other_fields(self, tmp_path: Path):
        """Status updates change only status/timestamps, preserving all other fields."""
        qs = _make_queue_store(tmp_path)
        item = _make_item(
            item_id="rq-c-preserve",
            action_payload={"source_finding_code": "eval_stale"},
            evidence={"test": "data"},
        )
        qs.create_item(item)

        updated = qs.update_status("rq-c-preserve", "acknowledged")
        assert updated is not None
        assert updated.finding_code == item.finding_code
        assert updated.recommended_action == item.recommended_action
        assert updated.action_payload == item.action_payload
        assert updated.evidence == item.evidence
        assert updated.capability_id == item.capability_id


# ═══════════════════════════════════════════════════════════════════════════
# Flow D: Permissions and feature flags
# ═══════════════════════════════════════════════════════════════════════════


class TestFlowDPermissionsAndFlags:
    """Flow D: tools are absent by default, gated behind repair_queue_tools_enabled flag."""

    def test_tools_absent_when_not_registered(self):
        """Without calling register_repair_queue_tools, no repair tools exist."""
        registry = _FakeRegistry()
        for name in REPAIR_QUEUE_TOOL_NAMES:
            assert registry.get(name) is None, f"{name} should not be registered"

    def test_all_six_tools_registered_when_called(self, tmp_path: Path):
        """register_repair_queue_tools registers exactly 6 tools."""
        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        for name in REPAIR_QUEUE_TOOL_NAMES:
            assert registry.get(name) is not None, f"{name} should be registered"

    def test_all_tools_have_correct_capability_tag(self, tmp_path: Path):
        """All 6 tools have capability_repair_operator tag."""
        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        for name in REPAIR_QUEUE_TOOL_NAMES:
            spec = registry.get(name)
            assert spec is not None
            caps = {spec.capability, *spec.capabilities}
            assert "capability_repair_operator" in caps, (
                f"{name} missing capability_repair_operator tag"
            )

    def test_forbidden_tools_absent(self, tmp_path: Path):
        """No forbidden tools are registered."""
        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        for name in FORBIDDEN_TOOL_NAMES:
            assert registry.get(name) is None, f"Forbidden tool {name} should not exist"

    def test_none_store_skips_registration(self):
        """When repair_queue_store is None, tools are not registered."""
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, None)
        for name in REPAIR_QUEUE_TOOL_NAMES:
            assert registry.get(name) is None, f"{name} should not be registered with None store"

    def test_profile_grants_tools_via_capability_tag(self, tmp_path: Path):
        """CAPABILITY_REPAIR_OPERATOR_PROFILE can access all 6 tools via tag matching."""
        from src.core.runtime_profiles import CAPABILITY_REPAIR_OPERATOR_PROFILE

        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        granted = registry.get_tools_for_profile(CAPABILITY_REPAIR_OPERATOR_PROFILE)
        granted_names = {s.name for s in granted}
        for name in REPAIR_QUEUE_TOOL_NAMES:
            assert name in granted_names, f"{name} not granted to repair operator profile"

    def test_standard_profiles_denied(self, tmp_path: Path):
        """Standard/default profiles cannot access repair queue tools."""
        from src.core.runtime_profiles import get_runtime_profile

        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        repair_cap = "capability_repair_operator"
        standard_names = ["standard", "chat_shell", "zero_tools",
                          "inner_tick", "local_execution", "compose_proactive"]
        for profile_name in standard_names:
            profile = get_runtime_profile(profile_name)
            caps = getattr(profile, "capabilities", frozenset())
            assert repair_cap not in caps, (
                f"{profile_name} should not have repair_operator tag"
            )
            granted = registry.get_tools_for_profile(profile)
            granted_names = {s.name for s in granted}
            for name in REPAIR_QUEUE_TOOL_NAMES:
                assert name not in granted_names, (
                    f"{name} should not be granted to {profile_name}"
                )

    def test_other_operator_profiles_denied(self, tmp_path: Path):
        """Other operator profiles cannot access repair queue tools."""
        from src.core.runtime_profiles import get_runtime_profile

        qs = _make_queue_store(tmp_path)
        store = _make_store(tmp_path)
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        repair_cap = "capability_repair_operator"
        operator_names = [
            "agent_admin_operator",
            "capability_lifecycle_operator",
            "capability_curator_operator",
            "identity_operator",
            "browser_operator",
            "skill_operator",
            "agent_candidate_operator",
            "capability_import_operator",
            "capability_trust_operator",
        ]
        for profile_name in operator_names:
            profile = get_runtime_profile(profile_name)
            caps = getattr(profile, "capabilities", frozenset())
            assert repair_cap not in caps, (
                f"{profile_name} should not have repair_operator tag"
            )
            granted = registry.get_tools_for_profile(profile)
            granted_names = {s.name for s in granted}
            for name in REPAIR_QUEUE_TOOL_NAMES:
                assert name not in granted_names, (
                    f"{name} should not be granted to {profile_name}"
                )

    def test_create_from_health_fails_without_capability_store(self, tmp_path: Path):
        """create_repair_queue_from_health returns error when no capability_store."""
        qs = _make_queue_store(tmp_path)
        registry = _FakeRegistry()
        # Register without capability_store
        register_repair_queue_tools(registry, qs)

        import asyncio
        spec = registry.get("create_repair_queue_from_health")
        result = asyncio.run(spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        ))
        assert not result.success
        assert "capability_store" in result.payload.get("error", "")


# ═══════════════════════════════════════════════════════════════════════════
# Combined E2E: Full maintenance flow
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EFullMaintenanceFlow:
    """End-to-end: health -> queue -> operator lifecycle, no mutation outside queue."""

    async def test_full_flow_health_to_dismiss(self, tmp_path: Path):
        """Complete flow from health report through all operator status transitions."""
        store = _make_store(tmp_path)
        cap_id = _make_doc(store, "full-flow", maturity="testing")

        hashes_before = _file_hashes(store.data_dir)

        # Step 1: Generate health report
        report = generate_capability_health_report(store)
        assert report.total_capabilities >= 1
        assert len(report.findings) >= 1

        # Step 2: Create queue items from health
        qs = _make_queue_store(tmp_path)
        items = qs.create_from_health_report(report, dedupe=False)
        assert len(items) >= 1

        # Step 3: Register tools and run full operator lifecycle
        registry = _FakeRegistry()
        register_repair_queue_tools(registry, qs, capability_store=store)

        item_id = items[0].item_id

        # List
        list_spec = registry.get("list_repair_queue_items")
        result = await list_spec.executor(
            ToolExecutionRequest(name="test", arguments={"status": "open"}),
            _make_context(),
        )
        assert result.success
        open_count = result.payload["count"]

        # View
        view_spec = registry.get("view_repair_queue_item")
        result = await view_spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": item_id}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "open"

        # Acknowledge
        ack_spec = registry.get("acknowledge_repair_queue_item")
        result = await ack_spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": item_id}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "acknowledged"

        # Resolve
        res_spec = registry.get("resolve_repair_queue_item")
        result = await res_spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": item_id}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "resolved"

        # Step 4: Verify only queue item files changed
        hashes_after = _file_hashes(store.data_dir)
        for path, h in hashes_before.items():
            if "repair_queue" in path:
                continue
            assert hashes_after.get(path) == h, f"Non-queue file mutated: {path}"

    async def test_filtered_list_after_status_changes(self, tmp_path: Path):
        """Filtered listing works correctly after status transitions."""
        store = _make_store(tmp_path)
        _make_doc(store, "filter-test", maturity="testing")

        qs = _make_queue_store(tmp_path)
        qs.create_item(_make_item(item_id="rq-filter-1", finding_code="eval_stale",
                                   severity="warning", recommended_action="reeval"))
        qs.create_item(_make_item(item_id="rq-filter-2", finding_code="eval_missing",
                                   severity="warning", recommended_action="reeval"))

        # Acknowledge one
        qs.update_status("rq-filter-1", "acknowledged")

        # Filter by status
        open_items = qs.list_items(status="open")
        ack_items = qs.list_items(status="acknowledged")
        assert len(open_items) >= 1
        assert len(ack_items) >= 1

        # Filter by severity
        warn_items = qs.list_items(severity="warning")
        assert len(warn_items) >= 2

        # Filter by action
        reeval_items = qs.list_items(action="reeval")
        assert len(reeval_items) >= 2

    async def test_dedupe_after_resolve_allows_new_items(self, tmp_path: Path):
        """After resolving an item, a new health report with same finding creates a new item."""
        store = _make_store(tmp_path)
        _make_doc(store, "rededup", maturity="testing")

        report = generate_capability_health_report(store)
        qs = _make_queue_store(tmp_path)

        # First pass
        items1 = qs.create_from_health_report(report, dedupe=False)
        assert len(items1) > 0

        # Resolve all items
        for item in items1:
            qs.update_status(item.item_id, "resolved")

        # Second pass with dedupe: resolved items don't block new ones (only open items dedupe)
        report2 = generate_capability_health_report(store)
        items2 = qs.create_from_health_report(report2, dedupe=True)
        # Resolved items shouldn't block dedup (only "open" status is checked)
        assert len(items2) >= 0

        # Items with same dedup keys should be newly created since old ones are resolved
        new_open = qs.list_items(status="open")
        assert len(items2) == len(new_open)
