"""SystemHealthAction — 定期检查系统健康，异常时主动通知。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.system_health")

# 阈值
_MEMORY_WARN_PERCENT = 85.0
_DISK_WARN_PERCENT = 90.0
_CPU_WARN_PERCENT = 90.0


class SystemHealthAction(HeartbeatAction):
    name = "system_health_check"
    description = "检查系统健康状态（CPU/内存/磁盘），异常时通知用户"
    beat_types = ["fast"]
    selection_mode = "always"  # 每次快心跳都检查，不经过 LLM 决策

    def __init__(self) -> None:
        self._last_alert_key: str | None = None  # 避免重复告警

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        try:
            import psutil
        except ImportError:
            return  # 没有 psutil 就跳过

        alerts: list[str] = []

        mem = psutil.virtual_memory()
        if mem.percent >= _MEMORY_WARN_PERCENT:
            alerts.append(f"内存用了 {mem.percent:.0f}%（{mem.used / (1024**3):.1f}GB / {mem.total / (1024**3):.1f}GB）")

        disk = psutil.disk_usage("/")
        if disk.percent >= _DISK_WARN_PERCENT:
            alerts.append(f"磁盘用了 {disk.percent:.0f}%")

        cpu = psutil.cpu_percent(interval=1)
        if cpu >= _CPU_WARN_PERCENT:
            alerts.append(f"CPU 占用 {cpu:.0f}%")

        # 通道断线检查
        if brain.channel_manager is not None:
            try:
                statuses = await brain.channel_manager.get_all_status()
                for name, status in statuses.items():
                    if name == "desktop":
                        continue  # desktop 离线是正常的
                    if not status.get("connected"):
                        alerts.append(f"{name} 通道断线了")
            except Exception:
                pass

        if not alerts:
            self._last_alert_key = None
            return

        alert_key = "|".join(sorted(alerts))
        if alert_key == self._last_alert_key:
            return  # 同样的告警不重复发

        self._last_alert_key = alert_key

        # 用 LLM 生成自然的告警消息
        alert_text = "；".join(alerts)
        prompt = (
            f"你是 Lapwing。你注意到自己身体（服务器）有点不对劲：{alert_text}。\n"
            f"用你平时发微信的方式简短告诉 Kuan 一声。不要太慌张，也不要太正式。\n"
            f"一两句话就好。"
        )

        try:
            message = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                slot="heartbeat_proactive",
                max_tokens=100,
                session_key=f"chat:{ctx.chat_id}",
                origin="heartbeat.system_health",
            )
            if message:
                await send_fn(message)
                await brain.memory.append(ctx.chat_id, "assistant", message)
                logger.info("[%s] 系统健康告警已发送: %s", ctx.chat_id, alert_text)
        except Exception as exc:
            logger.error("[%s] 系统健康告警发送失败: %s", ctx.chat_id, exc)
