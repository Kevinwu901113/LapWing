"""Phase 3B regression tests.

Verify Phase 3B does not break:
- Phase 0/1/2A/2B/3A tests
- Read-only capability tools
- No write tools exist
- No run_capability exists
- Legacy skill promote behavior unchanged
- Runtime imports remain limited
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_pytest(test_path: str, *, extra_args: list[str] | None = None) -> tuple[int, str]:
    args = [sys.executable, "-m", "pytest", test_path, "-q", "--tb=short"]
    if extra_args:
        args.extend(extra_args)
    result = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + "\n" + result.stderr


class TestPhase0Regression:
    def test_phase0_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase0_regression.py")
        assert rc == 0, f"Phase 0 regression failed:\n{output}"


class TestPhase1Regression:
    def test_phase1_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase1_parsing.py")
        assert rc == 0, f"Phase 1 regression failed:\n{output}"


class TestPhase2ARegression:
    def test_phase2a_store_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase2_store.py")
        assert rc == 0, f"Phase 2A store regression failed:\n{output}"

    def test_phase2a_index_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase2_index.py")
        assert rc == 0, f"Phase 2A index regression failed:\n{output}"

    def test_phase2a_search_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase2_search.py")
        assert rc == 0, f"Phase 2A search regression failed:\n{output}"

    def test_phase2a_versioning_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase2_versioning.py")
        assert rc == 0, f"Phase 2A versioning regression failed:\n{output}"


class TestPhase2BRegression:
    def test_phase2b_tools_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase2b_tools.py")
        assert rc == 0, f"Phase 2B tools regression failed:\n{output}"


class TestPhase3ARegression:
    def test_phase3a_policy_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase3a_policy.py")
        assert rc == 0, f"Phase 3A policy regression failed:\n{output}"

    def test_phase3a_evaluator_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase3a_evaluator.py")
        assert rc == 0, f"Phase 3A evaluator regression failed:\n{output}"

    def test_phase3a_eval_records_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase3a_eval_records.py")
        assert rc == 0, f"Phase 3A eval records regression failed:\n{output}"

    def test_phase3a_promotion_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase3a_promotion.py")
        assert rc == 0, f"Phase 3A promotion regression failed:\n{output}"

    def test_phase3a_hardening_tests_pass(self):
        rc, output = _run_pytest("tests/capabilities/test_phase3a_hardening.py")
        assert rc == 0, f"Phase 3A hardening regression failed:\n{output}"


class TestReadOnlyTools:
    def test_no_write_tools_exist(self):
        """Verify no write capability tools (create, disable, archive, promote, run)."""
        tools_file = REPO_ROOT / "src" / "tools" / "capability_tools.py"
        content = tools_file.read_text(encoding="utf-8")

        forbidden = [
            "create_capability",
            "disable_capability",
            "archive_capability",
            "promote_capability",
            "run_capability",
        ]
        for name in forbidden:
            assert f'"{name}"' not in content, f"Write tool '{name}' found in capability_tools.py"

    def test_no_run_capability_exists(self):
        """Verify no run_capability tool anywhere in src/."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "run_capability", str(REPO_ROOT / "src")],
            capture_output=True, text=True,
        )
        # run_capability should only appear in capability_tools.py as a comment
        # about it not existing, or in docs about Phase 3B
        for line in result.stdout.splitlines():
            if line.strip().startswith("#") or "docstring" in line.lower():
                continue
            # The only allowed mention is in policy/evaluator docstrings stating it's NOT implemented
            if "not" in line.lower() or "no" in line.lower() or "never" in line.lower():
                continue
            assert False, f"run_capability reference found: {line}"


class TestLegacyUnchanged:
    def test_legacy_skills_tests_pass(self):
        rc, output = _run_pytest("tests/skills/")
        assert rc == 0, f"Legacy skills tests failed:\n{output}"

    def test_legacy_agents_tests_pass(self):
        rc, output = _run_pytest("tests/agents/")
        assert rc == 0, f"Legacy agents tests failed:\n{output}"

    def test_tool_dispatcher_tests_pass(self):
        rc, output = _run_pytest("tests/core/test_tool_dispatcher.py")
        assert rc == 0, f"ToolDispatcher tests failed:\n{output}"

    def test_mutation_log_tests_pass(self):
        rc, output = _run_pytest("tests/logging/")
        assert rc == 0, f"MutationLog tests failed:\n{output}"

    def test_state_view_tests_pass(self):
        rc, output = _run_pytest(
            "tests/core/test_state_view_builder.py",
        )
        if rc == 0:
            rc2, output2 = _run_pytest(
                "tests/core/test_stateview_agent_summary.py",
            )
            rc = rc2
            output = output2
        assert rc == 0, f"StateView tests failed:\n{output}"


class TestRuntimeImports:
    def test_only_allowed_capability_imports(self):
        """Verify only capability_tools.py and container.py import src.capabilities."""
        result = subprocess.run(
            ["grep", "-rn", r"from src\.capabilities\|import src\.capabilities",
             str(REPO_ROOT / "src")],
            capture_output=True, text=True,
        )
        allowed_files = {
            "src/tools/capability_tools.py",
            "src/app/container.py",
        }
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            file_part = line.split(":")[0]
            # Normalize to relative path
            rel_path = Path(file_part).relative_to(REPO_ROOT) if Path(file_part).is_absolute() else file_part
            rel_str = str(rel_path)
            # Skip files within src/capabilities/ itself
            if rel_str.startswith("src/capabilities/"):
                continue
            assert rel_str in allowed_files, (
                f"Unauthorized import in {rel_str}: {line}"
            )
