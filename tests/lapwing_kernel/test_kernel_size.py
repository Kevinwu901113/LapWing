"""kernel.py composition root must stay ≤150 lines.

Blueprint §4.1 / §15.3 #5. If this test fails, business logic has leaked
into the composition root.
"""
from __future__ import annotations

from pathlib import Path

KERNEL_PY = (
    Path(__file__).resolve().parents[2] / "src" / "lapwing_kernel" / "kernel.py"
)
MAX_LINES = 150


def test_kernel_py_exists():
    assert KERNEL_PY.exists(), f"{KERNEL_PY} not found"


def test_kernel_py_under_150_lines():
    line_count = sum(1 for _ in KERNEL_PY.read_text().splitlines())
    assert (
        line_count <= MAX_LINES
    ), f"kernel.py is {line_count} lines, must be ≤{MAX_LINES} (blueprint §4.1)"


def test_kernel_py_no_business_logic_markers():
    """Composition root must not import business-logic modules directly."""
    src = KERNEL_PY.read_text()
    forbidden_imports = [
        "from .adapters.browser",
        "from .adapters.credential",
        "import playwright",
        "from .stores.interrupt_store",  # only Protocol from executor allowed
        "from .stores.event_log",
    ]
    for f in forbidden_imports:
        assert f not in src, (
            f"kernel.py contains forbidden import '{f}' — composition root "
            "must not directly import business-logic modules; wire them via "
            "the build_kernel factory or by accepting Protocols."
        )
