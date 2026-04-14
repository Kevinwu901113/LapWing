"""Shell 任务相关的共享数据类型。

从 shell_policy 拆出以打破 shell_policy ↔ verifier 的循环导入。
"""

from dataclasses import dataclass


@dataclass
class VerificationStatus:
    """任务级验证结果。"""

    completed: bool
    directory_path: str | None = None
    file_path: str | None = None
    file_content: str = ""
    reason: str = ""
