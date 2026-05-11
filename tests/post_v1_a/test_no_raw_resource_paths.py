"""V-A2: workers don't reach around the kernel to touch resources directly.

Post-v1 A §5 V-A2 acceptance test. Static grep over src/agents/ — any file
that directly imports a BrowserAdapter / CredentialAdapter class would
mean the worker bypasses the kernel's Action pipeline. Workers must access
resources via kernel.resources.get(name, profile) instead.

The spec's first V-A2 grep target (`src/tools/main_surface*`) doesn't
exist as a literal filename in this repo — main surface composition lives
in STANDARD_PROFILE.tool_names (see test_main_surface_lt_10.py), so V-A1
already enforces that invariant. This file covers the §2.2/§2.3 adapter-
import prohibition for agent workers.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_direct_adapter_imports_in_agents():
    """src/agents/* must not import BrowserAdapter / CredentialAdapter
    classes directly. Workers receive the kernel via services and call
    kernel.resources.get(...) so the Adapter handle is fetched at runtime."""
    agents_dir = REPO_ROOT / "src" / "agents"
    forbidden = (
        "from src.lapwing_kernel.adapters.browser import",
        "from src.lapwing_kernel.adapters.credential import",
    )
    hits: list[str] = []
    for py in agents_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            for f in forbidden:
                if stripped.startswith(f):
                    hits.append(f"{py.relative_to(REPO_ROOT)}: {stripped}")
    assert hits == [], (
        "Agent workers must not import adapter classes directly. "
        "Use kernel.resources.get(name, profile) instead.\n  " + "\n  ".join(hits)
    )
