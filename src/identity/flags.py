from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass
class IdentityFlags:
    """身份子系统各模块的特性开关。

    - 前缀为 *_enabled 的字段控制各模块的启停。
    - identity_system_killswitch 为全局主开关：设为 True 时所有组件均视为关闭。
    - Ticket A 模块（parser / store / retriever）默认开启。
    - Ticket B 模块（injector / gate / reviewer / l1_memory / evolution）默认关闭，
      待对应子任务实现后再打开。
    """

    # --- Ticket A 模块 ---
    parser_enabled: bool = True       # Module 2: 身份块解析
    store_enabled: bool = True        # Module 3: 身份存储
    retriever_enabled: bool = True    # Module 4: 身份检索

    # --- Ticket B 模块（默认关闭）---
    injector_enabled: bool = False    # Module 5: 提示注入
    gate_enabled: bool = False        # Module 6: 门控逻辑
    reviewer_enabled: bool = False    # Module 8: 评审器
    l1_memory_enabled: bool = False   # Module 9: L1 记忆集成
    evolution_enabled: bool = False   # Module 10: 演化引擎

    # --- 全局主开关 ---
    identity_system_killswitch: bool = False

    def is_active(self, component: str) -> bool:
        """返回指定组件是否处于激活状态。

        若 identity_system_killswitch 为 True，所有组件均返回 False。
        否则查找 {component}_enabled 字段并返回其值；未知组件返回 False。
        """
        if self.identity_system_killswitch:
            return False
        field_name = f"{component}_enabled"
        return getattr(self, field_name, False)

    def current(self) -> dict:
        """返回所有字段的快照字典，供日志 / 审计使用。"""
        return dataclasses.asdict(self)
