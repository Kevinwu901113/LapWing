"""多步骤任务流 — 持久化状态、优雅取消、进度推送。

TaskFlowManager 管理所有活跃任务流，每个流的状态以 JSON checkpoint
保存在 data/tasks/{flow_id}.json，支持崩溃后恢复。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Awaitable

from config.settings import DATA_DIR

logger = logging.getLogger("lapwing.core.task_flow")

FLOWS_DIR = DATA_DIR / "tasks"


@dataclass
class TaskStep:
    step_id: str
    description: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    tool_name: str | None = None
    tool_args: dict | None = None
    result: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class TaskFlow:
    flow_id: str
    title: str
    chat_id: str
    steps: list[TaskStep] = field(default_factory=list)
    current_step_index: int = 0
    status: str = "pending"  # pending | running | completed | failed | cancelled
    cancel_intent: bool = False
    state_revision: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def checkpoint_path(self) -> Path:
        return FLOWS_DIR / f"{self.flow_id}.json"

    @property
    def progress_pct(self) -> int:
        if not self.steps:
            return 0
        done = sum(1 for s in self.steps if s.status in ("completed", "failed", "cancelled"))
        return int(done / len(self.steps) * 100)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["progress_pct"] = self.progress_pct
        return d


class TaskFlowManager:
    """管理所有活跃的任务流。"""

    def __init__(self) -> None:
        self._active: dict[str, TaskFlow] = {}
        self._notification_queue: asyncio.Queue[dict] = asyncio.Queue()
        FLOWS_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def notification_queue(self) -> asyncio.Queue:
        return self._notification_queue

    def create_flow(self, *, title: str, chat_id: str, steps: list[dict]) -> TaskFlow:
        """创建新的任务流，立即保存 checkpoint。"""
        flow = TaskFlow(
            flow_id=str(uuid.uuid4())[:8],
            title=title,
            chat_id=chat_id,
            steps=[
                TaskStep(
                    step_id=f"step_{i + 1}",
                    description=s["description"],
                    tool_name=s.get("tool_name"),
                    tool_args=s.get("tool_args"),
                )
                for i, s in enumerate(steps)
            ],
        )
        self._active[flow.flow_id] = flow
        self._save_checkpoint(flow)
        return flow

    def get_flow(self, flow_id: str) -> TaskFlow | None:
        return self._active.get(flow_id)

    def list_active(self, chat_id: str | None = None) -> list[TaskFlow]:
        flows = list(self._active.values())
        if chat_id:
            flows = [f for f in flows if f.chat_id == chat_id]
        return flows

    def cancel_flow(self, flow_id: str) -> bool:
        """设置 cancel_intent，让当前步骤完成后优雅停止。"""
        flow = self._active.get(flow_id)
        if not flow or flow.status not in ("pending", "running"):
            return False
        flow.cancel_intent = True
        self._save_checkpoint(flow)
        logger.info("任务流「%s」收到取消意图", flow.title)
        return True

    async def execute_flow(
        self,
        flow: TaskFlow,
        tool_executor: Callable[[str, dict], Awaitable[Any]],
    ) -> None:
        """执行任务流。

        Args:
            flow: 要执行的任务流。
            tool_executor: async callable(tool_name, tool_args) -> result_str。
        """
        flow.status = "running"
        self._save_checkpoint(flow)

        for i in range(flow.current_step_index, len(flow.steps)):
            # 检查取消意图（在每步开始前）
            if flow.cancel_intent:
                flow.status = "cancelled"
                for j in range(i, len(flow.steps)):
                    flow.steps[j].status = "cancelled"
                self._save_checkpoint(flow)
                await self._notify(flow, "任务已取消，已完成的步骤结果已保留")
                self._cleanup(flow)
                return

            step = flow.steps[i]
            flow.current_step_index = i
            step.status = "running"
            step.started_at = datetime.now().isoformat()
            self._save_checkpoint(flow)

            await self._notify(flow, f"正在执行：{step.description}")

            try:
                result = await tool_executor(step.tool_name or "", step.tool_args or {})
                step.result = str(result)[:2000]
                step.status = "completed"
            except Exception as e:
                step.status = "failed"
                step.result = str(e)[:500]
                step.completed_at = datetime.now().isoformat()
                flow.status = "failed"
                self._save_checkpoint(flow)
                await self._notify(flow, f"步骤「{step.description}」失败：{e}")
                self._cleanup(flow)
                return

            step.completed_at = datetime.now().isoformat()
            self._save_checkpoint(flow)
            await self._notify(flow, f"步骤「{step.description}」完成")

        flow.status = "completed"
        self._save_checkpoint(flow)
        await self._notify(flow, "全部完成")
        self._cleanup(flow)

    def _save_checkpoint(self, flow: TaskFlow) -> None:
        flow.state_revision += 1
        flow.updated_at = datetime.now().isoformat()
        tmp = flow.checkpoint_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(flow.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(flow.checkpoint_path)

    def _cleanup(self, flow: TaskFlow) -> None:
        """完成/失败/取消后从活跃列表移除（checkpoint 保留在磁盘）。"""
        self._active.pop(flow.flow_id, None)

    async def _notify(self, flow: TaskFlow, message: str) -> None:
        completed = [
            f"[{s.description}]: {s.result[:200] if s.result else '(无输出)'}"
            for s in flow.steps
            if s.status == "completed" and s.result
        ]
        remaining = [s.description for s in flow.steps if s.status == "pending"]

        await self._notification_queue.put({
            "type": "task_progress",
            "flow_id": flow.flow_id,
            "title": flow.title,
            "chat_id": flow.chat_id,
            "message": message,
            "status": flow.status,
            "progress_pct": flow.progress_pct,
            "completed_steps": completed,
            "remaining_steps": remaining,
        })

    def load_pending_flows(self) -> list[TaskFlow]:
        """启动时恢复 status==running 的任务流（等待重新触发执行）。"""
        recovered: list[TaskFlow] = []
        for f in FLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") not in ("running", "pending"):
                    continue
                flow = TaskFlow(
                    flow_id=data["flow_id"],
                    title=data["title"],
                    chat_id=data["chat_id"],
                    steps=[TaskStep(**s) for s in data["steps"]],
                    current_step_index=data.get("current_step_index", 0),
                    status=data["status"],
                    cancel_intent=data.get("cancel_intent", False),
                    state_revision=data.get("state_revision", 0),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                )
                self._active[flow.flow_id] = flow
                recovered.append(flow)
                logger.info("恢复任务流：「%s」（步骤 %d/%d）",
                            flow.title, flow.current_step_index, len(flow.steps))
            except Exception as e:
                logger.warning("恢复任务流失败 %s: %s", f.name, e)
        return recovered
