"""Builtin agent specs (Blueprint §8).

These AgentSpec rows describe the always-available builtin agents
(researcher, coder) for catalog listing + StateView rendering. The
runtime instances themselves are still produced by Researcher.create()
and Coder.create() — Factory routes to those classmethods because the
existing constructors generate their own LegacyAgentSpec internally.

system_prompt is empty here precisely so we don't drift from the
prompt those classmethods build.
"""

from __future__ import annotations

from src.agents.spec import (
    AgentLifecyclePolicy,
    AgentResourceLimits,
    AgentSpec,
)


def builtin_researcher_spec() -> AgentSpec:
    return AgentSpec(
        id="builtin_researcher",
        name="researcher",
        display_name="Researcher",
        description="搜索和浏览网页，收集信息，适合调研和信息查找任务",
        kind="builtin",
        system_prompt="",
        model_slot="agent_researcher",
        runtime_profile="agent_researcher",
        lifecycle=AgentLifecyclePolicy(
            mode="persistent", ttl_seconds=None, max_runs=None,
        ),
        resource_limits=AgentResourceLimits(
            max_tool_calls=30, max_llm_calls=15,
            max_tokens=30000, max_wall_time_seconds=300,
        ),
        created_by="system",
        created_reason="builtin agent",
    )


def builtin_coder_spec() -> AgentSpec:
    return AgentSpec(
        id="builtin_coder",
        name="coder",
        display_name="Coder",
        description="文件读写和 Python 代码执行，适合实现和调试任务",
        kind="builtin",
        system_prompt="",
        model_slot="agent_coder",
        runtime_profile="agent_coder",
        lifecycle=AgentLifecyclePolicy(
            mode="persistent", ttl_seconds=None, max_runs=None,
        ),
        resource_limits=AgentResourceLimits(
            max_tool_calls=40, max_llm_calls=20,
            max_tokens=30000, max_wall_time_seconds=600,
        ),
        created_by="system",
        created_reason="builtin agent",
    )


def builtin_resident_operator_spec() -> AgentSpec:
    """Resident Operator agent (blueprint §16 O-1, Slice I.1).

    Drives kernel.execute(Action(...)) flows that need persistent identity
    (personal browser profile, long sessions, owner-takeover-able interrupts).
    Selected by cognition via delegate_to_agent(agent_name='resident_operator',
    task=...) when the task requires:
      - signed-in browser identity (cookies, autofill, 2FA tokens)
      - multi-step browser actions that may hit CAPTCHA / login walls
      - credential.use lease consumption (paired with BrowserAdapter.login)

    Runtime worker class: src/agents/resident_operator.py ResidentOperator.
    """
    return AgentSpec(
        id="builtin_resident_operator",
        name="resident_operator",
        display_name="Resident Operator",
        description=(
            "持久身份浏览器操作员 — 适合需要登录态、长会话、可能触发"
            "owner 介入(CAPTCHA / 2FA / 登录)的任务。"
        ),
        kind="builtin",
        system_prompt="",
        model_slot="agent_execution",
        runtime_profile="agent_execution",
        lifecycle=AgentLifecyclePolicy(
            mode="persistent", ttl_seconds=None, max_runs=None,
        ),
        resource_limits=AgentResourceLimits(
            # Resident operator may sit through long interrupt waits; larger
            # wall-time budget than researcher/coder.
            max_tool_calls=40, max_llm_calls=20,
            max_tokens=30000, max_wall_time_seconds=3600,
        ),
        created_by="system",
        created_reason="builtin agent (v1 blueprint §16 O-1)",
    )


def all_builtin_specs() -> list[AgentSpec]:
    """Return fresh AgentSpec instances for every registered builtin."""
    return [
        builtin_researcher_spec(),
        builtin_coder_spec(),
        builtin_resident_operator_spec(),
    ]
