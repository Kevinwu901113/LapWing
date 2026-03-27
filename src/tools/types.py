"""工具类型定义：统一 schema、执行请求与返回结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from src.tools.shell_executor import ShellResult

ToolRiskLevel = Literal["low", "medium", "high"]
ToolExecutor = Callable[["ToolExecutionRequest", "ToolExecutionContext"], Awaitable["ToolExecutionResult"]]


@dataclass(frozen=True)
class ToolExecutionRequest:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    execute_shell: Callable[[str], Awaitable[ShellResult]]
    shell_default_cwd: str


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
    risk_level: ToolRiskLevel = "low"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_function_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            },
        }
