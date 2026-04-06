"""self_status 工具 — Lapwing 查看自己的运行状态。"""

from __future__ import annotations

import logging

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.self_status")


async def _execute_self_status(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    from src.core.vitals import system_snapshot

    snapshot = await system_snapshot()

    # 通道连接状态
    channel_info = "（通道信息不可用）"
    channel_manager = context.services.get("channel_manager")
    if channel_manager is not None:
        try:
            statuses = await channel_manager.get_all_status()
            channel_lines = []
            for name, status in statuses.items():
                icon = "🟢" if status.get("connected") else "🔴"
                channel_lines.append(f"  {icon} {name}: {'在线' if status.get('connected') else '离线'}")
            channel_info = "\n".join(channel_lines) if channel_lines else "  没有已注册通道"
        except Exception:
            pass

    # 提醒统计
    memory_info = ""
    if context.memory:
        try:
            chat_id = context.chat_id or ""
            reminders = await context.memory.list_reminders(chat_id) if chat_id else []
            memory_info = f"\n活跃提醒数量：{len(reminders)}"
        except Exception:
            pass

    # 记忆索引统计
    memory_index_info = ""
    if context.memory_index is not None:
        try:
            entries = context.memory_index.all_entries()
            memory_index_info = f"\n记忆条目：{len(entries)} 条"
        except Exception:
            pass

    lines = [
        f"启动时间：{snapshot['boot_time']}",
        f"已运行：{snapshot['uptime']}",
        f"当前时间：{snapshot['now']}",
    ]

    if "cpu_percent" in snapshot:
        lines.extend([
            f"\nCPU：{snapshot['cpu_percent']}",
            f"内存：{snapshot['memory_used_gb']}GB / {snapshot['memory_total_gb']}GB（{snapshot['memory_percent']}）",
            f"磁盘：{snapshot['disk_used_gb']}GB / {snapshot['disk_total_gb']}GB（{snapshot['disk_percent']}）",
        ])
    elif "system_note" in snapshot:
        lines.append(f"\n{snapshot['system_note']}")

    lines.append(f"\n通道状态：\n{channel_info}")
    if memory_info:
        lines.append(memory_info)
    if memory_index_info:
        lines.append(memory_index_info)

    output = "\n".join(lines)
    return ToolExecutionResult(success=True, payload={"output": output})


SELF_STATUS_EXECUTORS = {
    "self_status": _execute_self_status,
}
