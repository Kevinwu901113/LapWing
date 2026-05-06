"""Phase 7A tests: tool registration gating, permission tags, dry_run behaviour, clean errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.store import CapabilityStore
from src.tools.capability_tools import register_capability_import_tools
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


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


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _write_package(dir_path: Path, **overrides) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": "tool_test_pkg", "name": "Tool Test", "description": "Testing tools.",
        "type": "skill", "scope": "user", "version": "0.1.0",
        "maturity": "draft", "status": "active", "risk_level": "low",
        "triggers": [], "tags": [],
    }
    fm.update(overrides)
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = f"---\n{fm_yaml}\n---\n\n## When to use\nTool test.\n\n## Procedure\n1. Test\n\n## Verification\nPass.\n\n## Failure handling\nRetry."
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (dir_path / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items() if k not in ("version",)
    }, indent=2), encoding="utf-8")
    return dir_path


@pytest.fixture
def registry():
    return _FakeRegistry()


@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


@pytest.fixture
def evaluator():
    return CapabilityEvaluator()


@pytest.fixture
def policy():
    return CapabilityPolicy()


# ── Registration ──────────────────────────────────────────────────────


class TestRegistration:
    def test_both_tools_registered(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        assert "inspect_capability_package" in registry
        assert "import_capability_package" in registry

    def test_tools_have_correct_capability_tag(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        for name in ("inspect_capability_package", "import_capability_package"):
            assert registry.get(name).capability == "capability_import_operator"

    def test_risk_levels(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        assert registry.get("inspect_capability_package").risk_level == "low"
        assert registry.get("import_capability_package").risk_level == "medium"

    def test_none_store_skips_registration(self, registry, evaluator, policy):
        register_capability_import_tools(registry, None, None, evaluator, policy)
        assert "inspect_capability_package" not in registry

    def test_none_evaluator_skips_registration(self, registry, store, policy):
        register_capability_import_tools(registry, store, None, None, policy)
        assert "inspect_capability_package" not in registry

    def test_none_policy_skips_registration(self, registry, store, evaluator):
        register_capability_import_tools(registry, store, None, evaluator, None)
        assert "inspect_capability_package" not in registry


# ── Inspect tool ──────────────────────────────────────────────────────


class TestInspectTool:
    async def test_inspect_valid_package(self, registry, store, evaluator, policy, tmp_path):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        pkg = _write_package(tmp_path / "insp_pkg")
        spec = registry.get("inspect_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={"path": str(pkg)}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["id"] == "tool_test_pkg"
        assert result.payload["would_import"] is True

    async def test_inspect_missing_path(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        spec = registry.get("inspect_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_inspect_bad_path(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        spec = registry.get("inspect_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={"path": "/nope/not/here"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_inspect_with_scope(self, registry, store, evaluator, policy, tmp_path):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        pkg = _write_package(tmp_path / "scope_tool_pkg")
        spec = registry.get("inspect_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={"path": str(pkg), "scope": "workspace"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["target_scope"] == "workspace"


# ── Import tool ───────────────────────────────────────────────────────


class TestImportTool:
    async def test_import_dry_run(self, registry, store, evaluator, policy, tmp_path):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        pkg = _write_package(tmp_path / "imp_dry_pkg")
        spec = registry.get("import_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={"path": str(pkg), "dry_run": True}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert result.success
        assert result.payload["dry_run"] is True
        assert result.payload["applied"] is False

    async def test_import_missing_path(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        spec = registry.get("import_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success

    async def test_import_errors_are_clean(self, registry, store, evaluator, policy):
        """Errors must be clean strings — no stack traces."""
        register_capability_import_tools(registry, store, None, evaluator, policy)
        spec = registry.get("import_capability_package")
        result = await spec.executor(
            ToolExecutionRequest(name="test_tool", arguments={"path": "/nope/not/here"}),
            ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp"),
        )
        assert not result.success
        err = result.payload.get("error", "")
        assert isinstance(err, str)
        assert "Traceback" not in err and "traceback" not in result.reason.lower()


# ── Prohibited tool names ─────────────────────────────────────────────


class TestProhibitedTools:
    def test_no_prohibited_tools_registered(self, registry, store, evaluator, policy):
        register_capability_import_tools(registry, store, None, evaluator, policy)
        prohibited = [
            "install_capability", "run_imported_capability",
            "promote_imported_capability", "auto_install_capability",
            "registry_search_capability", "update_capability_from_remote",
        ]
        for name in prohibited:
            assert name not in registry, f"Prohibited '{name}' must not be registered"
