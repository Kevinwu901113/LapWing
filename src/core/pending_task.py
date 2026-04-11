"""未完成任务数据模型与持久化存储。

当工具循环因超时、循环检测等原因中断且任务未完成时，
将任务状态保存为 PendingTask，供心跳恢复机制使用。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("lapwing.core.pending_task")

# ── 配置常量 ──

TASK_EXPIRY_SECONDS = 3600  # 任务过期时间（1 小时）
MAX_RETRY_COUNT = 3  # 单个 PendingTask 最大重试次数
MAX_TOTAL_RESUMPTIONS = 3  # 同一原始任务跨代际最多恢复次数
MAX_SKIP_COUNT = 5  # Lapwing 最多跳过次数后催促
RETRY_COOLDOWN_SECONDS = 60  # 两次重试之间最短间隔


@dataclass
class PendingTask:
    """一个未完成的任务快照。"""

    task_id: str
    chat_id: str
    user_id: str
    adapter: str
    user_request: str
    completed_steps: list[dict] = field(default_factory=list)
    partial_result: str = ""
    remaining_description: str = ""
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_retry_at: float = 0.0
    termination_reason: str = ""

    # v2 跨代际追踪
    original_task_id: str = ""
    total_resumption_count: int = 0
    skip_count: int = 0

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > TASK_EXPIRY_SECONDS

    def can_retry(self) -> bool:
        if self.retry_count >= MAX_RETRY_COUNT:
            return False
        if self.last_retry_at > 0:
            elapsed = time.time() - self.last_retry_at
            if elapsed < RETRY_COOLDOWN_SECONDS:
                return False
        return True

    def record_retry(self) -> None:
        self.retry_count += 1
        self.last_retry_at = time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PendingTask:
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class PendingTaskStore:
    """基于 JSON 文件的 PendingTask 持久化存储。"""

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                logger.warning("pending_tasks.json 格式异常，重置")
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取 pending_tasks.json 失败: %s", exc)
            return {}

    def _save_all(self, data: dict[str, dict]) -> None:
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("写入 pending_tasks.json 失败: %s", exc)

    def save(self, task: PendingTask) -> None:
        data = self._load_all()
        data[task.task_id] = task.to_dict()
        self._save_all(data)

    def get(self, task_id: str) -> PendingTask | None:
        data = self._load_all()
        raw = data.get(task_id)
        if raw is None:
            return None
        try:
            return PendingTask.from_dict(raw)
        except Exception as exc:
            logger.warning("解析 PendingTask %s 失败: %s", task_id, exc)
            return None

    def remove(self, task_id: str) -> None:
        data = self._load_all()
        if task_id in data:
            del data[task_id]
            self._save_all(data)

    def get_actionable(self) -> list[PendingTask]:
        """获取所有未过期且可重试的任务。"""
        data = self._load_all()
        result = []
        for raw in data.values():
            try:
                task = PendingTask.from_dict(raw)
            except Exception:
                continue
            if not task.is_expired() and task.can_retry():
                result.append(task)
        return result

    def cleanup_expired(self) -> int:
        """清理过期任务，返回清理数量。"""
        data = self._load_all()
        expired_ids = []
        for task_id, raw in data.items():
            try:
                task = PendingTask.from_dict(raw)
                if task.is_expired():
                    expired_ids.append(task_id)
            except Exception:
                expired_ids.append(task_id)

        if expired_ids:
            for tid in expired_ids:
                del data[tid]
            self._save_all(data)
            logger.info("清理了 %d 个过期/损坏的 PendingTask", len(expired_ids))

        return len(expired_ids)

    def list_all(self) -> list[PendingTask]:
        """列出所有任务（包括过期的）。"""
        data = self._load_all()
        result = []
        for raw in data.values():
            try:
                result.append(PendingTask.from_dict(raw))
            except Exception:
                continue
        return result
