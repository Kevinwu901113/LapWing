"""Shell 任务约束、命令意图和恢复策略。"""

import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from src.tools.shell_executor import ShellResult

_CURRENT_USER = getpass.getuser()
_HOME_DIR = str(Path.home())
_PATH_PATTERN = re.compile(r"(~/(?:[A-Za-z0-9._-]+/?)*|/(?!/)(?:[A-Za-z0-9._-]+/?)+)")
_TARGET_DIR_PATTERN = re.compile(
    r"在\s*(/(?:(?!\s)[^，。！？；]*)?)\s*下\s*(?:新建|创建)\s*(?:一个)?\s*"
    r"([A-Za-z0-9._\-\u4e00-\u9fff]+)\s*(?:文件夹|目录)"
)
_FILE_NAME_PATTERN = re.compile(
    r"(?:新建|创建|写入?|保存|追加).{0,12}?"
    r"([A-Za-z0-9._-]+\.[A-Za-z0-9]+)\s*(?:文件)?"
)
_FILE_EXT_PATTERN = re.compile(
    r"(?:新建|创建|写入?|保存|追加).{0,12}?"
    r"([A-Za-z0-9]+)\s*文件(?!夹|目录)"
)
_WRITE_VERBS = {
    "mkdir",
    "touch",
    "mv",
    "cp",
    "tee",
    "chmod",
    "chown",
    "chgrp",
    "ln",
    "install",
    "rm",
    "rmdir",
    "truncate",
    "echo",
    "printf",
}
_DIAGNOSTIC_VERBS = {
    "ls",
    "stat",
    "test",
    "id",
    "whoami",
    "pwd",
    "cat",
    "find",
    "grep",
    "head",
    "tail",
    "wc",
    "file",
    "readlink",
}
_VERIFY_CONTENT_LIMIT = 600


def _normalize_path(path: str) -> str:
    if not path:
        return path

    expanded = os.path.expanduser(path.strip())
    if expanded.startswith("/"):
        return PurePosixPath(expanded).as_posix()
    return expanded


def _unique_paths(paths: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for path in paths:
        normalized = _normalize_path(path.rstrip("，。！？；：,.!?)）]}」』"))
        if normalized and normalized not in seen:
            seen.append(normalized)
    return tuple(seen)


def _path_within(path: str, root: str) -> bool:
    normalized_path = PurePosixPath(_normalize_path(path))
    normalized_root = PurePosixPath(_normalize_path(root))
    return normalized_path == normalized_root or normalized_root in normalized_path.parents


def _ancestors(path: str) -> tuple[str, ...]:
    current = PurePosixPath(_normalize_path(path))
    return tuple(parent.as_posix() for parent in current.parents)


def _extract_paths(text: str) -> tuple[str, ...]:
    return _unique_paths(_PATH_PATTERN.findall(text))


def _normalize_extension(raw: str | None) -> str | None:
    if not raw:
        return None
    stripped = raw.strip().lower().lstrip(".")
    if not stripped:
        return None
    return f".{stripped}"


def _infer_target_directory(user_message: str) -> str | None:
    match = _TARGET_DIR_PATTERN.search(user_message)
    if match is None:
        return None

    base = _normalize_path(match.group(1))
    name = match.group(2).strip()
    if not base or not name:
        return None
    return PurePosixPath(base, name).as_posix()


def _infer_file_name(user_message: str) -> str | None:
    match = _FILE_NAME_PATTERN.search(user_message)
    if match is None:
        return None
    return match.group(1).strip()


def _infer_file_extension(user_message: str, file_name: str | None) -> str | None:
    if file_name and "." in file_name:
        return _normalize_extension(file_name.rsplit(".", 1)[-1])

    match = _FILE_EXT_PATTERN.search(user_message)
    if match is None:
        return None
    return _normalize_extension(match.group(1))


def _looks_like_write_request(user_message: str) -> bool:
    return bool(
        re.search(r"(?:新建|创建|写|写入|保存|追加|覆盖|生成|建立)", user_message)
    )


def _looks_like_confirmation(text: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[\s，。！？])"
            r"(好|可以|行|同意|按你说的|就用那个|用那个|那就这样|那就改到|改到那里|改到那个位置)"
            r"(?:$|[\s，。！？])",
            text,
        )
    )


def _looks_like_rejection(text: str) -> bool:
    return bool(
        re.search(r"(?:^|[\s，。！？])(不要|不用|不行|算了|先别|先不用)(?:$|[\s，。！？])", text)
    )


