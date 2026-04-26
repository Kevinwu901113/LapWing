"""TaskRuntime 工具剖面定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    capabilities: frozenset[str]
    tool_names: frozenset[str] = frozenset()
    # 当 capabilities 把太多工具拉进来时（例如 task_execution 同时持有 web +
    # agent），用 exclude_tool_names 显式剔除——避免主脑在 raw 工具和
    # delegate_to_* 之间做无意义的二选一。
    exclude_tool_names: frozenset[str] = frozenset()
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
        # create_skill removed: skill authoring is a deliberate, reviewed
        # action, not something the chat surface should do mid-conversation.
        # run_skill stays, gated by an approval check (see commit 3).
        "run_skill",
    }),
    include_internal=False,
    shell_policy_enabled=False,
)

# Inner-tick profile: autonomous self-initiated thinking pulses.
# Companion-aligned surface — preserves memory continuity, notes, reminders,
# commitments, focus, lightweight research/browse, and proactive messaging.
# Explicitly excludes: create_skill / shell / arbitrary file writes /
# Playwright browser_* automation / agent delegation / identity mutations.
# Inner ticks are not maintenance jobs; they must not gain shell or
# code-execution capability without explicit human ack.
INNER_TICK_PROFILE = RuntimeProfile(
    name="inner_tick",
    capabilities=frozenset(),
    tool_names=frozenset({
        # time
        "get_current_datetime",
        # proactive messaging (gated by ProactiveMessageGate in commit 5)
        "send_message",
        # lightweight research
        "research",
        "browse",
        # reminders
        "set_reminder",
        "view_reminders",
        "cancel_reminder",
        # commitments
        "commit_promise",
        "fulfill_promise",
        "abandon_promise",
        # focus
        "close_focus",
        "recall_focus",
        # memory
        "recall",
        "write_note",
        "read_note",
        "list_notes",
        "search_notes",
        # corrections
        "add_correction",
        # skills (only auto-runnable stable ones — gated in commit 3)
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
    # task_execution 必须走 Agent Team 的 delegate_to_* 来做调研，
    # 避免主脑直接调 research/browse 而绕过 Researcher 的多步推理。
    exclude_tool_names=frozenset({"research", "browse"}),
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
        INNER_TICK_PROFILE,
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
