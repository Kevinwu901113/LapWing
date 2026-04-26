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


# 正常对话中模型直接输出文字即为用户可见消息。
# send_message（personal_tools）用于主动消息场景，通过 general capability 暴露。
CHAT_SHELL_PROFILE = RuntimeProfile(
    name="chat_shell",
    capabilities=frozenset({
        "shell", "web", "skill", "memory", "schedule",
        "general", "browser", "commitment",
    }),
    include_internal=False,
    shell_policy_enabled=True,
)

CHAT_MINIMAL_PROFILE = RuntimeProfile(
    name="chat_minimal",
    capabilities=frozenset({"general"}),
    tool_names=frozenset({
        "get_current_datetime",
        "send_message",
        "add_correction",
    }),
    include_internal=False,
    shell_policy_enabled=False,
)

CHAT_EXTENDED_PROFILE = RuntimeProfile(
    name="chat_extended",
    capabilities=frozenset({"general", "memory", "web", "schedule", "skill", "commitment"}),
    tool_names=frozenset({
        "get_current_datetime",
        "send_message",
        "add_correction",
        "research",
        "get_sports_score",
        "browse",
        "set_reminder",
        "view_reminders",
        "cancel_reminder",
        "commit_promise",
        "fulfill_promise",
        "abandon_promise",
        "close_focus",
        "recall_focus",
        "recall",
        "write_note",
        "read_note",
        "list_notes",
        "search_notes",
        "create_skill",
        "run_skill",
    }),
    include_internal=False,
    shell_policy_enabled=False,
)

TASK_EXECUTION_PROFILE = RuntimeProfile(
    name="task_execution",
    capabilities=frozenset({
        "shell", "web", "skill", "memory", "schedule",
        "general", "browser", "commitment", "agent", "file",
        "code", "verify", "identity",
    }),
    include_internal=False,
    shell_policy_enabled=True,
)

CODER_SNIPPET_PROFILE = RuntimeProfile(
    name="coder_snippet",
    capabilities=frozenset({"code", "verify", "commitment"}),
    tool_names=frozenset({
        "run_python_code", "verify_code_result",
        "commit_promise", "fulfill_promise", "abandon_promise",
    }),
    include_internal=True,
)

CODER_WORKSPACE_PROFILE = RuntimeProfile(
    name="coder_workspace",
    capabilities=frozenset({"code", "file", "verify", "commitment"}),
    include_internal=True,
    tool_names=frozenset({
        "apply_workspace_patch", "verify_workspace",
        "commit_promise", "fulfill_promise", "abandon_promise",
    }),
)

FILE_OPS_PROFILE = RuntimeProfile(
    name="file_ops",
    capabilities=frozenset({"file", "commitment"}),
    tool_names=frozenset({
        "file_read_segment",
        "file_write",
        "file_append",
        "file_list_directory",
        "commit_promise",
        "fulfill_promise",
        "abandon_promise",
    }),
    include_internal=False,
)

# Agent Team profiles — 不含 general / commitment 能力，
# 拿不到 send_message / commit_promise——只有 Lapwing 能对用户
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
        "run_python_code",
    }),
    include_internal=True,
    shell_policy_enabled=False,
)

_PROFILES = {
    profile.name: profile
    for profile in (
        CHAT_SHELL_PROFILE,
        CHAT_MINIMAL_PROFILE,
        CHAT_EXTENDED_PROFILE,
        TASK_EXECUTION_PROFILE,
        CODER_SNIPPET_PROFILE,
        CODER_WORKSPACE_PROFILE,
        FILE_OPS_PROFILE,
        AGENT_RESEARCHER_PROFILE,
        AGENT_CODER_PROFILE,
    )
}


def get_runtime_profile(name: str) -> RuntimeProfile:
    if name not in _PROFILES:
        raise ValueError(f"未知 runtime profile: {name}")
    return _PROFILES[name]