@dataclass
class ExecutionConstraints:
    """从用户请求中提取出的硬约束。"""

    original_user_message: str
    explicit_paths: tuple[str, ...] = ()
    target_directory: str | None = None
    required_file_parent: str | None = None
    required_filename: str | None = None
    required_extension: str | None = None
    is_write_request: bool = False
    approved_directory: str | None = None

    @property
    def has_hard_path_constraints(self) -> bool:
        return self.target_directory is not None or bool(self.explicit_paths)

    @property
    def active_directory(self) -> str | None:
        return self.approved_directory or self.target_directory

    @property
    def active_file_parent(self) -> str | None:
        if self.active_directory is not None:
            return self.active_directory
        return self.required_file_parent

    @property
    def objective(self) -> Literal["generic", "create_dir", "create_dir_and_file", "create_file"]:
        if self.target_directory and (self.required_filename or self.required_extension):
            return "create_dir_and_file"
        if self.target_directory:
            return "create_dir"
        if self.required_file_parent and (self.required_filename or self.required_extension):
            return "create_file"
        return "generic"

    @property
    def allowed_write_roots(self) -> tuple[str, ...]:
        roots: list[str] = []
        if self.active_directory:
            roots.append(self.active_directory)
        elif self.active_file_parent:
            roots.append(self.active_file_parent)
        return _unique_paths(roots)

    @property
    def hard_paths(self) -> tuple[str, ...]:
        paths: list[str] = []
        if self.target_directory:
            paths.append(self.target_directory)
        if self.required_file_parent and self.required_file_parent not in paths:
            paths.append(self.required_file_parent)
        for path in self.explicit_paths:
            if path not in paths:
                paths.append(path)
        return tuple(paths)

    def describe(self) -> str:
        lines = [f"- objective: {self.objective}"]
        if self.target_directory:
            lines.append(f"- target_directory: {self.target_directory}")
        if self.active_directory and self.active_directory != self.target_directory:
            lines.append(f"- approved_directory: {self.active_directory}")
        if self.required_filename:
            lines.append(f"- required_filename: {self.required_filename}")
        if self.required_extension:
            lines.append(f"- required_extension: {self.required_extension}")
        if self.explicit_paths:
            lines.append(f"- explicit_paths: {', '.join(self.explicit_paths)}")
        if not self.has_hard_path_constraints:
            lines.append("- hard_constraints: none")
        return "\n".join(lines)


@dataclass
class CommandIntent:
    """对单条 shell 命令的粗粒度分析。"""

    command: str
    kind: Literal["diagnostic", "write", "mixed", "unknown"]
    paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    read_paths: tuple[str, ...]

    @property
    def is_write(self) -> bool:
        return self.kind in {"write", "mixed"}

    @property
    def is_diagnostic_only(self) -> bool:
        return self.kind == "diagnostic"


@dataclass
class AlternativeProposal:
    """需要用户确认的替代方案。"""

    directory: str
    reason: str
    blocked_command: str


@dataclass
class VerificationStatus:
    """任务级验证结果。"""

    completed: bool
    directory_path: str | None = None
    file_path: str | None = None
    file_content: str = ""
    reason: str = ""


