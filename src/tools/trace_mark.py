"""trace_mark 工具 — 标记当前执行轨迹供自省时优先回顾。

完成需要 3+ 次工具调用的任务后，如果这次经历值得记录为经验，
用此工具标记。不立即创建 Skill，晚上自省时会优先处理标记的轨迹。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from config.settings import SKILL_TRACES_DIR
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.trace_mark")

_MARKS_SUBDIR = "_marks"


async def trace_mark_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """标记本次任务值得在自省时回顾。

    参数:
        reason (str, 必填): 简短说明为什么这次值得回顾（一句话）
        category (str, 可选): 经验分类，默认 "general"
    """
    reason = str(request.arguments.get("reason", "")).strip()
    if not reason:
        payload = {"success": False, "reason": "缺少 reason 参数"}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 reason 参数")

    category = str(request.arguments.get("category", "general")).strip() or "general"

    # 写入 _marks/ 子目录
    marks_dir = Path(SKILL_TRACES_DIR) / _MARKS_SUBDIR
    try:
        marks_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("创建 _marks 目录失败: %s", exc)
        payload = {"success": False, "reason": f"无法创建标记目录: {exc}"}
        return ToolExecutionResult(success=False, payload=payload, reason=str(exc))

    today = date.today().isoformat()
    # 生成序号
    existing = list(marks_dir.glob(f"{today}_{category}_*.json"))
    seq = len(existing) + 1
    mark_id = f"{today}_{category}_{seq:03d}"

    mark_data = {
        "mark_id": mark_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "reason": reason,
        "reviewed": False,
    }

    mark_path = marks_dir / f"{mark_id}.json"
    try:
        mark_path.write_text(
            json.dumps(mark_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("轨迹标记已写入: %s — %s", mark_id, reason)
    except OSError as exc:
        logger.warning("写入标记文件失败: %s", exc)
        payload = {"success": False, "reason": f"写入失败: {exc}"}
        return ToolExecutionResult(success=False, payload=payload, reason=str(exc))

    payload = {
        "success": True,
        "mark_id": mark_id,
        "reason": reason,
        "message": "已标记，晚上自省时会回顾。",
    }
    return ToolExecutionResult(success=True, payload=payload)


TRACE_MARK_EXECUTORS = {
    "trace_mark": trace_mark_tool,
}
