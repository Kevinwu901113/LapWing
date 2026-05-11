"""V-B2: personal browser launches on Xvfb :99.

This is a host-bound integration smoke. CI does not have Xvfb / Chrome /
Playwright system deps, so the test auto-skips unless the host satisfies
all three preconditions:

  1. Xvfb is running on :99 (or `XVFB_DISPLAY` env var overrides).
  2. `xdpyinfo` reports the display geometry.
  3. Playwright + chromium are installed (`playwright install chromium`).

Run locally on the PVE host after `install_pve.sh`:

  sudo systemctl start lapwing-xvfb.service
  DISPLAY=:99 pytest tests/post_v1_b/test_personal_browser_starts.py -v

Post-v1 B §5 V-B2 specifies the spec; this file covers what can be
machine-verified. V-B1 / V-B3-B8 are operator hand-tests documented in
`docs/operations/personal_browser_takeover.md`.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest


DISPLAY = os.environ.get("XVFB_DISPLAY", ":99")


def _xvfb_alive() -> bool:
    if not shutil.which("xdpyinfo"):
        return False
    try:
        result = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _playwright_ready() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return False
    return True


_HOST_INTEGRATION_SKIP = pytest.mark.skipif(
    not (_xvfb_alive() and _playwright_ready()),
    reason=(
        f"requires Xvfb on {DISPLAY} (lapwing-xvfb.service) and "
        "playwright + chromium installed"
    ),
)


@_HOST_INTEGRATION_SKIP
async def test_headful_chrome_launches_on_xvfb(tmp_path: Path):
    """V-B2: launch_persistent_context(headless=False) succeeds against
    the configured Xvfb display and renders a real page.

    Mirrors the BrowserManager personal-profile launch pattern (see
    src/core/browser_manager.py:610) without booting the full Lapwing
    container — sufficient for confirming the OS-level stack works.
    """
    from playwright.async_api import async_playwright

    os.environ["DISPLAY"] = DISPLAY
    user_data = tmp_path / "personal_smoke"
    user_data.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=False,
            viewport={"width": 1280, "height": 720},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            page = await context.new_page()
            await page.goto("about:blank")
            assert page.url == "about:blank"
            title = await page.title()
            # about:blank has no title; assertion is that the call returns
            # rather than raises — a black/dead Xvfb would hang.
            assert title is not None
        finally:
            await context.close()


def test_systemd_units_present_on_disk():
    """Catches install_pve.sh regressions: the three unit files must
    exist with valid section markers. Not a runtime check — runs on any
    host (including CI)."""
    repo_root = Path(__file__).resolve().parents[2]
    unit_dir = repo_root / "ops" / "systemd"

    units = {
        "lapwing-xvfb.service": ["[Unit]", "[Service]", "[Install]", "Xvfb"],
        "lapwing-x11vnc.service": ["[Unit]", "[Service]", "[Install]", "x11vnc"],
        "lapwing.service": ["[Unit]", "[Service]", "[Install]", "DISPLAY=:99"],
    }
    for name, must_contain in units.items():
        path = unit_dir / name
        assert path.exists(), f"{path} missing"
        text = path.read_text()
        for token in must_contain:
            assert token in text, f"{name}: missing {token!r}"


def test_install_scripts_executable():
    """install_pve.sh / uninstall_pve.sh shipped with exec bit set."""
    repo_root = Path(__file__).resolve().parents[2]
    scripts = repo_root / "ops" / "scripts"
    for name in ("install_pve.sh", "uninstall_pve.sh"):
        path = scripts / name
        assert path.exists(), f"{path} missing"
        assert os.access(path, os.X_OK), f"{name} not executable"
        text = path.read_text()
        assert text.startswith("#!/usr/bin/env bash"), f"{name} shebang wrong"
        assert "set -euo pipefail" in text, f"{name} missing strict mode"
