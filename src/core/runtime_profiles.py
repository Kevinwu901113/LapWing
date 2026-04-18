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
        "run_python_code", "verify_code_result", "tell_user",
    }),
    include_internal=True,
)

CODER_WORKSPACE_PROFILE = RuntimeProfile(
    name="coder_workspace",
    capabilities=frozenset({"code", "file", "verify", "communication", "commitment"}),
    include_internal=True,
    tool_names=frozenset({
        "apply_workspace_patch", "verify_workspace", "tell_user",
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
        }
    ),
    include_internal=False,
)

_PROFILES = {
    profile.name: profile
    for profile in (
        CHAT_SHELL_PROFILE,
        CODER_SNIPPET_PROFILE,
        CODER_WORKSPACE_PROFILE,
        FILE_OPS_PROFILE,
    )
}


def get_runtime_profile(name: str) -> RuntimeProfile:
    if name not in _PROFILES:
        raise ValueError(f"未知 runtime profile: {name}")
    return _PROFILES[name]
