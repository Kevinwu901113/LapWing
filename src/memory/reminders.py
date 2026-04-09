"""提醒系统数据访问。"""

import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("lapwing.memory.reminders")

_VALID_RECURRENCE_TYPES = {"once", "daily", "weekly", "interval"}
_TIME_OF_DAY_PATTERN = re.compile(r"^\d{2}:\d{2}$")


class ReminderRepository:
    """管理提醒的数据访问。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def add_reminder(
        self,
        chat_id: str,
        content: str,
        recurrence_type: str,
        next_trigger_at: datetime | str,
        weekday: int | None = None,
        time_of_day: str | None = None,
        interval_minutes: int | None = None,
    ) -> int:
        try:
            normalized_content = str(content).strip()
            if not normalized_content:
                return 0

            recurrence = str(recurrence_type).strip().lower()
            if recurrence not in _VALID_RECURRENCE_TYPES:
                return 0

            next_dt = self._ensure_utc_datetime(next_trigger_at)
            now = datetime.now(timezone.utc)
            if recurrence == "once":
                if next_dt <= now:
                    return 0
                normalized_weekday = None
                normalized_time = None
                normalized_interval = None
            elif recurrence == "interval":
                normalized_weekday = None
                normalized_time = None
                normalized_interval = int(interval_minutes) if interval_minutes else None
                if not normalized_interval or normalized_interval <= 0:
                    return 0
                if next_dt <= now:
                    next_dt = now + timedelta(minutes=normalized_interval)
            else:
                normalized_weekday = self._normalize_weekday(weekday)
                normalized_time = self._normalize_time_of_day(time_of_day) or next_dt.strftime("%H:%M")
                normalized_interval = None
                if recurrence == "weekly" and normalized_weekday is None:
                    return 0
                if next_dt <= now:
                    next_dt = self._advance_to_future(next_dt, recurrence, now)

            created_at = now.isoformat()
            cursor = await self._db.execute(
                """INSERT INTO reminders (
                       chat_id, content, recurrence_type, next_trigger_at,
                       weekday, time_of_day, interval_minutes, active, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    chat_id,
                    normalized_content,
                    recurrence,
                    next_dt.isoformat(),
                    normalized_weekday,
                    normalized_time,
                    normalized_interval,
                    created_at,
                ),
            )
            await self._db.commit()
            return int(cursor.lastrowid or 0)
        except Exception as e:
            logger.error(f"新增提醒失败: {e}")
            return 0

    async def list_reminders(self, chat_id: str, include_inactive: bool = False) -> list[dict]:
        try:
            if include_inactive:
                query = (
                    "SELECT id, chat_id, content, recurrence_type, next_trigger_at, "
                    "weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes "
                    "FROM reminders WHERE chat_id = ? "
                    "ORDER BY active DESC, next_trigger_at ASC, id ASC"
                )
            else:
                query = (
                    "SELECT id, chat_id, content, recurrence_type, next_trigger_at, "
                    "weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes "
                    "FROM reminders WHERE chat_id = ? AND active = 1 "
                    "ORDER BY next_trigger_at ASC, id ASC"
                )

            async with self._db.execute(query, (chat_id,)) as cursor:
                rows = [row async for row in cursor]
            return [self._row_to_reminder(row) for row in rows]
        except Exception as e:
            logger.error(f"列出提醒失败: {e}")
            return []

    async def cancel_reminder(self, chat_id: str, reminder_id: int) -> bool:
        try:
            cancelled_at = datetime.now(timezone.utc).isoformat()
            cursor = await self._db.execute(
                """UPDATE reminders
                   SET active = 0, cancelled_at = ?
                   WHERE chat_id = ? AND id = ? AND active = 1""",
                (cancelled_at, chat_id, reminder_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"取消提醒失败: {e}")
            return False

    async def get_due_reminders(
        self,
        chat_id: str,
        now: datetime,
        grace_seconds: int,
        limit: int = 20,
    ) -> list[dict]:
        if limit <= 0:
            return []

        try:
            now_utc = self._ensure_utc_datetime(now)
            grace = max(int(grace_seconds), 0)
            oldest_allowed = now_utc - timedelta(seconds=grace)
            scan_limit = max(limit * 4, limit)

            async with self._db.execute(
                """SELECT id, chat_id, content, recurrence_type, next_trigger_at,
                          weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes
                   FROM reminders
                   WHERE chat_id = ? AND active = 1 AND next_trigger_at <= ?
                   ORDER BY next_trigger_at ASC, id ASC
                   LIMIT ?""",
                (chat_id, now_utc.isoformat(), scan_limit),
            ) as cursor:
                rows = [row async for row in cursor]

            due: list[dict] = []
            for row in rows:
                reminder = self._row_to_reminder(row)
                next_dt = self._ensure_utc_datetime(reminder["next_trigger_at"])
                if next_dt < oldest_allowed:
                    await self._drop_or_roll_forward_stale(reminder, now_utc)
                    continue
                due.append(reminder)
                if len(due) >= limit:
                    break
            return due
        except Exception as e:
            logger.error(f"获取到期提醒失败: {e}")
            return []

    async def complete_or_reschedule_reminder(self, reminder_id: int, now: datetime) -> bool:
        try:
            now_utc = self._ensure_utc_datetime(now)
            async with self._db.execute(
                """SELECT id, chat_id, content, recurrence_type, next_trigger_at,
                          weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes
                   FROM reminders
                   WHERE id = ? AND active = 1""",
                (reminder_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return False

            reminder = self._row_to_reminder(row)
            recurrence = reminder["recurrence_type"]
            if recurrence == "once":
                cursor = await self._db.execute(
                    """UPDATE reminders
                       SET active = 0, last_triggered_at = ?
                       WHERE id = ? AND active = 1""",
                    (now_utc.isoformat(), reminder_id),
                )
                await self._db.commit()
                return cursor.rowcount > 0

            current_next = self._ensure_utc_datetime(reminder["next_trigger_at"])
            next_trigger = self._advance_to_future(
                current_next, recurrence, now_utc,
                interval_minutes=reminder.get("interval_minutes"),
            )
            cursor = await self._db.execute(
                """UPDATE reminders
                   SET last_triggered_at = ?, next_trigger_at = ?
                   WHERE id = ? AND active = 1""",
                (now_utc.isoformat(), next_trigger.isoformat(), reminder_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"完成/重排提醒失败: {e}")
            return False

    async def _drop_or_roll_forward_stale(self, reminder: dict, now_utc: datetime) -> None:
        recurrence = str(reminder.get("recurrence_type", "once"))
        reminder_id = int(reminder["id"])
        if recurrence == "once":
            await self._db.execute(
                """UPDATE reminders
                   SET active = 0, cancelled_at = ?
                   WHERE id = ? AND active = 1""",
                (now_utc.isoformat(), reminder_id),
            )
            await self._db.commit()
            return

        current_next = self._ensure_utc_datetime(reminder["next_trigger_at"])
        next_trigger = self._advance_to_future(
            current_next, recurrence, now_utc,
            interval_minutes=reminder.get("interval_minutes"),
        )
        await self._db.execute(
            "UPDATE reminders SET next_trigger_at = ? WHERE id = ? AND active = 1",
            (next_trigger.isoformat(), reminder_id),
        )
        await self._db.commit()

    def _advance_to_future(
        self,
        start: datetime,
        recurrence_type: str,
        now_utc: datetime,
        interval_minutes: int | None = None,
    ) -> datetime:
        recurrence = str(recurrence_type).lower()
        if recurrence == "daily":
            step = timedelta(days=1)
        elif recurrence == "weekly":
            step = timedelta(days=7)
        elif recurrence == "interval" and interval_minutes:
            step = timedelta(minutes=interval_minutes)
        else:
            return start

        next_dt = start
        for _ in range(0, 4096):
            if next_dt > now_utc:
                return next_dt
            next_dt = next_dt + step
        return next_dt

    def _ensure_utc_datetime(self, value: datetime | str) -> datetime:
        if isinstance(value, str):
            text = value.strip()
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = value

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _normalize_weekday(self, raw_value) -> int | None:
        if raw_value in (None, "", "null"):
            return None
        try:
            weekday = int(raw_value)
        except (TypeError, ValueError):
            return None
        return weekday if 0 <= weekday <= 6 else None

    def _normalize_time_of_day(self, raw_value) -> str | None:
        if raw_value in (None, "", "null"):
            return None
        time_text = str(raw_value).strip()
        if not _TIME_OF_DAY_PATTERN.match(time_text):
            return None
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError:
            return None
        return time_text

    async def get_reminder_by_id(self, reminder_id: int) -> dict | None:
        if self._db is None:
            return None
        try:
            async with self._db.execute(
                """SELECT id, chat_id, content, recurrence_type, next_trigger_at,
                          weekday, time_of_day, active, created_at,
                          last_triggered_at, cancelled_at, interval_minutes
                   FROM reminders WHERE id = ? AND active = 1""",
                (reminder_id,),
            ) as cursor:
                row = await cursor.fetchone()
            return self._row_to_reminder(row) if row else None
        except Exception as exc:
            logger.error("get_reminder_by_id(%d) 失败: %s", reminder_id, exc)
            return None

    def _row_to_reminder(self, row) -> dict:
        result = {
            "id": row[0],
            "chat_id": row[1],
            "content": row[2],
            "recurrence_type": row[3],
            "next_trigger_at": row[4],
            "weekday": row[5],
            "time_of_day": row[6],
            "active": bool(row[7]),
            "created_at": row[8],
            "last_triggered_at": row[9],
            "cancelled_at": row[10],
        }
        if len(row) > 11:
            result["interval_minutes"] = row[11]
        return result
