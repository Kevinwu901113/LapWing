"""src/agents/spec.py"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.core.time_utils import now as local_now


@dataclass
class AgentLifecyclePolicy:
    mode: Literal["ephemeral", "session", "persistent"] = "ephemeral"
    ttl_seconds: int | None = 3600
    max_runs: int | None = 1
    reusable: bool = False


@dataclass
class AgentResourceLimits:
    max_tool_calls: int = 20
    max_llm_calls: int = 8
    max_tokens: int = 30000
    max_wall_time_seconds: int = 180
    max_child_agents: int = 0


@dataclass
class AgentSpec:
    id: str = field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:12]}")
    name: str = ""
    display_name: str = ""
    description: str = ""
    kind: Literal["builtin", "dynamic"] = "dynamic"
    version: int = 1
    status: Literal["active", "archived", "disabled"] = "active"
    system_prompt: str = ""
    model_slot: str = "agent_researcher"
    runtime_profile: str = ""
    tool_denylist: list[str] = field(default_factory=list)
    lifecycle: AgentLifecyclePolicy = field(default_factory=AgentLifecyclePolicy)
    resource_limits: AgentResourceLimits = field(default_factory=AgentResourceLimits)
    created_by: str = "brain"
    created_reason: str = ""
    created_at: datetime = field(default_factory=local_now)
    updated_at: datetime = field(default_factory=local_now)

    def spec_hash(self) -> str:
        content = json.dumps({
            "name": self.name,
            "system_prompt": self.system_prompt,
            "model_slot": self.model_slot,
            "runtime_profile": self.runtime_profile,
            "tool_denylist": sorted(self.tool_denylist),
            "resource_limits": {
                "max_tool_calls": self.resource_limits.max_tool_calls,
                "max_llm_calls": self.resource_limits.max_llm_calls,
                "max_tokens": self.resource_limits.max_tokens,
                "max_wall_time_seconds": self.resource_limits.max_wall_time_seconds,
                "max_child_agents": self.resource_limits.max_child_agents,
            },
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()[:16]


ALLOWED_MODEL_SLOTS: frozenset[str] = frozenset({
    "agent_researcher",
    "agent_coder",
    "lightweight_judgment",
})

ALLOWED_DYNAMIC_PROFILES: frozenset[str] = frozenset({
    "agent_researcher",
    "agent_coder",
})

DYNAMIC_AGENT_DENYLIST: frozenset[str] = frozenset({
    "create_agent", "list_agents", "save_agent", "destroy_agent",
    "delegate_to_agent",
    "delegate_to_researcher", "delegate_to_coder",
    "send_message", "send_image", "proactive_send",
    "memory_note", "edit_soul", "edit_voice", "add_correction",
    "commit_promise", "fulfill_promise", "abandon_promise",
    "set_reminder", "cancel_reminder",
    "plan_task", "update_plan",
    "close_focus", "recall_focus",
})
