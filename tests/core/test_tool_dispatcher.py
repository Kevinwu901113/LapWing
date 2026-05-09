import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.tool_dispatcher import ToolDispatcher, ServiceContextView, MissingServiceError
from src.core.task_runtime import TaskRuntime
from src.tools.types import ToolExecutionRequest, ToolExecutionResult
from src.logging.state_mutation_log import MutationType
from src.agents.spec import AgentSpec as AgentSpecV2
from src.agents.types import LegacyAgentSpec

@pytest.fixture
def mock_runtime():
    runtime = MagicMock(spec=TaskRuntime)
    runtime._resolve_profile.return_value = MagicMock(
        name="test_profile",
        include_internal=False,
        shell_policy_enabled=False
    )
    runtime._tool_registry = MagicMock()
    runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    runtime._memory_index = None
    return runtime

@pytest.fixture
def dispatcher(mock_runtime):
    return ToolDispatcher(mock_runtime)

@pytest.mark.asyncio
async def test_tool_dispatcher_unknown_tool(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = None
    req = ToolExecutionRequest(name="unknown_tool", arguments={})

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )

    assert not result.success
    assert result.reason == "未知工具：unknown_tool"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "unknown_tool"

@pytest.mark.asyncio
async def test_tool_dispatcher_profile_not_allowed(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    req = ToolExecutionRequest(name="disallowed_tool", arguments={})

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )

    assert not result.success
    assert "不允许工具" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "profile_not_allowed"

@pytest.mark.asyncio
async def test_tool_dispatcher_authority_gate(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"execute_shell"}
    req = ToolExecutionRequest(name="execute_shell", arguments={})

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        adapter="qq",  # guest path should be denied for execute_shell
        user_id="untrusted_user",
    )

    assert not result.success
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "authority_gate"

@pytest.mark.asyncio
async def test_tool_dispatcher_agent_policy_missing_fail_closed(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    agent_spec = AgentSpecV2(
        name="dyn_agent",
        kind="dynamic",
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
    )

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
        adapter="agent",
    )

    assert not result.success
    assert result.reason == "missing_agent_policy"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"


@pytest.mark.asyncio
async def test_tool_dispatcher_agent_policy_denied(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    agent_spec = AgentSpecV2(
        name="dyn_agent",
        kind="dynamic",
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
    )
    mock_policy = MagicMock()
    mock_policy.validate_tool_access.return_value = False

    services = {
        "mutation_log": AsyncMock(),
        "agent_policy": mock_policy,
    }
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
        adapter="agent",
    )

    assert not result.success
    assert result.reason == "policy_denied_tool"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"


# ── Agent spec kind tamper detection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_spec_kind_builtin_tampered_fail_closed(dispatcher, mock_runtime):
    """v2 AgentSpec with kind='builtin' -> fail-closed (tamper detected)."""
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    agent_spec = AgentSpecV2(
        name="dyn_agent",
        kind="builtin",  # tampered -- should be "dynamic"
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
    )

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
        adapter="agent",
    )

    assert not result.success
    assert result.reason == "agent_spec_kind_mismatch"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"
    assert args[1]["agent_kind"] == "builtin"


@pytest.mark.asyncio
async def test_agent_spec_kind_empty_tampered_fail_closed(dispatcher, mock_runtime):
    """v2 AgentSpec with kind='' -> fail-closed (tamper detected)."""
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    agent_spec = AgentSpecV2(
        name="dyn_agent",
        kind="",  # type: ignore[arg-type]  # tampered
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
    )

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
        adapter="agent",
    )

    assert not result.success
    assert result.reason == "agent_spec_kind_mismatch"


# ── LegacyAgentSpec passthrough for builtins ─────────────────────────────

@pytest.mark.asyncio
async def test_legacy_agent_spec_passthrough_for_builtin(dispatcher, mock_runtime):
    """LegacyAgentSpec with adapter='agent' passes through without dynamic denylist.

    This is the builtin Researcher/Coder path -- they use LegacyAgentSpec and
    rely on profile-based tool restrictions, not the dynamic agent denylist.
    """
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
        success=True, payload={"result": "ok"}, reason="",
    ))
    mock_runtime._tool_names_for_profile.return_value = {"research"}
    req = ToolExecutionRequest(name="research", arguments={})

    legacy_spec = LegacyAgentSpec(
        name="researcher",
        description="Builtin researcher",
        system_prompt="You are a researcher.",
        model_slot="agent_researcher",
    )

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=legacy_spec,
        adapter="agent",
    )

    # Should NOT be blocked by agent policy -- passes through to tool execution.
    assert result.success
    assert result.reason != "agent_spec_kind_mismatch"
    assert result.reason != "unknown_agent_spec_type"
    assert result.reason != "missing_agent_policy"
    assert result.reason != "policy_denied_tool"


