"""PromptSnapshotManager — frozen system prompt cache.

Historically bundled with PromptBuilder; promoted to its own module in
Step 3 M2.f so the PromptBuilder class can be deleted without taking
the snapshot cache with it. M3 decides whether the snapshot itself
survives the refactor (see docs/refactor_v2/step3_prompt_snapshot_analysis.md).
"""

from __future__ import annotations


class PromptSnapshotManager:
    """冻结 system prompt 快照，实现 session 内复用 + prefix 缓存。

    在同一个 session 内，system prompt 只构建一次。后续用户消息复用冻结的快照，
    使 Anthropic 端的 prefix cache 命中率最大化。
    """

    def __init__(self) -> None:
        self._frozen: str | None = None
        self._session_id: str | None = None

    def freeze(self, session_id: str, prompt: str) -> str:
        """冻结当前 prompt 快照，绑定到 session_id。"""
        self._frozen = prompt
        self._session_id = session_id
        return prompt

    def get(self, session_id: str) -> str | None:
        """获取缓存的快照（仅当 session_id 匹配时返回）。"""
        if self._frozen and self._session_id == session_id:
            return self._frozen
        return None

    def invalidate(self) -> None:
        """清除快照（模型切换、/reload 等场景）。"""
        self._frozen = None
        self._session_id = None
