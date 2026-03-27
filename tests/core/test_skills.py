"""Skills 子系统测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.skills import SkillManager


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str,
    metadata: dict | None = None,
    user_invocable: bool | None = None,
    disable_model_invocation: bool | None = None,
    command_dispatch: str | None = None,
    command_tool: str | None = None,
    command_arg_mode: str | None = None,
    body: str = "# Demo\n\nDo something.",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if user_invocable is not None:
        lines.append(f"user-invocable: {'true' if user_invocable else 'false'}")
    if disable_model_invocation is not None:
        lines.append(
            "disable-model-invocation: "
            f"{'true' if disable_model_invocation else 'false'}"
        )
    if command_dispatch is not None:
        lines.append(f"command-dispatch: {command_dispatch}")
    if command_tool is not None:
        lines.append(f"command-tool: {command_tool}")
    if command_arg_mode is not None:
        lines.append(f"command-arg-mode: {command_arg_mode}")
    if metadata is not None:
        lines.append(f"metadata: {json.dumps(metadata, ensure_ascii=False)}")
    lines.append("---")
    lines.append(body)

    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


def _build_manager(
    *,
    workspace: Path,
    managed: Path,
    bundled: Path,
    extra: list[Path] | None = None,
) -> SkillManager:
    return SkillManager(
        enabled=True,
        workspace_dir=workspace,
        managed_dir=managed,
        bundled_dir=bundled,
        extra_dirs=extra or [],
    )


def test_precedence_workspace_overrides_managed_bundled_and_extra(tmp_path: Path):
    workspace = tmp_path / "workspace_skills"
    managed = tmp_path / "managed_skills"
    bundled = tmp_path / "bundled_skills"
    extra = tmp_path / "extra_skills"

    _write_skill(extra, "demo", description="extra")
    _write_skill(bundled, "demo", description="bundled")
    _write_skill(managed, "demo", description="managed")
    _write_skill(workspace, "demo", description="workspace")

    manager = _build_manager(
        workspace=workspace,
        managed=managed,
        bundled=bundled,
        extra=[extra],
    )
    manager.reload()

    skill = manager.get("demo")
    assert skill is not None
    assert skill.description == "workspace"
    assert skill.source == "workspace"


def test_invalid_frontmatter_is_skipped(tmp_path: Path):
    workspace = tmp_path / "workspace_skills"
    managed = tmp_path / "managed_skills"
    bundled = tmp_path / "bundled_skills"

    bad_dir = workspace / "bad-skill"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "SKILL.md").write_text(
        """---
name: bad-skill
---
no description
""",
        encoding="utf-8",
    )

    manager = _build_manager(workspace=workspace, managed=managed, bundled=bundled)
    manager.reload()

    assert manager.get("bad-skill") is None


def test_realpath_escape_is_skipped(tmp_path: Path):
    workspace = tmp_path / "workspace_skills"
    managed = tmp_path / "managed_skills"
    bundled = tmp_path / "bundled_skills"
    workspace.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside"
    _write_skill(outside, "escape", description="outside")

    # workspace/escape -> outside/escape（符号链接越界）
    (workspace / "escape").symlink_to(outside / "escape", target_is_directory=True)

    manager = _build_manager(workspace=workspace, managed=managed, bundled=bundled)
    manager.reload()

    assert manager.get("escape") is None


def test_requires_env_and_bins_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace_skills"
    managed = tmp_path / "managed_skills"
    bundled = tmp_path / "bundled_skills"

    _write_skill(
        workspace,
        "env-required",
        description="need env",
        metadata={"openclaw": {"requires": {"env": ["MY_SKILL_KEY"]}}},
    )
    _write_skill(
        workspace,
        "bin-required",
        description="need bin",
        metadata={"openclaw": {"requires": {"bins": ["definitely-not-a-real-bin"]}}},
    )

    manager = _build_manager(workspace=workspace, managed=managed, bundled=bundled)
    manager.reload()

    assert manager.get("env-required") is None
    assert manager.get("bin-required") is None

    monkeypatch.setenv("MY_SKILL_KEY", "ok")
    manager.reload()

    assert manager.get("env-required") is not None
    assert manager.get("bin-required") is None


def test_catalog_hides_disable_model_invocation_and_activate_returns_resources(tmp_path: Path):
    workspace = tmp_path / "workspace_skills"
    managed = tmp_path / "managed_skills"
    bundled = tmp_path / "bundled_skills"

    _write_skill(
        workspace,
        "hidden-skill",
        description="hidden",
        disable_model_invocation=True,
    )
    visible = _write_skill(
        workspace,
        "visible-skill",
        description="visible",
        body="""# Visible\n\nStep A\n""",
    )
    (visible / "scripts").mkdir(parents=True, exist_ok=True)
    (visible / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")

    manager = _build_manager(workspace=workspace, managed=managed, bundled=bundled)
    manager.reload()

    catalog = manager.render_catalog_for_prompt()
    assert "visible-skill" in catalog
    assert "hidden-skill" not in catalog
    assert manager.get("hidden-skill") is not None

    activated = manager.activate("visible-skill", user_input="do it")
    assert activated["skill_name"] == "visible-skill"
    assert "Step A" in activated["content"]
    assert "scripts/run.sh" in activated["resources"]
    assert "<skill_content name=\"visible-skill\">" in activated["wrapped_content"]