# ── Unknown spec type fail-closed ────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_agent_spec_type_fail_closed(dispatcher, mock_runtime):
    """A non-v2, non-legacy spec on an agent adapter -> fail-closed."""
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    bogus_spec = MagicMock(kind="dynamic", name="bogus")

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=bogus_spec,
        adapter="agent",
    )

    assert not result.success
    assert result.reason == "unknown_agent_spec_type"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"


@pytest.mark.asyncio
async def test_agent_spec_none_skips_policy_check(dispatcher, mock_runtime):
    """agent_spec=None (normal user path) skips agent policy entirely."""
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
        success=True, payload={"result": "ok"}, reason="",
    ))
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=None,
    )

    # Should not be blocked by agent policy -- proceeds to tool execution.
    assert result.success
    assert result.reason != "agent_spec_kind_mismatch"
    assert result.reason != "unknown_agent_spec_type"
    assert result.reason != "missing_agent_policy"
    assert result.reason != "policy_denied_tool"


@pytest.mark.asyncio
async def test_tool_dispatcher_browser_guard_missing(dispatcher, mock_runtime):
    tool_mock = MagicMock(capability="browser")
    mock_runtime._tool_registry.get.return_value = tool_mock
    mock_runtime._tool_names_for_profile.return_value = {"browser_open"}
    req = ToolExecutionRequest(name="browser_open", arguments={})

    mock_runtime._browser_guard = None

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )

    assert not result.success
    assert "未挂载" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "browser_guard_missing"


# ── ServiceContextView property tests ────────────────────────────────────────

_ALL_SERVICE_KEYS = [
    # Core routing
    ("llm_router", "llm_router"),
    # Tool execution
    ("tool_registry", "tool_registry"),
    ("dispatcher", "dispatcher"),
    ("tool_dispatcher", "tool_dispatcher"),
    # Auditing
    ("mutation_log", "mutation_log"),
    # Agents
    ("agent_registry", "agent_registry"),
    ("agent_policy", "agent_policy"),
    # Budget
    ("budget_ledger", "budget_ledger"),
    # Proactive / outbound
    ("proactive_message_gate", "proactive_message_gate"),
    ("proactive_send_active", "proactive_send_active"),
    # Channels
    ("channel_manager", "channel_manager"),
    ("owner_qq_id", "owner_qq_id"),
    # Browser
    ("browser_manager", "browser_manager"),
    ("vlm", "vlm"),
    # Skills
    ("skill_store", "skill_store"),
    ("skill_executor", "skill_executor"),
    # Memory
    ("note_store", "note_store"),
    ("vector_store", "vector_store"),
    # Scheduling
    ("durable_scheduler", "durable_scheduler"),
    ("reminder_scheduler", "reminder_scheduler"),
    # Commitments / focus / trajectory
    ("commitment_store", "commitment_store"),
    ("focus_manager", "focus_manager"),
    ("trajectory_store", "trajectory_store"),
    # Corrections
    ("correction_manager", "correction_manager"),
    # Safety
    ("circuit_breaker", "circuit_breaker"),
    ("infra_breaker", "infra_breaker"),
    ("expression_gate", "expression_gate"),
    # Ambient / interest / research
    ("ambient_store", "ambient_store"),
    ("interest_profile", "interest_profile"),
    ("research_engine", "research_engine"),
    # Plan state (runtime-only)
    ("plan_state", "plan_state"),
    # Agent-factory
    ("shell_default_cwd", "shell_default_cwd"),
]


@pytest.mark.parametrize("prop_name,dict_key", _ALL_SERVICE_KEYS)
def test_service_context_view_property_returns_raw_value(prop_name, dict_key):
    obj = object()
    view = ServiceContextView({dict_key: obj})
    assert getattr(view, prop_name) is obj


