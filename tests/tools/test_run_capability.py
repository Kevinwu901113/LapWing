"""run_capability tool tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.capabilities.eval_records import write_eval_record
from src.capabilities.evaluator import EvalRecord
from src.capabilities.store import CapabilityStore
from src.eval.axes import AxisResult, AxisStatus, EvalAxis
from src.tools.capability_tools import register_capability_runner_tools
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class FakeSkillResult:
    success = True
    output = '{"ok": true}'
    error = ""
    exit_code = 0
    timed_out = False


class FakeSkillExecutor:
    def __init__(self):
        self.calls = []
        self.directory_calls = []

    async def execute(self, skill_id, arguments=None, timeout=30):
        self.calls.append((skill_id, arguments, timeout))
        return FakeSkillResult()

    async def execute_directory(
        self,
        directory,
        entry_script,
        arguments=None,
        timeout=None,
        capability_context=None,
    ):
        self.directory_calls.append((directory, entry_script, arguments, timeout, capability_context))
        return FakeSkillResult()


def _write_doc(
    root: Path,
    *,
    entry_type="skill_bridge",
    skill_id="skill_01",
    entry_script: str | None = None,
    tags: list[str] | None = None,
    sensitive_contexts: list[str] | None = None,
    side_effects: list[str] | None = None,
) -> Path:
    cap_dir = root / "workspace" / "cap_01"
    cap_dir.mkdir(parents=True)
    fm = {
        "id": "cap_01",
        "name": "Cap One",
        "description": "A runnable capability.",
        "type": "skill",
        "scope": "workspace",
        "version": "0.1.0",
        "maturity": "stable",
        "status": "active",
        "risk_level": "low",
        "trust_required": "developer",
        "do_not_apply_when": ["when unsafe"],
        "reuse_boundary": "Only for explicit run_capability calls.",
        "side_effects": ["none"],
        "entry_type": entry_type,
    }
    if skill_id:
        fm["skill_id"] = skill_id
    if entry_script:
        fm["entry_script"] = entry_script
    if tags is not None:
        fm["tags"] = tags
    if sensitive_contexts is not None:
        fm["sensitive_contexts"] = sensitive_contexts
    if side_effects is not None:
        fm["side_effects"] = side_effects
    body = """## When to use

Use when testing the runner.

## Procedure

Run the bridged skill.

## Verification

Check the returned payload.

## Failure handling

