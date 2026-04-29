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
    # send_message is proactive-only (inner_tick / compose_proactive). Direct
    # chat replies are bare assistant text — exposing send_message here lets
    # the model emit "side-door" messages mid-turn, splitting one reply into
    # multiple official messages.
    exclude_tool_names=frozenset({"send_message"}),
    include_internal=False,
    shell_policy_enabled=True,
)

# ── agents-as-tools refactor (2026-04-29): zero_tools / standard ────
# ZERO_TOOLS is the pure-text reply path — IntentRouter routes pure
# chitchat here so the model skips the OpenAI tool-call protocol
# entirely. STANDARD is Lapwing's full self-capability surface; every
# external seam goes through delegate_to_researcher / delegate_to_coder.

ZERO_TOOLS_PROFILE = RuntimeProfile(
    name="zero_tools",
    capabilities=frozenset(),
    tool_names=frozenset(),
    include_internal=False,
    shell_policy_enabled=False,
)

STANDARD_PROFILE = RuntimeProfile(
    name="standard",
    capabilities=frozenset(),
    tool_names=frozenset({
        # ── memory ──
        "recall", "write_note", "read_note", "list_notes", "search_notes",
        # ── time ──
        "get_current_datetime", "convert_timezone",
        # ── reminders ──
        "set_reminder", "view_reminders", "cancel_reminder",
        # ── promises ──
        "commit_promise", "fulfill_promise", "abandon_promise",
        # ── self-correction ──
        "add_correction",
        # ── conversation focus ──
        "close_focus", "recall_focus",
        # ── outward seams (the only edges out to the world) ──
        "delegate_to_researcher", "delegate_to_coder",
        # ── skills ──
        "run_skill",
        # ── planning ──
        "plan_task", "update_plan",
        # send_message intentionally excluded — proactive-only, see
        # COMPOSE_PROACTIVE_PROFILE / INNER_TICK_PROFILE.
    }),
    include_internal=False,
    shell_policy_enabled=False,
)


# Legacy aliases — same tool surface as the new profiles, but the
# legacy ``name`` is preserved so older callers / tests that compare
# ``profile.name == "chat_minimal"`` keep working. Both keys are
# registered in _PROFILES so resolution works either way. Removed in
# the cleanup commit at the end of this refactor.
CHAT_MINIMAL_PROFILE = RuntimeProfile(
    name="chat_minimal",
    capabilities=ZERO_TOOLS_PROFILE.capabilities,
    tool_names=ZERO_TOOLS_PROFILE.tool_names,
    include_internal=ZERO_TOOLS_PROFILE.include_internal,
    shell_policy_enabled=ZERO_TOOLS_PROFILE.shell_policy_enabled,
)

CHAT_EXTENDED_PROFILE = RuntimeProfile(
    name="chat_extended",
    capabilities=STANDARD_PROFILE.capabilities,
    tool_names=STANDARD_PROFILE.tool_names,
    include_internal=STANDARD_PROFILE.include_internal,
    shell_policy_enabled=STANDARD_PROFILE.shell_policy_enabled,
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

# TEMPORARY LEGACY ESCAPE HATCH (Step 1 only).
# task_execution still aggregates shell/browser/file capabilities so
# specific power flows can run. This is *not* the target architecture
# — Step 2 must migrate the execution tools to Coder, at which point
# task_execution either becomes a thin alias for STANDARD or is
# deleted. send_message stays excluded (proactive-only) and raw web
# retrieval stays out (goes through delegate_to_researcher).
TASK_EXECUTION_PROFILE = RuntimeProfile(
    name="task_execution",
    capabilities=frozenset({
        "shell", "skill", "memory", "schedule",
        "general", "browser", "commitment", "agent", "file",
        "code", "verify", "identity",
    }),
    exclude_tool_names=frozenset({
        "research", "browse", "get_sports_score", "send_message",
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
    tool_names=frozenset({
        "research",
        "browse",
        # Specialized retrieval API — Researcher chooses between this and
        # the generic search/browse based on the question. Lapwing never
        # sees it directly; sports questions go via delegate_to_researcher.
        "get_sports_score",
    }),
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

# compose_proactive: the always-on tool surface used by Brain.compose_proactive
# and any other path that needs the "talk to Kevin + check state + delegate
# heavy work" surface without raw shell access. Source of truth for the
# tool names — replaces the hardcoded list previously inlined in
# TaskRuntime.chat_tools(). Shell, raw web, browser, ambient knowledge
# are layered on dynamically by chat_tools() based on caller flags.
COMPOSE_PROACTIVE_PROFILE = RuntimeProfile(
    name="compose_proactive",
    capabilities=frozenset(),
    tool_names=frozenset({
        # talk to user / view shared media
        "send_message",
        "get_time",
        "send_image",
        "view_image",
        # reminders
        "set_reminder",
        "view_reminders",
        "cancel_reminder",
        # outward seams (the only edges out to the world)
        "delegate_to_researcher",
        "delegate_to_coder",
        # commitments
        "commit_promise",
        "fulfill_promise",
        "abandon_promise",
        # planning + corrections + focus
        "plan_task",
        "update_plan",
        "add_correction",
        "close_focus",
        "recall_focus",
    }),
    include_internal=False,
    shell_policy_enabled=False,
)


_PROFILES = {
    profile.name: profile
    for profile in (
        CHAT_SHELL_PROFILE,
        ZERO_TOOLS_PROFILE,
        STANDARD_PROFILE,
        CHAT_MINIMAL_PROFILE,
        CHAT_EXTENDED_PROFILE,
        INNER_TICK_PROFILE,
        COMPOSE_PROACTIVE_PROFILE,
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