def test_service_context_view_missing_key_returns_none():
    view = ServiceContextView({})
    for prop_name, _dict_key in _ALL_SERVICE_KEYS:
        assert getattr(view, prop_name) is None


# ── require_* fail-closed ──────────────────────────────────────────────────

def test_require_dispatcher_missing_raises():
    view = ServiceContextView({})
    with pytest.raises(MissingServiceError, match="dispatcher"):
        view.require_dispatcher()


def test_require_dispatcher_present_returns():
    d = object()
    view = ServiceContextView({"dispatcher": d})
    assert view.require_dispatcher() is d


def test_require_tool_dispatcher_missing_raises():
    view = ServiceContextView({})
    with pytest.raises(MissingServiceError, match="tool_dispatcher"):
        view.require_tool_dispatcher()


def test_require_tool_dispatcher_present_returns_dispatch_capable():
    d = MagicMock()
    d.dispatch = AsyncMock()
    view = ServiceContextView({"tool_dispatcher": d})
    assert view.require_tool_dispatcher() is d


def test_require_agent_policy_missing_raises():
    view = ServiceContextView({})
    with pytest.raises(MissingServiceError, match="agent_policy"):
        view.require_agent_policy()


def test_require_agent_policy_present_returns():
    p = object()
    view = ServiceContextView({"agent_policy": p})
    assert view.require_agent_policy() is p


def test_require_tool_registry_missing_raises():
    view = ServiceContextView({})
    with pytest.raises(MissingServiceError, match="tool_registry"):
        view.require_tool_registry()


def test_require_tool_registry_present_returns():
    tr = object()
    view = ServiceContextView({"tool_registry": tr})
    assert view.require_tool_registry() is tr


# ── require_*_optional graceful degrade ────────────────────────────────────

def test_require_mutation_log_optional_missing_returns_none():
    view = ServiceContextView({})
    assert view.require_mutation_log_optional() is None


def test_require_mutation_log_optional_present_returns():
    ml = object()
    view = ServiceContextView({"mutation_log": ml})
    assert view.require_mutation_log_optional() is ml


def test_require_budget_ledger_optional_missing_returns_none():
    view = ServiceContextView({})
    assert view.require_budget_ledger_optional() is None


def test_require_budget_ledger_optional_present_returns():
    bl = object()
    view = ServiceContextView({"budget_ledger": bl})
    assert view.require_budget_ledger_optional() is bl


# ── Runtime-only key accessors ─────────────────────────────────────────────

def test_proactive_send_active_accessor():
    view = ServiceContextView({"proactive_send_active": True})
    assert view.proactive_send_active is True

    view_empty = ServiceContextView({})
    assert view_empty.proactive_send_active is None


def test_plan_state_accessor():
    ps = object()
    view = ServiceContextView({"plan_state": ps})
    assert view.plan_state is ps


def test_shell_default_cwd_accessor():
    view = ServiceContextView({"shell_default_cwd": "/tmp/workspace"})
    assert view.shell_default_cwd == "/tmp/workspace"


@pytest.mark.asyncio
async def test_capability_missing_execution_manifest_denies(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"some_tool"}
    req = ToolExecutionRequest(name="some_tool", arguments={})

    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        capability_id="cap_01",
    )

    assert not result.success
    assert result.reason == "capability_missing_execution_manifest"


@pytest.mark.asyncio
async def test_capability_child_tool_not_declared_denies(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"some_tool"}
    req = ToolExecutionRequest(name="some_tool", arguments={})

    manifest = MagicMock()
    manifest.required_tools = ["other_tool"]
    services = {
        "mutation_log": AsyncMock(),
        "capability_execution_manifest": manifest,
    }
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        capability_id="cap_01",
    )

    assert not result.success
    assert result.reason == "capability_child_tool_not_declared"


@pytest.mark.asyncio
async def test_capability_child_tool_declared_allows(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"some_tool"}
    mock_runtime._tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
        success=True, payload={"ok": True}, reason="",
    ))
    req = ToolExecutionRequest(name="some_tool", arguments={})

    manifest = MagicMock()
    manifest.required_tools = ["some_tool"]
    services = {
        "mutation_log": AsyncMock(),
        "capability_execution_manifest": manifest,
    }
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        capability_id="cap_01",
    )

    assert result.success
