"""DurableScheduler — 持久化提醒调度器（Phase 4）。

与旧版 ReminderScheduler 完全独立。使用独立的 reminders_v2 表，
避免迁移期间与旧表冲突。支持循环提醒（daily / weekly / interval），
分钟级轮询检查，并向 consciousness engine 提供 urgency_callback。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

logger = logging.getLogger("lapwing.core.durable_scheduler")

# 台北时区
_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _now_taipei() -> datetime:
    """返回当前台北时间（带时区信息）。"""
    return datetime.now(_TAIPEI_TZ)


def _ensure_taipei(dt: datetime) -> datetime:
    """确保 datetime 携带台北时区信息。未带时区时假定为台北时间。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TAIPEI_TZ)
    return dt.astimezone(_TAIPEI_TZ)


@dataclass
class Reminder:
    reminder_id: str          # 例如 "rem_20260416_153000_a1b2"
    due_time: datetime        # 带时区（Asia/Taipei）
    content: str
    repeat: str | None = None              # "daily" / "weekly" / "interval" / None
    interval_minutes: int | None = None    # 仅 "interval" 类型使用
    time_of_day: str | None = None         # 仅 "daily" 类型使用，格式 "HH:MM"
    execution_mode: str = "notify"         # "notify" / "agent"
    created_at: datetime | None = None
    fired: bool = False


def _make_reminder_id() -> str:
    """生成唯一提醒 ID。"""
    now = _now_taipei()
    short = uuid.uuid4().hex[:8]
    return f"rem_{now.strftime('%Y%m%d_%H%M%S')}_{short}"


def _reminder_from_row(row: dict) -> Reminder:
    """从数据库行构造 Reminder 对象。"""
    due_dt = datetime.fromisoformat(row["due_time"])
    due_dt = _ensure_taipei(due_dt)

    created_dt = None
    if row.get("created_at"):
        created_dt = datetime.fromisoformat(row["created_at"])
        created_dt = _ensure_taipei(created_dt)

    return Reminder(
        reminder_id=row["reminder_id"],
        due_time=due_dt,
        content=row["content"],
        repeat=row.get("repeat"),
        interval_minutes=row.get("interval_minutes"),
        time_of_day=row.get("time_of_day"),
        execution_mode=row.get("execution_mode", "notify"),
        created_at=created_dt,
        fired=bool(row.get("fired", 0)),
    )


