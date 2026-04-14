"""LLM 路由器数据类型。"""

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCallRequest:
    """统一后的工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurnResult:
    """带工具能力的一轮模型响应。"""

    text: str
    tool_calls: list[ToolCallRequest]
    continuation_message: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelOption:
    index: int
    ref: str
    alias: str | None = None
