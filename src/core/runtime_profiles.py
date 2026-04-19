"""TaskRuntime 工具剖面定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    capabilities: frozenset[str]
    tool_names: frozenset[str] = frozenset()
    include_internal: bool = False
    shell_policy_enabled: bool = False


# Step 5: ``communication`` / ``commitment`` 是基础能力（tell_user 与
# commit/fulfill/abandon_promise），所有 profile 都默认包含——没有它
# 模型就无法对用户说话或登记承诺。tool_names 白名单的 profile 显式
# 列出 tell_user；commit/fulfill/abandon_promise 在 M2 注册后由 M2
# 步骤补入，避免 ToolNotRegisteredError 在 M1 时炸。
CHAT_SHELL_PROFILE = RuntimeProfile(
    name="chat_shell",
    capabilities=frozenset({
        "shell", "web", "skill", "memory", "schedule",
        "general", "browser", "communication", "commitment",
    }),
    include_internal=False,
    shell_policy_enabled=True,
)

CODER_SNIPPET_PROFILE = RuntimeProfile(
    name="coder_snippet",
    capabilities=frozenset({"code", "verify", "communication", "commitment"}),
    tool_names=frozenset({
        "run_python_code", "verify_code_result",
        "tell_user", "commit_promise", "fulfill_promise", "abandon_promise",
    }),
    include_internal=True,
)

CODER_WORKSPACE_PROFILE = RuntimeProfile(
    name="coder_workspace",
    capabilities=frozenset({"code", "file", "verify", "communication", "commitment"}),
    include_internal=True,
    tool_names=frozenset({
        "apply_workspace_patch", "verify_workspace",
        "tell_user", "commit_promise", "fulfill_promise", "abandon_promise",
    }),
)

FILE_OPS_PROFILE = RuntimeProfile(
    name="file_ops",
    capabilities=frozenset({"file", "communication", "commitment"}),
    tool_names=frozenset(
        {
            "file_read_segment",
            "file_write",
            "file_append",
            "file_list_directory",
            "tell_user",
            "commit_promise",
            "fulfill_promise",
            "abandon_promise",
        }
    ),
    include_internal=False,
)

# Step 6: Agent Team profiles — 注意不含 ``communication`` / ``commitment``
# 能力，因此也拿不到 tell_user / commit_promise——只有 Lapwing 能对用户
# 说话，Agent 的产出只作为返回值给编排层（delegate 工具）消费。
AGENT_RESEARCHER_PROFILE = RuntimeProfile(
    name="agent_researcher",
    capabilities=frozenset(),
    tool_names=frozenset({"research", "browse"}),
    include_internal=False,
    shell_policy_enabled=False,
)

AGENT_CODER_PROFILE = RuntimeProfile(
    name="agent_coder",
    capabilities=frozenset(),
    tool_names=frozenset({
        "ws_file_read", "ws_file_write", "ws_file_list",
        "execute_shell", "run_python_code",
    }),
    include_internal=True,  # ws_file_* 注册为 internal；Agent 需要显式放行
    shell_policy_enabled=False,  # Agent 的 shell 由 _noop_shell 阻断（见 base.py）
)

AGENT_TEAM_LEAD_PROFILE = RuntimeProfile(
    name="agent_team_lead",
    capabilities=frozenset(),
    tool_names=frozenset({"delegate_to_agent"}),
    include_internal=False,
    shell_policy_enabled=False,
)

_PROFILES = {
    profile.name: profile
    for profile in (
        CHAT_SHELL_PROFILE,
        CODER_SNIPPET_PROFILE,
        CODER_WORKSPACE_PROFILE,
        FILE_OPS_PROFILE,
        AGENT_RESEARCHER_PROFILE,
        AGENT_CODER_PROFILE,
        AGENT_TEAM_LEAD_PROFILE,
    )
}


def get_runtime_profile(name: str) -> RuntimeProfile:
    if name not in _PROFILES:
        raise ValueError(f"未知 runtime profile: {name}")
    return _PROFILES[name]
