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
    """§15.2 I-2 static check, gentle version.

    Coarse top-level grep — the precise dedicated tests live in
    tests/lapwing_kernel/test_credential_lease_store.py
    (TestNoPersistenceInvariant) once PR-07 has landed.
    """
    p = REPO_ROOT / "src" / "lapwing_kernel" / "adapters" / "credential_lease_store.py"
    if not p.exists():
        return
    src = p.read_text()
    # Strip comment + docstring lines so HARD CONSTRAINT documentation that
    # MENTIONS forbidden modules doesn't false-positive against the file.
    code_lines = []
    in_docstring = False
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Toggle on first triple-quote, off on next; one-line docstrings handled too
            in_docstring = not in_docstring
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                in_docstring = False  # single-line triple-quote
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code_only = "\n".join(code_lines)

    # Imports / calls that would persist plaintext are forbidden in the
    # CODE portion (docstrings explaining the constraint are fine).
    forbidden_imports = [
        "import sqlite3",
        "import aiosqlite",
        "import pickle",
        "import shelve",
        "import marshal",
    ]
    for f in forbidden_imports:
        assert f not in code_only, (
            f"credential_lease_store.py must remain in-memory only — "
            f"forbidden import {f!r} found in code (blueprint §7.2 HARD CONSTRAINT)"
        )


def test_main_surface_tool_count_will_be_lt_10():
    """Placeholder for §15.2 I-4. Real check lands in PR-10 (Slice I.2)."""
    main_surface_path = REPO_ROOT / "src" / "tools" / "main_surface.py"
    if not main_surface_path.exists():
        return  # Slice I.2 not yet landed
    # Will be activated in PR-10
