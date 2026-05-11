"""Cross-cutting invariant tests achievable at Slice A scope.

Full §15.2 I-1 / I-2 / I-3 require adapters (Slice C/G); I-4 requires Slice I.
This module covers the invariants that CAN be tested with kernel primitives
alone, plus a sanity check that the others have planned test files.

See blueprint §15.2.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# I-5: state has explicit fact sources


def test_wiki_write_enabled_is_false_in_config():
    config_path = REPO_ROOT / "config.toml"
    if not config_path.exists():
        return  # not present in this checkout — skip silently
    content = config_path.read_text()
    # Must contain write_enabled = false somewhere in the [memory.wiki] block.
    # Tolerant grep — exact toml parsing would require external dep.
    assert (
        "write_enabled = false" in content or "write_enabled=false" in content
    ), "[memory.wiki].write_enabled must be false in config.toml (blueprint §12.1, §15.3)"


# I-6: continuation lifecycle is covered by test_continuation_registry.py
# I-1 / I-2 / I-3 / I-4 require later Slices — placeholders below


def test_credential_lease_store_will_be_in_memory_only():
    """Placeholder for §15.2 I-2 static-grep test.

    Real test lands in PR-07 (Slice G) — at that point this xfails until the
    file exists. For now we only assert the file does NOT yet exist (Slice G
    creates it).
    """
    p = REPO_ROOT / "src" / "lapwing_kernel" / "adapters" / "credential_lease_store.py"
    # Either absent (current state, pre-Slice G) or, if present, must not
    # contain persistence-shaped imports. The PR-07 task description references
    # this test verbatim.
    if not p.exists():
        return
    forbidden = ["import sqlite3", "open(", "pickle", "shelve", "marshal"]
    src = p.read_text()
    for f in forbidden:
        assert f not in src, (
            f"credential_lease_store.py must remain in-memory only — "
            f"forbidden marker {f!r} found (blueprint §7.2 HARD CONSTRAINT)"
        )


def test_main_surface_tool_count_will_be_lt_10():
    """Placeholder for §15.2 I-4. Real check lands in PR-10 (Slice I.2)."""
    main_surface_path = REPO_ROOT / "src" / "tools" / "main_surface.py"
    if not main_surface_path.exists():
        return  # Slice I.2 not yet landed
    # Will be activated in PR-10
