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


CHAT_SHELL_PROFILE = RuntimeProfile(
    name="chat_shell",
    capabilities=frozenset({"shell", "web", "skill"}),
    include_internal=False,
    shell_policy_enabled=True,
)

CODER_SNIPPET_PROFILE = RuntimeProfile(
    name="coder_snippet",
    capabilities=frozenset({"code", "verify"}),
    tool_names=frozenset({"run_python_code", "verify_code_result"}),
    include_internal=True,
)

CODER_WORKSPACE_PROFILE = RuntimeProfile(
    name="coder_workspace",
    capabilities=frozenset({"code", "file", "verify"}),
    include_internal=True,
    tool_names=frozenset({"apply_workspace_patch", "verify_workspace"}),
)

FILE_OPS_PROFILE = RuntimeProfile(
    name="file_ops",
    capabilities=frozenset({"file"}),
    tool_names=frozenset(
        {
            "file_read_segment",
            "file_write",
            "file_append",
            "file_list_directory",
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
