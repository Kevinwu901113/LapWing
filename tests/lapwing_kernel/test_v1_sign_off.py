"""§15.3 v1 sign-off checklist invariants.

15 items from the blueprint sign-off list. Items requiring deployment
environment (Xvfb running on PVE) or external operator action (running
the full repo suite) are documented but not asserted here — see the
test docstrings and Slice J commit message for the manual steps.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# §15.3 #5: kernel.py ≤ 150 lines


def test_kernel_py_under_150_lines():
    kernel_py = REPO_ROOT / "src" / "lapwing_kernel" / "kernel.py"
    assert kernel_py.exists()
    lines = kernel_py.read_text().splitlines()
    assert len(lines) <= 150, f"kernel.py is {len(lines)} lines; must be ≤150"


# §15.3 #6: 主面 ToolSpec < 10


def test_standard_profile_under_10_tools():
    from src.core.runtime_profiles import STANDARD_PROFILE

    assert len(STANDARD_PROFILE.tool_names) < 10


# §15.3 #7: src/ runtime code has no v0.1 forbidden names


def test_no_v0_1_names_in_src_runtime():
    """src/ must not contain ResidentRuntime / PersonalBrowserService /
    FetchBrowserService / BrowserResult / PendingUserAttentionStore /
    ResidentAuditLog — those were v0.1 names rejected by the v0.2 intent."""
    forbidden = [
        "ResidentRuntime",
        "PersonalBrowserService",
        "FetchBrowserService",
        "BrowserResult",
        "PendingUserAttentionStore",
        "ResidentAuditLog",
    ]
    src = REPO_ROOT / "src"
    hits = []
    for py in src.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(errors="ignore")
        for name in forbidden:
            if name in text:
                hits.append(f"{py.relative_to(REPO_ROOT)}: {name}")
    assert hits == [], (
        f"v0.1 names leaked into src/ runtime code: {hits} "
        f"(blueprint §17.1 / §15.3 #7)"
    )


# §15.3 #11: No adapter directly imports another adapter


def test_no_cross_adapter_imports():
    """Blueprint §7.1 / §15.3 #11: no Adapter (Resource Protocol impl)
    directly imports another Adapter. Helpers (CredentialLeaseStore,
    CredentialUseState) within the same package are explicitly allowed —
    they're the in-process handle mechanism that lets adapters coordinate
    without coupling directly.
    """
    adapters_dir = REPO_ROOT / "src" / "lapwing_kernel" / "adapters"
    if not adapters_dir.is_dir():
        return
    # Adapter files (Resource Protocol implementations) — NOT helpers
    adapter_files = ["browser.py", "credential.py"]
    adapter_module_stems = [f.replace(".py", "") for f in adapter_files]
    for fname in adapter_files:
        p = adapters_dir / fname
        if not p.exists():
            continue
        src = p.read_text()
        for sibling in adapter_module_stems:
            if sibling == fname.replace(".py", ""):
                continue
            for forbidden in (
                f"from .{sibling} import",
                f"from src.lapwing_kernel.adapters.{sibling} import",
                f"import src.lapwing_kernel.adapters.{sibling}",
            ):
                assert forbidden not in src, (
                    f"{fname} contains cross-adapter import {forbidden!r} — "
                    f"blueprint §7.1 / §15.3 #11 forbids Adapter-to-Adapter "
                    f"coupling. Use the in-process handle mechanism "
                    f"(CredentialLeaseStore) instead."
                )


# §15.3 #12: EventLog append-only — no UPDATE / DELETE


def test_event_log_append_only_invariant():
    el = REPO_ROOT / "src" / "lapwing_kernel" / "stores" / "event_log.py"
    if not el.exists():
        return
    src = el.read_text()
    for forbidden in (
        re.compile(r"UPDATE\s+events", re.IGNORECASE),
        re.compile(r"DELETE\s+FROM\s+events", re.IGNORECASE),
        re.compile(r"events\s+SET\b", re.IGNORECASE),
    ):
        assert not forbidden.search(src), (
            f"EventLog source has mutation SQL matching {forbidden.pattern}"
        )


# §15.3 #13: model_slots empty candidates → ConfigError


def test_model_slots_empty_candidates_raises():
    from src.lapwing_kernel.model_slots import ConfigError, ModelSlotResolver

    import pytest

    with pytest.raises(ConfigError):
        ModelSlotResolver.from_config(
            {"any_slot": {"tiers": [{"name": "primary", "candidates": []}]}}
        )


# §15.3 #8 / #14: capabilities subsystem unwired


def test_capabilities_enabled_is_false():
    """Slice J: capability subsystem disconnected via config flag."""
    import tomllib

    cfg_path = REPO_ROOT / "config.toml"
    cfg = tomllib.loads(cfg_path.read_text())
    capabilities = cfg.get("capabilities", {})
    assert capabilities.get("enabled") is False, (
        "capabilities.enabled must be false in v1 (blueprint §13.3)"
    )


# §15.3 #5 corollary / §12.1 wiki config explicitness


def test_wiki_config_explicit_write_paths_off():
    import tomllib

    cfg = tomllib.loads((REPO_ROOT / "config.toml").read_text())
    wiki = cfg.get("memory", {}).get("wiki", {})
    assert wiki.get("write_enabled") is False
    assert wiki.get("gate_enabled") is False
    assert wiki.get("auto_writeback") is False
    # Read paths stay open per §12.1
    assert wiki.get("enabled") is True
    assert wiki.get("context_enabled") is True


# §15.3 #15: §13.5 grep audit artifact present


def test_grep_audit_artifact_present():
    artifact = REPO_ROOT / "docs" / "refactor_v2" / "slice_j_grep_audit.md"
    assert artifact.exists(), (
        "PR-11 §13.5 grep audit artifact missing; blocks v1 sign-off"
    )
    content = artifact.read_text()
    # Audit must classify all 8 categories
    for category in (
        "Agent / tool old entries",
        "browser_*",
        "BrowserManager direct imports",
        "credential_vault direct usage",
        "ambient",
        "Capability subsystem",
        "ContextProfile",
        "ProactiveMessageGate",
    ):
        assert category in content, f"audit missing category {category!r}"


# §15.3 #2 / §15.2 I-2: CredentialLeaseStore in-memory only


def test_lease_store_no_persistence_imports():
    p = REPO_ROOT / "src" / "lapwing_kernel" / "adapters" / "credential_lease_store.py"
    if not p.exists():
        return
    src = p.read_text()
    # Strip docstrings to avoid false positives on HARD CONSTRAINT prose
    code_lines = []
    in_doc = False
    for line in src.split("\n"):
        s = line.strip()
        if s.startswith('"""'):
            in_doc = not in_doc
            if s.count('"""') >= 2:
                in_doc = False
            continue
        if in_doc or s.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    for forbidden_import in (
        "import sqlite3",
        "import aiosqlite",
        "import pickle",
        "import shelve",
        "import marshal",
    ):
        assert forbidden_import not in code


# §15.3 #1 / §15.1: closed-loop e2e test exists


def test_v1_closed_loop_e2e_present():
    p = REPO_ROOT / "tests" / "integration" / "test_v1_closed_loop.py"
    assert p.exists(), "the canonical §15.1 e2e test is missing"


# §15.3 #2 / 6 invariants present


def test_invariant_test_files_present():
    """All 6 §15.2 invariants have at least placeholder coverage."""
    base = REPO_ROOT / "tests" / "lapwing_kernel"
    expected = [
        "test_redaction.py",  # I-2
        "test_credential_lease_store.py",  # I-2
        "test_main_surface_lt_10.py",  # I-4
        "test_continuation_registry.py",  # I-6
        "test_invariants.py",  # I-5 + placeholders
        "test_browser_adapter.py",  # I-3 (no auto-bypass)
    ]
    for f in expected:
        assert (base / f).exists(), f"missing invariant test file: {f}"
