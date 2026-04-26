"""Sudo is denied by default — both at config load and at the shell executor.

This locks in two things:
- The shipped config defaults (config.toml + config.example.toml + the
  ShellConfig pydantic model) all set allow_sudo=false.
- The shell executor's _blocked_reason refuses to run any command that
  contains a `sudo` invocation when SHELL_ALLOW_SUDO is false.

Together these mean: a freshly-cloned Lapwing instance cannot escalate
to root via the shell tool without a deliberate operator override.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_settings_disable_sudo():
    """The pydantic ShellConfig default must be allow_sudo=False."""
    from src.config.settings import ShellConfig

    assert ShellConfig().allow_sudo is False


def test_shipped_config_toml_disables_sudo():
    """The default config.toml shipped with the repo must set allow_sudo=false."""
    text = (REPO_ROOT / "config.toml").read_text(encoding="utf-8")
    # Match the [shell] block and confirm allow_sudo is false there.
    shell_section = re.search(
        r"\[shell\][^\[]*", text, flags=re.DOTALL,
    )
    assert shell_section, "config.toml must contain a [shell] section"
    block = shell_section.group(0)
    assert re.search(r"^\s*allow_sudo\s*=\s*false", block, flags=re.MULTILINE), (
        f"config.toml [shell].allow_sudo must default to false, got block:\n{block}"
    )


def test_example_config_toml_disables_sudo():
    """config.example.toml mirrors the safe default."""
    text = (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    shell_section = re.search(
        r"\[shell\][^\[]*", text, flags=re.DOTALL,
    )
    assert shell_section
    block = shell_section.group(0)
    assert re.search(r"^\s*allow_sudo\s*=\s*false", block, flags=re.MULTILINE), (
        f"config.example.toml [shell].allow_sudo must default to false:\n{block}"
    )


class TestShellExecutorSudoBlock:
    """_blocked_reason refuses sudo when SHELL_ALLOW_SUDO is false."""

    def test_blocks_bare_sudo_command(self, monkeypatch):
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)
        reason = shell_executor._blocked_reason("sudo apt update")
        assert reason is not None
        assert "sudo" in reason

    def test_blocks_sudo_with_flags(self, monkeypatch):
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)
        reason = shell_executor._blocked_reason("sudo -E env PATH=/usr/bin ls")
        assert reason is not None
        assert "sudo" in reason

    def test_blocks_sudo_in_compound_command(self, monkeypatch):
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)
        reason = shell_executor._blocked_reason("ls && sudo systemctl restart x")
        assert reason is not None
        assert "sudo" in reason

    def test_allows_non_sudo_command(self, monkeypatch):
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)
        # Plain command must not be blocked by the sudo path. (Other guards
        # may still block it for other reasons, but the sudo gate alone
        # should not flag this.)
        reason = shell_executor._blocked_reason("ls -la /tmp")
        if reason is not None:
            assert "sudo" not in reason

    def test_allows_when_explicitly_enabled(self, monkeypatch):
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", True)
        # Sudo should not be blocked by the sudo gate when explicitly
        # enabled. Other guards (dangerous patterns, protected paths) may
        # still apply, but the bare `sudo ls` must pass the sudo gate.
        reason = shell_executor._blocked_reason("sudo ls")
        # Either no reason, or a reason unrelated to sudo.
        if reason is not None:
            assert "sudo" not in reason

    def test_does_not_match_sudo_substring(self, monkeypatch):
        """Word boundary — 'pseudo-' or 'sudoku' must not match."""
        from src.tools import shell_executor

        monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)
        # Word-boundary regex prevents false positives like pseudo-fs.
        for cmd in ("ls /var/pseudofs", "echo sudoku"):
            reason = shell_executor._blocked_reason(cmd)
            if reason is not None:
                assert "sudo" not in reason, (
                    f"unexpected sudo block on {cmd!r}: {reason!r}"
                )