@dataclass
class ExecutionSessionState:
    """一次 shell 任务执行期间的恢复状态。"""

    constraints: ExecutionConstraints
    failure_reason: str = ""
    failure_type: str | None = None
    diagnostic_commands: list[str] = field(default_factory=list)
    write_commands: list[str] = field(default_factory=list)
    completed: bool = False
    verification: VerificationStatus | None = None
    consent_required: bool = False
    alternative: AlternativeProposal | None = None

    def record_intent(self, intent: CommandIntent) -> None:
        if intent.is_write:
            self.write_commands.append(intent.command)
        elif intent.is_diagnostic_only:
            self.diagnostic_commands.append(intent.command)

    def record_failure(self, reason: str, failure_type: str | None) -> None:
        self.failure_reason = reason
        self.failure_type = failure_type

    def mark_completed(self, verification: VerificationStatus) -> None:
        self.completed = True
        self.verification = verification
        self.failure_reason = ""
        self.failure_type = None

    def require_consent(self, alternative: AlternativeProposal) -> None:
        self.consent_required = True
        self.alternative = alternative

    def clear_consent(self) -> None:
        self.consent_required = False
        self.alternative = None

    def as_system_context(self) -> str:
        allowed_actions = "diagnostic, same-path retry, verification"
        blocked_actions = "rewriting target path, renaming target file, switching directory without consent"
        lines = [
            "## 当前 Shell 任务状态",
            "",
            self.constraints.describe(),
            f"- failure_type: {self.failure_type or 'none'}",
            f"- failure_reason: {self.failure_reason or 'none'}",
            f"- diagnostic_commands: {len(self.diagnostic_commands)}",
            f"- write_commands: {len(self.write_commands)}",
            f"- allowed_actions: {allowed_actions}",
            f"- blocked_actions: {blocked_actions}",
        ]
        if self.alternative is not None:
            lines.append(f"- pending_alternative: {self.alternative.directory}")
        return "\n".join(lines)

    def consent_message(self) -> str:
        if self.alternative is None:
            return "原请求还没有完成，我需要先确认一个替代方案。"
        reason = self.failure_reason or self.alternative.reason
        return (
            f"原请求还没有完成。原因是：{reason}\n\n"
            f"我找到一个可能可行的替代位置：`{self.alternative.directory}`。\n"
            "如果你同意，我就改在那里继续完成刚才的任务；"
            "你回复“可以”就行。"
        )

    def failure_message(self) -> str:
        reason = self.failure_reason or "没有完成原请求。"
        return f"原请求还没有完成。原因是：{reason}"

    def success_message(self) -> str:
        verification = self.verification
        if verification is None:
            return "原请求已经完成了。"

        parts = ["原请求已经完成了。"]
        if verification.directory_path:
            parts.append(f"目录：`{verification.directory_path}`")
        if verification.file_path:
            parts.append(f"文件：`{verification.file_path}`")
        if verification.file_content:
            parts.append(f"内容：\n\n```\n{verification.file_content}\n```")
        return "\n\n".join(parts)


@dataclass
class PendingShellConfirmation:
    """等待用户确认的替代路径方案。"""

    original_user_message: str
    alternative_directory: str
    reason: str


def extract_execution_constraints(
    user_message: str,
    approved_directory: str | None = None,
) -> ExecutionConstraints:
    target_directory = _infer_target_directory(user_message)
    required_filename = _infer_file_name(user_message)
    required_extension = _infer_file_extension(user_message, required_filename)
    is_write_request = _looks_like_write_request(user_message)

    if approved_directory is not None:
        target_directory = _normalize_path(approved_directory)

    required_file_parent = target_directory if required_extension or required_filename else None

    return ExecutionConstraints(
        original_user_message=user_message,
        explicit_paths=_extract_paths(user_message),
        target_directory=target_directory,
        required_file_parent=required_file_parent,
        required_filename=required_filename,
        required_extension=required_extension,
        is_write_request=is_write_request,
        approved_directory=_normalize_path(approved_directory) if approved_directory else None,
    )


def analyze_command(command: str) -> CommandIntent:
    paths = _extract_paths(command)
    normalized = command.strip().lower()

    verbs = re.findall(r"\b([a-z][a-z0-9_-]*)\b", normalized)
    has_write_marker = ">" in normalized or ">>" in normalized
    has_write_verb = any(verb in _WRITE_VERBS for verb in verbs)
    has_diagnostic_verb = any(verb in _DIAGNOSTIC_VERBS for verb in verbs)

    if has_write_verb or has_write_marker:
        kind: Literal["diagnostic", "write", "mixed", "unknown"] = (
            "mixed" if has_diagnostic_verb else "write"
        )
        write_paths = paths
        read_paths = paths if has_diagnostic_verb else ()
    elif has_diagnostic_verb:
        kind = "diagnostic"
        write_paths = ()
        read_paths = paths
    else:
        kind = "unknown"
        write_paths = ()
        read_paths = paths

    return CommandIntent(
        command=command,
        kind=kind,
        paths=paths,
        write_paths=write_paths,
        read_paths=read_paths,
    )


def failure_type_from_result(result: ShellResult) -> str | None:
    if result.blocked:
        return "blocked"
    if result.timed_out:
        return "timeout"
    if result.return_code == 0:
        return None

    lowered = result.stderr.lower()
    if "permission denied" in lowered:
        return "permission_denied"
    if "no such file or directory" in lowered:
        return "not_found"
    return "nonzero_exit"


