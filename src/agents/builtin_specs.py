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


def all_builtin_specs() -> list[AgentSpec]:
    """Return fresh AgentSpec instances for every registered builtin."""
    return [builtin_researcher_spec(), builtin_coder_spec()]
