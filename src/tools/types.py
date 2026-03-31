"""工具类型定义：统一 schema、执行请求与返回结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from src.tools.shell_executor import ShellResult

ToolRiskLevel = Literal["low", "medium", "high"]
ToolVisibility = Literal["model", "internal"]
ToolExecutor = Callable[["ToolExecutionRequest", "ToolExecutionContext"], Awaitable["ToolExecutionResult"]]


@dataclass(frozen=True)
class ToolExecutionRequest:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    execute_shell: Callable[[str], Awaitable[ShellResult]]
    shell_default_cwd: str
    workspace_root: str = ""
    services: dict[str, Any] = field(default_factory=dict)
    # 身份信息（由 adapter 层注入；默认 OWNER 保持内部 agent/heartbeat 不受限）
    adapter: str = ""
    user_id: str = ""
    auth_level: int = 2  # 2 = AuthLevel.OWNER


@dataclass
class ToolExecutionResult:
    success: bool
    payload: dict[str, Any]
    reason: str = ""
    shell_result: ShellResult | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]
    executor: ToolExecutor
    capability: str = "general"
    capabilities: tuple[str, ...] = ()
    visibility: ToolVisibility = "model"
    risk_level: ToolRiskLevel = "low"
    metadata: dict[str, Any] = field(default_factory=dict)

    def supports_capability(self, capability: str) -> bool:
        all_caps = {self.capability, *self.capabilities}
        return capability in all_caps

    @property
    def is_model_facing(self) -> bool:
        return self.visibility == "model"

    def to_function_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            },
        }
