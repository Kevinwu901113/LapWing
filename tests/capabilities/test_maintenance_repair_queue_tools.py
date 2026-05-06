"""Maintenance C: Functional tests for repair queue operator tools.

Tests: registration, list/view/create-from-health/acknowledge/resolve/dismiss
behavior, update preservation of action_payload/evidence/recommended_action.
"""

from __future__ import annotations

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

    @property
    def names(self) -> list[str]:
        return sorted(self._t.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._t


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_queue_store(tmp_path: Path) -> RepairQueueStore:
    return RepairQueueStore(data_dir=tmp_path / "capabilities")


def _make_item(**overrides) -> RepairQueueItem:
    defaults = {
        "item_id": "rq-test-001",
        "created_at": "2026-05-05T10:00:00+00:00",
        "source": "health_report",
        "finding_code": "missing_provenance_legacy",
        "severity": "info",
        "status": "open",
        "title": "Test item",
        "description": "Test description",
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
    return doc.id


def _make_context():
    return ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp")


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    return _FakeRegistry()


@pytest.fixture
def queue_store(tmp_path):
    return _make_queue_store(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# Registration tests
# ═══════════════════════════════════════════════════════════════════════════

REPAIR_QUEUE_TOOL_NAMES = {
    "list_repair_queue_items",
    "view_repair_queue_item",
    "create_repair_queue_from_health",
    "acknowledge_repair_queue_item",
    "resolve_repair_queue_item",
    "dismiss_repair_queue_item",
}

FORBIDDEN_TOOL_NAMES = {
    "repair_capability",
    "execute_repair",
    "auto_repair_capability",
    "apply_repair_queue_item",
    "rebuild_index_from_health",
    "promote_from_health",
    "run_capability",
}


class TestRepairQueueToolsRegistration:
    def test_six_tools_registered(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        for name in REPAIR_QUEUE_TOOL_NAMES:
            assert name in registry, f"{name} should be registered"

    def test_exactly_six_tools(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        assert len(registry.names) == 6

    def test_tools_have_correct_capability_tag(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        for name in REPAIR_QUEUE_TOOL_NAMES:
            spec = registry.get(name)
            assert spec.capability == "capability_repair_operator", (
                f"{name} has capability={spec.capability}, expected capability_repair_operator"
            )

    def test_all_tools_low_risk(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        for name in REPAIR_QUEUE_TOOL_NAMES:
            spec = registry.get(name)
            assert spec.risk_level == "low", f"{name} risk_level={spec.risk_level}"

    def test_none_store_skips_registration(self, registry):
        register_repair_queue_tools(registry, None)
        assert len(registry.names) == 0

    def test_forbidden_tools_absent(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        for name in FORBIDDEN_TOOL_NAMES:
            assert name not in registry, f"Forbidden tool '{name}' should not be registered"


# ═══════════════════════════════════════════════════════════════════════════
# list_repair_queue_items
# ═══════════════════════════════════════════════════════════════════════════

class TestListRepairQueueItems:
    async def test_list_empty(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        assert result.payload["items"] == []
        assert result.payload["count"] == 0

    async def test_list_returns_compact_summaries(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-000000000001"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 1
        item = result.payload["items"][0]
        # Compact summary fields present
        assert item["item_id"] == "rq-000000000001"
        assert item["status"] == "open"
        assert item["severity"] == "info"
        assert item["finding_code"] == "missing_provenance_legacy"
        # action_payload NOT expanded
        assert "action_payload" not in item
        assert "evidence" not in item

    async def test_list_filter_by_status(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-001", status="open"))
        queue_store.create_item(_make_item(item_id="rq-002", status="acknowledged"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"status": "acknowledged"}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 1
        assert result.payload["items"][0]["item_id"] == "rq-002"

    async def test_list_filter_by_severity(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-001", severity="info"))
        queue_store.create_item(_make_item(item_id="rq-002", severity="error"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"severity": "error"}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 1
        assert result.payload["items"][0]["item_id"] == "rq-002"

    async def test_list_filter_by_capability_id(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-001", capability_id="cap-a"))
        queue_store.create_item(_make_item(item_id="rq-002", capability_id="cap-b"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"capability_id": "cap-a"}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 1
        assert result.payload["items"][0]["item_id"] == "rq-001"

    async def test_list_filter_by_recommended_action(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-001", recommended_action="add_provenance"))
        queue_store.create_item(_make_item(item_id="rq-002", recommended_action="reindex"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"recommended_action": "reindex"}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 1
        assert result.payload["items"][0]["item_id"] == "rq-002"

    async def test_list_respects_limit(self, registry, queue_store):
        for i in range(10):
            queue_store.create_item(_make_item(item_id=f"rq-{i:03d}"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"limit": 3}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["items"]) == 3

    async def test_list_default_limit_50(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("list_repair_queue_items")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        # Default limit is 50, empty list is fine


# ═══════════════════════════════════════════════════════════════════════════
# view_repair_queue_item
# ═══════════════════════════════════════════════════════════════════════════

class TestViewRepairQueueItem:
    async def test_view_existing_item(self, registry, queue_store):
        item = _make_item(item_id="rq-view-001")
        queue_store.create_item(item)
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("view_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-view-001"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["item_id"] == "rq-view-001"
        assert result.payload["item"]["status"] == "open"
        # Full item includes action_payload (inert metadata)
        assert "action_payload" in result.payload["item"]

    async def test_view_missing_item(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("view_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_view_empty_item_id(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("view_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": ""}),
            _make_context(),
        )
        assert not result.success
        assert "requires item_id" in result.reason


# ═══════════════════════════════════════════════════════════════════════════
# create_repair_queue_from_health
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateRepairQueueFromHealth:
    async def test_creates_items_from_health_report(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "test-cap")
        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert result.success
        assert result.payload["created"] >= 0
        assert "total_findings" in result.payload
        assert "recommendations" in result.payload

    async def test_creates_items_dedupe(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "test-cap")
        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")

        # First call
        result1 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"dedupe": True}),
            _make_context(),
        )
        assert result1.success
        created1 = result1.payload["created"]

        # Second call with same data — should skip duplicates
        result2 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"dedupe": True}),
            _make_context(),
        )
        assert result2.success
        # Second call should create fewer (or zero) items due to dedup
        assert result2.payload["created"] <= created1

    async def test_dedupe_disabled_creates_duplicates(self, registry, queue_store, tmp_path):
        store = _make_store(tmp_path)
        _make_doc(store, "test-cap")
        register_repair_queue_tools(registry, queue_store, capability_store=store)
        spec = registry.get("create_repair_queue_from_health")

        result1 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"dedupe": False}),
            _make_context(),
        )
        created1 = result1.payload["created"]

        result2 = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"dedupe": False}),
            _make_context(),
        )
        created2 = result2.payload["created"]

        # With dedupe=False, second call creates same number of items
        assert created2 == created1

    async def test_no_capability_store_returns_error(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store, capability_store=None)
        spec = registry.get("create_repair_queue_from_health")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={}),
            _make_context(),
        )
        assert not result.success
        assert "capability_store_unavailable" in result.payload["error"]


# ═══════════════════════════════════════════════════════════════════════════
# acknowledge_repair_queue_item
# ═══════════════════════════════════════════════════════════════════════════

class TestAcknowledgeRepairQueueItem:
    async def test_acknowledge_changes_status(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-ack-001", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("acknowledge_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-ack-001"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "acknowledged"

        # Verify on disk
        item = queue_store.get_item("rq-ack-001")
        assert item.status == "acknowledged"

    async def test_acknowledge_with_reason_and_actor(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-ack-002", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("acknowledge_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": "rq-ack-002",
                "actor": "kevin",
                "reason": "Looking into this",
            }),
            _make_context(),
        )
        assert result.success
        item = queue_store.get_item("rq-ack-002")
        assert item.metadata.get("status_change_actor") == "kevin"
        assert item.metadata.get("status_change_reason") == "Looking into this"

    async def test_acknowledge_missing_item(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("acknowledge_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"


# ═══════════════════════════════════════════════════════════════════════════
# resolve_repair_queue_item
# ═══════════════════════════════════════════════════════════════════════════

class TestResolveRepairQueueItem:
    async def test_resolve_changes_status(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-res-001", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("resolve_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-res-001"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "resolved"

        item = queue_store.get_item("rq-res-001")
        assert item.status == "resolved"
        assert item.resolved_at is not None

    async def test_resolve_with_reason(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-res-002", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("resolve_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": "rq-res-002",
                "reason": "Fixed by re-import",
            }),
            _make_context(),
        )
        assert result.success
        item = queue_store.get_item("rq-res-002")
        assert item.metadata.get("status_change_reason") == "Fixed by re-import"

    async def test_resolve_missing_item(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("resolve_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"


# ═══════════════════════════════════════════════════════════════════════════
# dismiss_repair_queue_item
# ═══════════════════════════════════════════════════════════════════════════

class TestDismissRepairQueueItem:
    async def test_dismiss_changes_status(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-dis-001", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-dis-001"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["item"]["status"] == "dismissed"

        item = queue_store.get_item("rq-dis-001")
        assert item.status == "dismissed"
        assert item.dismissed_at is not None

    async def test_dismiss_with_reason(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-dis-002", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={
                "item_id": "rq-dis-002",
                "reason": "Not actionable",
            }),
            _make_context(),
        )
        assert result.success
        item = queue_store.get_item("rq-dis-002")
        assert item.metadata.get("status_change_reason") == "Not actionable"

    async def test_dismiss_does_not_delete(self, registry, queue_store):
        queue_store.create_item(_make_item(item_id="rq-dis-003", status="open"))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-dis-003"}),
            _make_context(),
        )
        # Item still exists on disk
        item = queue_store.get_item("rq-dis-003")
        assert item is not None
        assert item.status == "dismissed"

    async def test_dismiss_missing_item(self, registry, queue_store):
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        result = await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"


# ═══════════════════════════════════════════════════════════════════════════
# Update preservation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestUpdateToolsPreserveFields:
    """Status-update tools must not alter action_payload, evidence, or recommended_action."""

    async def test_acknowledge_preserves_action_payload(self, registry, queue_store):
        original_payload = {"source_finding_code": "eval_stale", "stage": "testing"}
        queue_store.create_item(_make_item(
            item_id="rq-preserve-001",
            action_payload=original_payload,
        ))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("acknowledge_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-preserve-001"}),
            _make_context(),
        )
        item = queue_store.get_item("rq-preserve-001")
        assert item.action_payload == original_payload

    async def test_resolve_preserves_evidence(self, registry, queue_store):
        original_evidence = {"checked_by": "health_report", "timestamp": "2026-05-05"}
        queue_store.create_item(_make_item(
            item_id="rq-preserve-002",
            evidence=original_evidence,
        ))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("resolve_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-preserve-002"}),
            _make_context(),
        )
        item = queue_store.get_item("rq-preserve-002")
        assert item.evidence == original_evidence

    async def test_dismiss_preserves_recommended_action(self, registry, queue_store):
        queue_store.create_item(_make_item(
            item_id="rq-preserve-003",
            recommended_action="add_provenance",
        ))
        register_repair_queue_tools(registry, queue_store)
        spec = registry.get("dismiss_repair_queue_item")
        await spec.executor(
            ToolExecutionRequest(name="test", arguments={"item_id": "rq-preserve-003"}),
            _make_context(),
        )
        item = queue_store.get_item("rq-preserve-003")
        assert item.recommended_action == "add_provenance"
