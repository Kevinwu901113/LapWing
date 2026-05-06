"""工具类型定义：统一 schema、执行请求与返回结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
    # 对话上下文（供需要写入数据库的工具使用，如 reminder 相关工具）
    chat_id: str = ""
    focus_id: str | None = None
    memory: Any = None  # 保留供 Agent ToolExecutionContext 兼容
    memory_index: Any = None  # MemoryIndex 实例（可选）
    # send_message 工具的用户通道。None 表示当前上下文没有可达用户。
    send_fn: Callable[[str], Awaitable[Any]] | None = None
    # The RuntimeProfile name under which this tool is being executed.
    # Lets approval gates (e.g. run_skill) refuse autonomous execution of
    # draft/testing/broken skills from standard or inner_tick.
    runtime_profile: str = ""


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_ERROR = "permission_error"
    PRECONDITION_ERROR = "precondition_error"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT_ERROR = "timeout_error"
    DEPENDENCY_ERROR = "dependency_error"
    INTERNAL_ERROR = "internal_error"


class ToolErrorCode(str, Enum):
    SCHEMA_VALIDATION_FAILED = "tool.schema_validation_failed"
    PRECONDITION_FAILED = "tool.precondition_failed"
    EXECUTION_FAILED = "tool.execution_failed"
    TIMEOUT = "tool.timeout"
    PERMISSION_DENIED = "tool.permission_denied"
    DEPENDENCY_UNAVAILABLE = "tool.dependency_unavailable"
    INTERNAL_ERROR = "tool.internal_error"


class ToolErrorClass(str, Enum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    PRECONDITION = "precondition"
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    DEPENDENCY = "dependency"
    INTERNAL = "internal"


DETAILS_SCHEMA_VERSION = "tool_error.v1"


@dataclass(frozen=True)
class ToolErrorPayload:
    status: ToolResultStatus
    error_code: ToolErrorCode
    error_class: ToolErrorClass
    retryable: bool
    safe_details: dict[str, Any] = field(default_factory=dict)
    details_schema_version: str = DETAILS_SCHEMA_VERSION

    def to_payload(self, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(base or {})
        payload.update({
            "status": self.status.value,
            "error_code": self.error_code.value,
            "error_class": self.error_class.value,
            "retryable": self.retryable,
            "safe_details": self.safe_details,
            "details_schema_version": self.details_schema_version,
        })
        return payload


@dataclass
class ToolExecutionResult:
    success: bool
    payload: dict[str, Any]
    reason: str = ""
    shell_result: ShellResult | None = None
    status: ToolResultStatus | str | None = None
    error_code: ToolErrorCode | str | None = None
    error_class: ToolErrorClass | str | None = None
    retryable: bool | None = None
    safe_details: dict[str, Any] | None = None
    details_schema_version: str | None = None

    def __post_init__(self) -> None:
        payload = self.payload if isinstance(self.payload, dict) else {}
        if self.status is None:
            self.status = payload.get("status") or (
                ToolResultStatus.SUCCESS if self.success else ToolResultStatus.EXECUTION_ERROR
            )
        if self.error_code is None:
            self.error_code = payload.get("error_code")
        if self.error_class is None:
            self.error_class = payload.get("error_class")
        if self.retryable is None:
            retryable = payload.get("retryable")
            self.retryable = bool(retryable) if retryable is not None else None
        if self.safe_details is None:
            details = payload.get("safe_details")
            self.safe_details = details if isinstance(details, dict) else None
        if self.details_schema_version is None:
            version = payload.get("details_schema_version")
            self.details_schema_version = str(version) if version else None


def make_tool_error_result(
    *,
    status: ToolResultStatus,
    error_code: ToolErrorCode,
    error_class: ToolErrorClass,
    retryable: bool,
    safe_details: dict[str, Any],
    reason: str = "",
    base_payload: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    error = ToolErrorPayload(
        status=status,
        error_code=error_code,
        error_class=error_class,
        retryable=retryable,
        safe_details=safe_details,
    )
    payload = error.to_payload(base=base_payload)
    return ToolExecutionResult(
        success=False,
        payload=payload,
        reason=reason or _reason_from_error(error_code),
        status=status,
        error_code=error_code,
        error_class=error_class,
        retryable=retryable,
        safe_details=safe_details,
        details_schema_version=DETAILS_SCHEMA_VERSION,
    )


def _reason_from_error(error_code: ToolErrorCode) -> str:
    if error_code == ToolErrorCode.SCHEMA_VALIDATION_FAILED:
        return "tool arguments failed schema validation"
    if error_code == ToolErrorCode.PERMISSION_DENIED:
        return "tool execution denied by policy"
    if error_code == ToolErrorCode.TIMEOUT:
        return "tool execution timed out"
    if error_code == ToolErrorCode.DEPENDENCY_UNAVAILABLE:
        return "tool dependency unavailable"
    if error_code == ToolErrorCode.INTERNAL_ERROR:
        return "tool internal error"
    if error_code == ToolErrorCode.PRECONDITION_FAILED:
        return "tool precondition failed"
    return "tool execution failed"


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
    # Phase 4: 工具重铸扩展字段
    max_result_tokens: int = 2000
    trust_required: str = "guest"       # 最低信任级别: "guest" / "trusted" / "owner"
    destructive: bool = False            # 是否不可逆操作
    owner_confirm: bool = False          # 是否需要 OWNER 确认

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
