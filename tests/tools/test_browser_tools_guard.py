"""BrowserGuard wiring on the browser_* tool executors.

browser_open already runs check_url through TaskRuntime. This file
locks in the contract for the rest of the browser surface:

- browser_click  — runtime check_action with element_text + url
- browser_type   — runtime check_action with element_text + url
- browser_login  — guard returns require_consent → executor blocks
- JS evaluation  — BrowserManager.execute_js consults check_js
- Downloads      — BrowserGuard.check_download is reachable

Each denial path must also emit a TOOL_DENIED mutation log entry so
the audit trail captures guard-level refusals, not just shell-level
ones. (Currently only browser_open URL block + browser_guard_missing
record TOOL_DENIED — see test_audit_logging.py for that contract.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.browser_guard import BrowserGuard
from src.core.browser_manager import PageState
from src.logging.state_mutation_log import MutationType
from src.tools.browser_tools import register_browser_tools
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _FakeMutationLog:
    def __init__(self):
        self.records: list[tuple[str, dict, dict]] = []

    async def record(self, event_type, payload, *, iteration_id=None, chat_id=None):
        self.records.append(
            (event_type.value, dict(payload), {"iteration_id": iteration_id, "chat_id": chat_id})
        )


def _by_type(records, event_type):
    return [r for r in records if r[0] == event_type.value]


def _ctx(services: dict) -> ToolExecutionContext:
    from src.tools.shell_executor import ShellResult

    async def _noop_shell(_):
        return ShellResult(stdout="", stderr="", return_code=0)

    return ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd="/tmp",
        services=services,
        auth_level=2,
        chat_id="c1",
        runtime_profile="local_execution",
    )


def _make_page_state(url: str, *, elements=None) -> PageState:
    return PageState(
        url=url,
        title="t",
        elements=elements or [],
        text_summary="",
        visual_description=None,
        scroll_position="top",
        has_more_below=False,
        tab_id="t1",
        timestamp="2026-04-27T00:00:00",
        is_image_heavy=False,
    )


def _build_registry(browser_manager, browser_guard):
    registry = ToolRegistry()
    register_browser_tools(
        registry=registry,
        browser_manager=browser_manager,
        credential_vault=None,
        browser_guard=browser_guard,
        event_bus=None,
    )
    return registry


@pytest.mark.asyncio
async def test_browser_type_runs_check_action():
    """browser_type must call check_action — currently it does not, so a
    flooded type loop bypasses the action budget entirely."""
    bm = MagicMock()
    bm.get_page_state = AsyncMock(return_value=_make_page_state("https://example.com/"))
    bm.type_text = AsyncMock(return_value=_make_page_state("https://example.com/"))

    guard = BrowserGuard(
        block_internal_network=False,
        max_actions_per_session=1,
        block_downloads=True,
    )
    # First click consumes the only allowed action slot
    out = guard.check_action("click", "Read", "https://example.com/")
    assert out.action == "allow"

    log = _FakeMutationLog()
    registry = _build_registry(bm, guard)
    spec = registry.get("browser_type")
    assert spec is not None

    req = ToolExecutionRequest(
        name="browser_type",
        arguments={"element": "[3]", "text": "hello"},
    )
    result = await spec.executor(req, _ctx({"mutation_log": log}))
    assert result.success is False
    assert "budget" in (result.reason or "").lower() or "action_budget_exceeded" in (
        result.reason or ""
    )
    bm.type_text.assert_not_awaited()
    denied = _by_type(log.records, MutationType.TOOL_DENIED)
    assert len(denied) == 1
    assert denied[0][1]["tool"] == "browser_type"
    assert denied[0][1]["guard"] == "browser_guard"


@pytest.mark.asyncio
async def test_browser_login_blocks_with_require_consent():
    """browser_login must consult the guard and refuse without OWNER
    consent. Currently nothing in the executor calls check_action."""
    bm = MagicMock()
    guard = BrowserGuard(block_internal_network=False, block_downloads=True)
    log = _FakeMutationLog()
    registry = _build_registry(bm, guard)
    spec = registry.get("browser_login")
    assert spec is not None

    req = ToolExecutionRequest(
        name="browser_login", arguments={"service": "github"},
    )
    result = await spec.executor(
        req,
        _ctx({"mutation_log": log, "credential_vault": None}),
    )
    assert result.success is False
    # Either consent gate or vault-missing — both are valid blocks.
    assert result.payload.get("requires_consent") is True or "凭据" in (
        result.reason or ""
    ) or "OWNER" in (result.reason or "")
    bm.navigate.assert_not_called() if hasattr(bm, "navigate") else None
    # Audit must fire for the guard denial path specifically.
    denied = _by_type(log.records, MutationType.TOOL_DENIED)
    assert any(r[1]["tool"] == "browser_login" and r[1]["guard"] == "browser_guard"
               for r in denied)


@pytest.mark.asyncio
async def test_browser_click_audit_logs_consent_denial():
    """When click hits a destructive verb, the existing executor returns
    require_consent — audit must be logged so the denial is observable."""
    bm = MagicMock()
    bm.get_page_state = AsyncMock(return_value=_make_page_state(
        "https://example.com/",
        elements=[
            type("E", (), {
                "index": 1, "text": "Delete account", "aria_label": "",
                "tag": "button", "element_type": None, "name": "",
            })(),
        ],
    ))

    guard = BrowserGuard(
        block_internal_network=False,
        sensitive_words=("delete",),
        block_downloads=True,
    )
    log = _FakeMutationLog()
    registry = _build_registry(bm, guard)
    spec = registry.get("browser_click")
    assert spec is not None

    req = ToolExecutionRequest(
        name="browser_click", arguments={"element": "[1]"}
    )
    result = await spec.executor(req, _ctx({"mutation_log": log}))
    assert result.success is False
    assert result.payload.get("requires_consent") is True
    bm.click.assert_not_called() if hasattr(bm, "click") else None
    denied = _by_type(log.records, MutationType.TOOL_DENIED)
    assert any(
        r[1]["tool"] == "browser_click" and r[1]["guard"] == "browser_guard"
        for r in denied
    )


def test_browser_guard_check_download_reachable():
    """check_download is part of the public guard surface even though no
    download tool is currently registered. Lock in that the API exists
    so future download paths can call it."""
    guard = BrowserGuard(block_downloads=True)
    out = guard.check_download(url="https://example.com/x.zip", filename="x.zip")
    assert out.action == "block"


@pytest.mark.asyncio
async def test_browser_manager_execute_js_consults_guard():
    """JS evaluation goes through BrowserManager.execute_js which already
    calls check_js. This test pins the contract — block must propagate."""
    from src.core.browser_manager import BrowserError, BrowserManager

    bm = BrowserManager()
    guard = BrowserGuard(block_downloads=True)
    bm.set_browser_guard(guard)

    with pytest.raises(BrowserError):
        await bm.execute_js("eval('1+1')")