def failure_reason_from_result(result: ShellResult) -> str:
    if result.reason and (result.blocked or result.timed_out):
        return result.reason

    stderr = result.stderr.strip()
    if stderr:
        return stderr

    if result.reason:
        return result.reason

    if result.timed_out:
        return "命令执行超时了。"

    if result.return_code != 0:
        return f"命令执行失败，退出码 {result.return_code}。"

    return "命令执行失败了。"


def is_confirmation_message(user_message: str) -> bool:
    return _looks_like_confirmation(user_message)


def is_rejection_message(user_message: str) -> bool:
    return _looks_like_rejection(user_message)


def build_followup_message(pending: PendingShellConfirmation) -> str:
    return (
        f"{pending.original_user_message}\n\n"
        f"用户已经同意改到 `{pending.alternative_directory}` 继续完成原任务。"
    )


def is_write_allowed(intent: CommandIntent, constraints: ExecutionConstraints) -> bool:
    if not intent.is_write:
        return True

    roots = constraints.allowed_write_roots
    if not roots:
        return True

    return all(any(_path_within(path, root) for root in roots) for path in intent.write_paths)


def infer_alternative_directory(
    constraints: ExecutionConstraints,
    intent: CommandIntent,
) -> str | None:
    target_directory = constraints.target_directory
    if target_directory is None:
        return None

    target_name = PurePosixPath(target_directory).name
    for path in intent.write_paths:
        normalized = _normalize_path(path)
        candidate = PurePosixPath(normalized)
        if target_name in candidate.parts:
            parts = list(candidate.parts)
            index = parts.index(target_name)
            return PurePosixPath(*parts[: index + 1]).as_posix()

    if target_directory.startswith(f"{_HOME_DIR}/"):
        return None

    if target_directory.count("/") == 2 and target_directory.startswith("/home/"):
        return PurePosixPath(_HOME_DIR, target_name).as_posix()

    return None


def build_alternative_proposal(
    constraints: ExecutionConstraints,
    intent: CommandIntent,
    reason: str,
) -> AlternativeProposal | None:
    candidate = infer_alternative_directory(constraints, intent)
    if candidate is None:
        return None
    return AlternativeProposal(
        directory=candidate,
        reason=reason,
        blocked_command=intent.command,
    )


def infer_permission_denied_alternative(
    constraints: ExecutionConstraints,
) -> str | None:
    """当目标路径在 /home/X（X 不是当前用户）时，推断替代路径 /home/current_user/X。

    用于权限拒绝后的主动恢复：无需等待 LLM 自行发现替代方案。
    """
    target = constraints.target_directory
    if target is None:
        return None
    # 已经在当前用户 home 下，无需替代
    if target.startswith(f"{_HOME_DIR}/"):
        return None
    # /home/X 模式：两段路径且以 /home/ 开头
    if target.count("/") == 2 and target.startswith("/home/"):
        name = PurePosixPath(target).name
        return PurePosixPath(_HOME_DIR, name).as_posix()
    return None


def verify_constraints(constraints: ExecutionConstraints) -> VerificationStatus:
    from src.core.verifier import verify_shell_constraints_status

    return verify_shell_constraints_status(constraints)


def should_validate_after_success(
    constraints: ExecutionConstraints,
    intent: CommandIntent,
    result: ShellResult,
) -> bool:
    return (
        constraints.is_write_request
        and constraints.objective != "generic"
        and intent.is_write
        and result.return_code == 0
        and not result.blocked
        and not result.timed_out
    )


def should_request_consent_for_command(
    constraints: ExecutionConstraints,
    intent: CommandIntent,
    state: ExecutionSessionState,
) -> AlternativeProposal | None:
    if not intent.is_write or not constraints.has_hard_path_constraints:
        return None

    if is_write_allowed(intent, constraints):
        return None

    reason = state.failure_reason or "这会改变你刚才指定的目标路径。"
    return build_alternative_proposal(constraints, intent, reason)


def build_shell_runtime_policy(
    *,
    verify_constraints_fn=verify_constraints,
):
    """兼容导出：构建运行时策略对象。"""
    from src.policy.shell_runtime_policy import ShellRuntimePolicy

    return ShellRuntimePolicy(
        analyze_command=analyze_command,
        should_request_consent_for_command=should_request_consent_for_command,
        failure_type_from_result=failure_type_from_result,
        infer_permission_denied_alternative=infer_permission_denied_alternative,
        should_validate_after_success=should_validate_after_success,
        verify_constraints=verify_constraints_fn,
        failure_reason_builder=failure_reason_from_result,
    )