class DurableScheduler:
    """持久化提醒调度器。

    生命周期:
      1. AppContainer 创建实例，注入 urgency_callback / send_fn / brain
      2. container.start() 时调用 asyncio.create_task(scheduler.run_loop())
      3. container.shutdown() 时调用 await scheduler.stop()
    """

    CHECK_INTERVAL = 60  # 秒，每分钟检查一次

    def __init__(
        self,
        db_path: str | Path,
        urgency_callback=None,
        send_fn=None,
        brain=None,
        dispatcher=None,
        trajectory_store=None,
        mutation_log=None,
        event_queue=None,
    ) -> None:
        # urgency_callback: async def callback(reminder: Reminder)
        # send_fn: async def send(text: str)
        # brain: LapwingBrain 引用 — kept only as a fallback for agent
        # mode when no event_queue is wired (unit tests, phase-0). In
        # production agent-mode fires route through MainLoop via the
        # shared EventQueue, not direct brain calls.
        # trajectory_store / mutation_log: optional — passed to
        # ``send_system_message`` so reminder fires record into the
        # standard audit trail (mirror of ``tell_user``).
        # event_queue: optional — when wired, agent-mode fires push a
        # MessageEvent with done_future so MainLoop's preempt rules
        # (OWNER interruption) apply to scheduled agent runs just like
        # to adapter-driven ones.
        self._db_path = str(db_path)
        self._urgency_callback = urgency_callback
        self._send_fn = send_fn
        self._brain = brain
        self.dispatcher = dispatcher
        self._trajectory_store = trajectory_store
        self._mutation_log = mutation_log
        self._event_queue = event_queue
        self._running = False

    @property
    def urgency_callback(self):
        return self._urgency_callback

    @urgency_callback.setter
    def urgency_callback(self, callback) -> None:
        self._urgency_callback = callback

    @property
    def send_fn(self):
        return self._send_fn

    @send_fn.setter
    def send_fn(self, send_fn) -> None:
        self._send_fn = send_fn

    @property
    def brain(self):
        return self._brain

    @brain.setter
    def brain(self, brain) -> None:
        self._brain = brain

    # ── 公开接口 ────────────────────────────────────────────────────

    async def schedule(
        self,
        due_time: datetime,
        content: str,
        repeat: str | None = None,
        interval_minutes: int | None = None,
        time_of_day: str | None = None,
        execution_mode: str = "notify",
    ) -> str:
        """创建一条提醒，写入数据库，返回 reminder_id。"""
        reminder_id = _make_reminder_id()
        due_dt = _ensure_taipei(due_time)
        created_dt = _now_taipei()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO reminders_v2
                    (reminder_id, due_time, content, repeat, interval_minutes,
                     time_of_day, execution_mode, created_at, fired)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    reminder_id,
                    due_dt.isoformat(),
                    content,
                    repeat,
                    interval_minutes,
                    time_of_day,
                    execution_mode,
                    created_dt.isoformat(),
                ),
            )
            await db.commit()

        logger.info("提醒已创建: %s content=%s repeat=%s", reminder_id, content[:50], repeat)
        return reminder_id

    async def cancel(self, reminder_id: str) -> bool:
        """取消一条未触发的提醒。返回是否成功找到并删除。"""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM reminders_v2 WHERE reminder_id = ? AND fired = 0",
                (reminder_id,),
            )
            await db.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info("提醒已取消: %s", reminder_id)
        else:
            logger.warning("取消失败，未找到未触发的提醒: %s", reminder_id)
        return deleted

    async def list_pending(self) -> list[Reminder]:
        """返回所有未触发的提醒（按到期时间升序）。"""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM reminders_v2 WHERE fired = 0 ORDER BY due_time"
            )
            rows = await cursor.fetchall()

        return [_reminder_from_row(dict(row)) for row in rows]

    async def get_due_soon(self, minutes: int = 30) -> list[Reminder]:
        """返回未来 N 分钟内到期的提醒（供 PromptBuilder 注入上下文）。"""
        now = _now_taipei()
        cutoff = now + timedelta(minutes=minutes)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM reminders_v2
                WHERE fired = 0 AND due_time <= ?
                ORDER BY due_time
                """,
                (cutoff.isoformat(),),
            )
            rows = await cursor.fetchall()

        return [_reminder_from_row(dict(row)) for row in rows]

    async def list_fired(
        self,
        *,
        before_ts: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List fired reminders, newest-first. Returns raw dict rows."""
        query = "SELECT * FROM reminders_v2 WHERE fired = 1"
        params: list = []

        if before_ts is not None:
            # due_time is stored as ISO in Taipei tz — convert `before_ts` to
            # the same tz/format so lexicographic `<` matches chronological `<`.
            cutoff_iso = datetime.fromtimestamp(
                before_ts,
                tz=ZoneInfo("Asia/Taipei"),
            ).isoformat()
            query += " AND due_time < ?"
            params.append(cutoff_iso)

        query += " ORDER BY due_time DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = [dict(r) for r in await cursor.fetchall()]

        return rows

    async def get_due_reminders(
        self,
        chat_id: str = "__all__",
        now: datetime | None = None,
        grace_seconds: int = 1800,
        limit: int = 3,
    ) -> list[dict]:
        """PromptBuilder 兼容接口：返回近期到期提醒的简化字典列表。

        兼容 StateViewBuilder 的 duck-typed reminder_source 签名，使 DurableScheduler
        可直接作为 PromptBuilder 的 reminder_source 使用。
        """
        if now is None:
            now = _now_taipei()
        else:
            now = _ensure_taipei(now) if now.tzinfo else now.replace(tzinfo=_TAIPEI_TZ)

        # 用 grace_seconds 换算为向前展望的分钟数
        minutes = max(grace_seconds // 60, 1)
        due_list = await self.get_due_soon(minutes=minutes)

        result: list[dict] = []
        for r in due_list[:limit]:
            result.append({
                "content": r.content,
                "next_trigger_at": r.due_time.isoformat(),
            })
        return result

    async def check_and_fire(self) -> None:
        """检查所有已到期提醒并触发。"""
        now = _now_taipei()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM reminders_v2 WHERE fired = 0 AND due_time <= ? ORDER BY due_time",
                (now.isoformat(),),
            )
            rows = await cursor.fetchall()

        if not rows:
            return

        for row in rows:
            reminder = _reminder_from_row(dict(row))
            await self._fire_reminder(reminder)

    async def run_loop(self) -> None:
        """主循环：初始化表后每 CHECK_INTERVAL 秒检查一次。"""
        self._running = True
        await self._init_table()
        logger.info("DurableScheduler 已启动，检查间隔 %d 秒", self.CHECK_INTERVAL)

        while self._running:
            try:
                await self.check_and_fire()
            except Exception as exc:
                logger.exception("check_and_fire 异常: %s", exc)

            # 分段 sleep，方便快速响应 stop() 调用
            remaining = self.CHECK_INTERVAL
            while remaining > 0 and self._running:
                chunk = min(remaining, 5)
                await asyncio.sleep(chunk)
                remaining -= chunk

        logger.info("DurableScheduler 已停止")

    async def stop(self) -> None:
        """停止主循环。"""
        self._running = False

    # ── 内部实现 ────────────────────────────────────────────────────

    async def _init_table(self) -> None:
        """创建 reminders_v2 表（如不存在）。"""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders_v2 (
                    reminder_id     TEXT PRIMARY KEY,
                    due_time        TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    repeat          TEXT,
                    interval_minutes INTEGER,
                    time_of_day     TEXT,
                    execution_mode  TEXT DEFAULT 'notify',
                    created_at      TEXT NOT NULL,
                    fired           INTEGER DEFAULT 0
                )
                """
            )
            # 为到期查询建立索引
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_v2_due ON reminders_v2 (fired, due_time)"
            )
            await db.commit()
        logger.debug("reminders_v2 表已就绪")

    async def _fire_reminder(self, reminder: Reminder) -> None:
        """触发一条提醒：标记已触发、处理循环、调用回调和发送。"""
        # 先标记为已触发（防止重复触发）
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE reminders_v2 SET fired = 1 WHERE reminder_id = ? AND fired = 0",
                (reminder.reminder_id,),
            )
            await db.commit()

        logger.info("触发提醒: %s content=%s mode=%s", reminder.reminder_id, reminder.content[:50], reminder.execution_mode)

        if self.dispatcher is not None:
            try:
                await self.dispatcher.submit(
                    "reminder.fired",
                    payload={
                        "reminder_id": reminder.reminder_id,
                        "content": reminder.content[:200],
                        "execution_mode": reminder.execution_mode,
                        "due_time": reminder.due_time.isoformat(),
                    },
                    actor="lapwing",
                )
            except Exception:
                logger.debug("reminder.fired 事件提交失败", exc_info=True)

        # 若为循环提醒，创建下一次
        if reminder.repeat:
            try:
                next_dt = self._calc_next(
                    reminder.due_time,
                    reminder.repeat,
                    interval_minutes=reminder.interval_minutes,
                    time_of_day=reminder.time_of_day,
                )
                await self.schedule(
                    due_time=next_dt,
                    content=reminder.content,
                    repeat=reminder.repeat,
                    interval_minutes=reminder.interval_minutes,
                    time_of_day=reminder.time_of_day,
                    execution_mode=reminder.execution_mode,
                )
                logger.info("循环提醒下一次已调度: %s -> %s", reminder.reminder_id, next_dt.isoformat())
            except Exception as exc:
                logger.error("创建下一次循环提醒失败: %s err=%s", reminder.reminder_id, exc)

        # 调用 urgency_callback（Step 4 M3 起：注入到 InnerTickScheduler.push_urgency）
        if self._urgency_callback is not None:
            try:
                await self._urgency_callback(reminder)
            except Exception as exc:
                logger.error("urgency_callback 异常: %s err=%s", reminder.reminder_id, exc)

        # 按执行模式分发
        if reminder.execution_mode == "agent":
            await self._fire_agent(reminder)
        else:
            await self._fire_notify(reminder)

    async def _fire_notify(self, reminder: Reminder) -> None:
        """notify 模式：直接发送提醒文本。"""
        if self._send_fn is None:
            logger.warning("notify 模式缺少 send_fn，跳过发送: %s", reminder.reminder_id)
            return

        from src.core.system_send import send_system_message

        message = f"⏰ {reminder.content}"
        await send_system_message(
            self._send_fn,
            message,
            source="reminder_notify",
            trajectory_store=self._trajectory_store,
            mutation_log=self._mutation_log,
        )

    async def _fire_agent(self, reminder: Reminder) -> None:
        """agent 模式：通过 MainLoop 执行完整对话循环。

        Flow: build a MessageEvent with a silent send_fn (tool-loop
        chatter should not echo directly — only the final reply goes
        to the user) + a ``done_future`` for the result. Push onto the
        shared ``EventQueue``; MainLoop dispatches via
        ``_handle_message``, so OWNER preempt rules apply here the same
        way they do for adapter-driven messages. Await the future, then
        system_send-route the final reply so trajectory + mutation_log
        capture the user-visible byte.

        Fallbacks: if no event_queue is wired (unit tests, phase-0) the
        scheduler falls back to a direct brain call for parity.
        """
        from src.core.system_send import send_system_message

        if self._event_queue is None and self._brain is None:
            logger.warning(
                "agent 模式缺少 event_queue 和 brain，fallback 到 notify: %s",
                reminder.reminder_id,
            )
            await self._fire_notify(reminder)
            return

        async def _silent_send(text: str) -> None:
            logger.debug("DurableScheduler agent 中间输出（不发送）: %s", text[:80])

        user_message = f"[定时任务] {reminder.content}"

        try:
            if self._event_queue is not None:
                from src.core.authority_gate import AuthLevel
                from src.core.events import MessageEvent

                done = asyncio.get_running_loop().create_future()
                event = MessageEvent.from_message(
                    chat_id="__scheduler__",
                    user_id="__scheduler__",
                    text=user_message,
                    adapter="system",
                    send_fn=_silent_send,
                    auth_level=int(AuthLevel.TRUSTED),
                    done_future=done,
                )
                await self._event_queue.put(event)
                result = await done
            else:
                # Fallback path for contexts without MainLoop (tests /
                # phase-0). Kept strictly for parity; production flows
                # always wire event_queue via AppContainer.
                result = await self._brain.think_conversational(
                    chat_id="__scheduler__",
                    user_message=user_message,
                    send_fn=_silent_send,
                    adapter="system",
                    user_id="__scheduler__",
                )

            if result and self._send_fn is not None:
                await send_system_message(
                    self._send_fn,
                    result,
                    source="reminder_agent_result",
                    chat_id="__scheduler__",
                    adapter="system",
                    trajectory_store=self._trajectory_store,
                    mutation_log=self._mutation_log,
                )

        except Exception as exc:
            logger.error(
                "agent 模式执行失败，fallback 到 notify: %s err=%s",
                reminder.reminder_id, exc,
            )
            if self._send_fn is not None:
                await send_system_message(
                    self._send_fn,
                    f"⏰ {reminder.content}\n（自动执行失败，仅提醒）",
                    source="reminder_agent_fallback",
                    chat_id="__scheduler__",
                    adapter="system",
                    trajectory_store=self._trajectory_store,
                    mutation_log=self._mutation_log,
                )

    def _calc_next(
        self,
        current: datetime,
        repeat: str,
        interval_minutes: int | None = None,
        time_of_day: str | None = None,
    ) -> datetime:
        """计算循环提醒的下一次触发时间。"""
        base = _ensure_taipei(current)

        if repeat == "interval":
            if not interval_minutes or interval_minutes <= 0:
                raise ValueError("interval 类型需要有效的 interval_minutes")
            return base + timedelta(minutes=interval_minutes)

        elif repeat == "daily":
            if time_of_day:
                try:
                    h, m = map(int, time_of_day.split(":"))
                    next_dt = base.replace(hour=h, minute=m, second=0, microsecond=0)
                    # 确保是明天或更晚的时间
                    while next_dt <= base:
                        next_dt = next_dt + timedelta(days=1)
                    return next_dt
                except (ValueError, TypeError):
                    pass
            # time_of_day 解析失败则简单加一天
            return base + timedelta(days=1)

        elif repeat == "weekly":
            if time_of_day:
                try:
                    h, m = map(int, time_of_day.split(":"))
                    next_dt = base.replace(hour=h, minute=m, second=0, microsecond=0)
                    while next_dt <= base:
                        next_dt = next_dt + timedelta(weeks=1)
                    return next_dt
                except (ValueError, TypeError):
                    pass
            return base + timedelta(weeks=1)

        else:
            raise ValueError(f"未知的 repeat 类型: {repeat}")


# ── 工具执行器 ───────────────────────────────────────────────────────

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult  # noqa: E402


def _get_scheduler(ctx: ToolExecutionContext) -> DurableScheduler | None:
    """从 services 字典中取出 DurableScheduler 实例。"""
    return ctx.services.get("durable_scheduler")


def _parse_time_str(time_str: str) -> datetime | None:
    """解析 'YYYY-MM-DD HH:MM' 格式的时间字符串（台北时间）。"""
    time_str = time_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(time_str, fmt)
            return dt.replace(tzinfo=_TAIPEI_TZ)
        except ValueError:
            continue
    return None


async def set_reminder_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """设置提醒工具执行器。

    参数:
      time (str, 必填): 触发时间，格式 "YYYY-MM-DD HH:MM"（台北时间）
      content (str, 必填): 提醒内容
      repeat (str, 可选): "daily" / "weekly" / "interval"，不填表示单次
      interval_minutes (int, 可选): repeat="interval" 时的间隔分钟数
      time_of_day (str, 可选): repeat="daily"/"weekly" 时的每日时间 "HH:MM"
      execution_mode (str, 可选): "notify"（默认）/ "agent"
    """
    scheduler = _get_scheduler(ctx)
    if scheduler is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "DurableScheduler 不可用（未初始化）"},
            reason="scheduler_unavailable",
        )

    args = req.arguments
    time_str = str(args.get("time", "")).strip()
    content = str(args.get("content", "")).strip()
    repeat = args.get("repeat")
    interval_minutes = args.get("interval_minutes")
    time_of_day = args.get("time_of_day")
    execution_mode = str(args.get("execution_mode", "notify")).strip()

    if not time_str:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 time 参数，需要格式 YYYY-MM-DD HH:MM"},
            reason="missing_time",
        )
    if not content:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 content 参数"},
            reason="missing_content",
        )
    if execution_mode not in ("notify", "agent"):
        execution_mode = "notify"

    due_time = _parse_time_str(time_str)
    if due_time is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"无法解析时间 '{time_str}'，请使用 YYYY-MM-DD HH:MM 格式"},
            reason="invalid_time",
        )

    # 校验 interval_minutes
    if repeat == "interval":
        try:
            interval_minutes = int(interval_minutes or 0)
            if interval_minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return ToolExecutionResult(
                success=False,
                payload={"error": "repeat='interval' 时需要有效的 interval_minutes（正整数）"},
                reason="invalid_interval_minutes",
            )
    else:
        interval_minutes = None

    try:
        reminder_id = await scheduler.schedule(
            due_time=due_time,
            content=content,
            repeat=repeat or None,
            interval_minutes=interval_minutes,
            time_of_day=time_of_day or None,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        logger.exception("set_reminder_executor 异常: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"创建提醒失败: {exc}"},
            reason="db_error",
        )

    # 生成人性化描述
    repeat_label = {
        "daily": "每天",
        "weekly": "每周",
        "interval": f"每隔 {interval_minutes} 分钟",
    }.get(repeat or "", "单次")
    output = f"已设置提醒 [{reminder_id}]：{content}（{time_str}，{repeat_label}，{execution_mode} 模式）"

    return ToolExecutionResult(
        success=True,
        payload={"output": output, "reminder_id": reminder_id},
    )


async def view_reminders_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """查看所有未触发的提醒（最多 20 条）。"""
    scheduler = _get_scheduler(ctx)
    if scheduler is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "DurableScheduler 不可用（未初始化）"},
            reason="scheduler_unavailable",
        )

    try:
        pending = await scheduler.list_pending()
    except Exception as exc:
        logger.exception("view_reminders_executor 异常: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"查询失败: {exc}"},
            reason="db_error",
        )

    if not pending:
        return ToolExecutionResult(
            success=True,
            payload={"output": "当前没有待触发的提醒。"},
        )

    lines = []
    for r in pending[:20]:
        repeat_label = {
            "daily": "每天",
            "weekly": "每周",
            "interval": f"每隔{r.interval_minutes}分钟",
        }.get(r.repeat or "", "单次")
        due_str = r.due_time.strftime("%Y-%m-%d %H:%M")
        lines.append(f"  [{r.reminder_id}] {r.content[:60]} — {due_str} — {repeat_label}")

    total = len(pending)
    shown = min(total, 20)
    header = f"待触发提醒（{shown}/{total} 条）:\n"
    return ToolExecutionResult(
        success=True,
        payload={"output": header + "\n".join(lines)},
    )


async def cancel_reminder_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """取消一条未触发的提醒。

    参数:
      reminder_id (str, 必填): 提醒 ID，例如 "rem_20260416_153000_a1b2"
    """
    scheduler = _get_scheduler(ctx)
    if scheduler is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "DurableScheduler 不可用（未初始化）"},
            reason="scheduler_unavailable",
        )

    reminder_id = str(req.arguments.get("reminder_id", "")).strip()
    if not reminder_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 reminder_id 参数"},
            reason="missing_reminder_id",
        )

    try:
        success = await scheduler.cancel(reminder_id)
    except Exception as exc:
        logger.exception("cancel_reminder_executor 异常: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"取消失败: {exc}"},
            reason="db_error",
        )

    if success:
        return ToolExecutionResult(
            success=True,
            payload={"output": f"已取消提醒 [{reminder_id}]"},
        )
    else:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"未找到未触发的提醒: {reminder_id}"},
            reason="not_found",
        )


# 导出工具执行器映射，方便注册
DURABLE_SCHEDULER_EXECUTORS: dict[str, Any] = {
    "set_reminder": set_reminder_executor,
    "view_reminders": view_reminders_executor,
    "cancel_reminder": cancel_reminder_executor,
}