Return the skill error.
"""
    (cap_dir / "CAPABILITY.md").write_text(
        f"---\n{yaml.dump(fm, sort_keys=False)}---\n\n{body}",
        encoding="utf-8",
    )
    (cap_dir / "evals").mkdir()
    if entry_script:
        script_path = cap_dir / entry_script
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text('def run(**kwargs):\n    return {"ok": True, "args": kwargs}\n', encoding="utf-8")
    return cap_dir


def _write_current_eval(doc):
    record = EvalRecord(
        capability_id=doc.id,
        scope=doc.manifest.scope.value,
        content_hash=doc.content_hash,
        passed=True,
        axes={
            EvalAxis.FUNCTIONAL.value: AxisResult(EvalAxis.FUNCTIONAL, AxisStatus.PASS),
            EvalAxis.SAFETY.value: AxisResult(EvalAxis.SAFETY, AxisStatus.PASS),
            EvalAxis.PRIVACY.value: AxisResult(EvalAxis.PRIVACY, AxisStatus.UNKNOWN),
            EvalAxis.REVERSIBILITY.value: AxisResult(EvalAxis.REVERSIBILITY, AxisStatus.PASS),
        },
    )
    write_eval_record(record, doc)


def _ctx(registry, executor, *, runtime_profile="standard", extra_services=None):
    services = {"skill_executor": executor, "tool_registry": registry}
    if extra_services:
        services.update(extra_services)
    return ToolExecutionContext(
        execute_shell=lambda _: None,
        shell_default_cwd="/tmp",
        services=services,
        auth_level=3,
        runtime_profile=runtime_profile,
    )


async def test_run_capability_skill_bridge_success(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store.get("cap_01", None) if False else None
    doc = store._parser.parse(_write_doc(tmp_path))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)
    executor = FakeSkillExecutor()

    result = await registry.execute(
        ToolExecutionRequest(
            name="run_capability",
            arguments={"id": "cap_01", "arguments": {"x": 1}, "timeout": 7},
        ),
        context=_ctx(registry, executor),
    )

    assert result.success
    assert result.payload["capability_id"] == "cap_01"
    assert executor.calls == [("skill_01", {"x": 1}, 7)]


async def test_run_capability_denies_missing_latest_valid_eval(tmp_path):
    store = CapabilityStore(tmp_path)
    store._parser.parse(_write_doc(tmp_path))
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(registry, FakeSkillExecutor()),
    )

    assert not result.success
    assert result.payload["reason"] == "stale_evaluation"


async def test_run_capability_denies_procedural(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(_write_doc(tmp_path, entry_type="procedural", skill_id=""))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(registry, FakeSkillExecutor()),
    )

    assert not result.success
    assert result.payload["reason"] == "procedural_not_executable"


async def test_run_capability_executable_script_success(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(
        _write_doc(
            tmp_path,
            entry_type="executable_script",
            skill_id="",
            entry_script="scripts/main.py",
        ),
    )
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)
    executor = FakeSkillExecutor()

    result = await registry.execute(
        ToolExecutionRequest(
            name="run_capability",
            arguments={"id": "cap_01", "arguments": {"x": 2}, "timeout": 9},
        ),
        context=_ctx(registry, executor),
    )

    assert result.success
    assert result.payload["entry_type"] == "executable_script"
    assert result.payload["entry_script"] == "scripts/main.py"
    assert len(executor.directory_calls) == 1
    _, entry_script, arguments, timeout, capability_context = executor.directory_calls[0]
    assert entry_script == "scripts/main.py"
    assert arguments == {"x": 2}
    assert timeout == 9
    assert capability_context.capability_id == "cap_01"
    assert capability_context.capability_content_hash == doc.content_hash


def test_tool_execution_context_capability_provenance_defaults_none():
    ctx = ToolExecutionContext(execute_shell=lambda _: None, shell_default_cwd="/tmp")
    assert ctx.capability_id is None
    assert ctx.capability_version is None
    assert ctx.capability_content_hash is None


async def test_run_capability_inner_tick_requires_auto_run_tag(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(_write_doc(tmp_path, tags=[]))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(registry, FakeSkillExecutor(), runtime_profile="inner_tick"),
    )

    assert not result.success
    assert result.payload["reason"] == "inner_tick_requires_auto_run_tag"


async def test_run_capability_inner_tick_allows_auto_run_tag(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(_write_doc(tmp_path, tags=["auto_run"]))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(registry, FakeSkillExecutor(), runtime_profile="inner_tick"),
    )

    assert result.success


async def test_run_capability_do_not_apply_when_blocks_current_task(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(_write_doc(tmp_path))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(
            registry,
            FakeSkillExecutor(),
            extra_services={"current_user_task": "do this task when unsafe for users"},
        ),
    )

    assert not result.success
    assert result.payload["reason"] == "do_not_apply_when_matched"


async def test_run_capability_sensitive_context_blocks_without_approval(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(
        _write_doc(tmp_path, sensitive_contexts=["personal_data"]),
    )
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(
            registry,
            FakeSkillExecutor(),
            extra_services={
                "current_sensitive_contexts": {"personal_data"},
                "approved_sensitive_contexts": set(),
            },
        ),
    )

    assert not result.success
    assert result.payload["reason"] == "sensitive_context_not_approved"


async def test_run_capability_network_side_effect_denied_by_standard_profile(tmp_path):
    store = CapabilityStore(tmp_path)
    doc = store._parser.parse(_write_doc(tmp_path, side_effects=["network_send", "none"]))
    _write_current_eval(doc)
    registry = ToolRegistry()
    register_capability_runner_tools(registry, store)

    result = await registry.execute(
        ToolExecutionRequest(name="run_capability", arguments={"id": "cap_01"}),
        context=_ctx(registry, FakeSkillExecutor()),
    )

    assert not result.success
    assert result.payload["reason"] == "side_effects_not_allowed_by_profile"
