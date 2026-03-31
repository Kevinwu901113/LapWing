"""TodoAgent - 管理待办事项与提醒任务。"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.agents.todo")

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_OF_DAY_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_VALID_REMINDER_RECURRENCES = {"once", "daily", "weekly"}


class TodoAgent(BaseAgent):
    name = "todo"
    description = "管理本地待办事项和提醒，支持添加、查看、完成、删除、取消"
    capabilities = ["添加待办", "列出待办", "完成待办", "删除待办", "添加提醒", "查看提醒", "取消提醒"]

    def __init__(self, memory) -> None:
        self._memory = memory

    async def execute(self, task: AgentTask, router) -> AgentResult:
        command = await self._parse_command(task.chat_id, task.user_message, router)
        action = str(command.get("action") or "").strip().lower()
        domain = str(command.get("domain") or "").strip().lower()

        if self._is_reminder_command(command, domain, action):
            return await self._handle_reminder(task.chat_id, command, action)

        if action == "add":
            return await self._handle_add(task.chat_id, command)
        if action == "list":
            return await self._handle_list(task.chat_id)
        if action == "done":
            return await self._handle_done(task.chat_id, command)
        if action == "delete":
            return await self._handle_delete(task.chat_id, command)

        message = command.get("reason") or "我这次没看懂待办操作。"
        return AgentResult(content=message, needs_persona_formatting=False)

    async def _parse_command(self, chat_id: str, user_message: str, router) -> dict:
        now_local = datetime.now().astimezone()
        today = now_local.strftime("%Y-%m-%d")
        tz_name = now_local.tzname() or "UTC"
        prompt = (
            load_prompt("agent_todo")
            .replace("{today}", today)
            .replace("{timezone}", tz_name)
            .replace("{user_message}", user_message)
        )
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=320,
                session_key=f"chat:{chat_id}",
                origin="agent.todo.parse",
            )
        except Exception as exc:
            logger.warning(f"[todo] 解析待办命令失败: {exc}")
            return {"action": "error", "reason": "解析待办/提醒操作时出了点问题，请稍后再试。"}

        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"action": "error", "reason": "我这次没看懂待办/提醒操作，你可以再说得明确一点。"}

        if not isinstance(data, dict):
            return {"action": "error", "reason": "我这次没看懂待办/提醒操作，你可以再说得明确一点。"}
        return data

    async def _handle_add(self, chat_id: str, command: dict) -> AgentResult:
        content = str(command.get("content") or "").strip()
        due_date = self._normalize_due_date(command.get("due_date"))

        if not content:
            return AgentResult(
                content="要添加待办的话，请把具体内容也告诉我。",
                needs_persona_formatting=False,
            )
        if command.get("due_date") and due_date is None:
            return AgentResult(
                content="我没能识别截止日期，请换成更明确的日期再试一次。",
                needs_persona_formatting=False,
            )

        todo_id = await self._memory.add_todo(chat_id, content, due_date)
        if not todo_id:
            return AgentResult(
                content="待办保存失败了，请稍后再试。",
                needs_persona_formatting=False,
            )

        suffix = f"（截止 {due_date}）" if due_date else ""
        return AgentResult(
            content=f"已添加待办 #{todo_id}：{content}{suffix}",
            needs_persona_formatting=True,
        )

    async def _handle_list(self, chat_id: str) -> AgentResult:
        todos = await self._memory.list_todos(chat_id)
        if not todos:
            return AgentResult(content="当前没有待办事项。", needs_persona_formatting=False)

        lines = ["当前待办："]
        for todo in todos:
            status = "[x]" if todo["done"] else "[ ]"
            due = f"（截止 {todo['due_date']}）" if todo.get("due_date") else ""
            lines.append(f"- {status} #{todo['id']} {todo['content']}{due}")
        return AgentResult(content="\n".join(lines), needs_persona_formatting=False)

    async def _handle_done(self, chat_id: str, command: dict) -> AgentResult:
        todo_id = self._parse_id(command.get("todo_id"))
        if todo_id is None:
            return AgentResult(content="请告诉我要完成哪一条待办。", needs_persona_formatting=False)

        success = await self._memory.mark_todo_done(chat_id, todo_id)
        if not success:
            return AgentResult(content="没有这条待办。", needs_persona_formatting=False)
        return AgentResult(content=f"已完成待办 #{todo_id}。", needs_persona_formatting=True)

    async def _handle_delete(self, chat_id: str, command: dict) -> AgentResult:
        todo_id = self._parse_id(command.get("todo_id"))
        if todo_id is None:
            return AgentResult(content="请告诉我要删除哪一条待办。", needs_persona_formatting=False)

        success = await self._memory.delete_todo(chat_id, todo_id)
        if not success:
            return AgentResult(content="没有这条待办。", needs_persona_formatting=False)
        return AgentResult(content=f"已删除待办 #{todo_id}。", needs_persona_formatting=True)

    async def _handle_reminder(self, chat_id: str, command: dict, action: str) -> AgentResult:
        normalized_action = action
        if normalized_action in {"reminder_add", "add"}:
            return await self._handle_add_reminder(chat_id, command)
        if normalized_action in {"reminder_list", "list"}:
            return await self._handle_list_reminders(chat_id)
        if normalized_action in {"reminder_cancel", "cancel", "delete"}:
            return await self._handle_cancel_reminder(chat_id, command)

        message = command.get("reason") or "我这次没看懂提醒操作。"
        return AgentResult(content=message, needs_persona_formatting=False)

    async def _handle_add_reminder(self, chat_id: str, command: dict) -> AgentResult:
        content = str(command.get("content") or "").strip()
        if not content:
            return AgentResult(content="要添加提醒的话，请告诉我提醒内容。", needs_persona_formatting=False)

        recurrence = self._normalize_recurrence(
            command.get("recurrence_type") or command.get("recurrence")
        )
        if recurrence is None:
            return AgentResult(content="提醒周期格式不对，请用一次性、每天或每周。", needs_persona_formatting=False)

        local_tz = self._local_timezone()
        now_local = datetime.now(local_tz)

        weekday = self._normalize_weekday(command.get("weekday"))
        time_of_day = self._normalize_time_of_day(command.get("time_of_day"))
        trigger_local = self._normalize_trigger_at(command.get("trigger_at"), local_tz)

        if recurrence == "once":
            if trigger_local is None:
                return AgentResult(
                    content="一次性提醒需要明确的触发时间（YYYY-MM-DD HH:MM）。",
                    needs_persona_formatting=False,
                )
            if trigger_local <= now_local:
                return AgentResult(content="提醒时间需要晚于现在。", needs_persona_formatting=False)
            next_local = trigger_local
            weekday = None
            time_of_day = None
        else:
            if time_of_day is None and trigger_local is not None:
                time_of_day = trigger_local.strftime("%H:%M")
            if time_of_day is None:
                return AgentResult(content="周期提醒需要具体时间（HH:MM）。", needs_persona_formatting=False)

            if recurrence == "weekly":
                if weekday is None and trigger_local is not None:
                    weekday = trigger_local.weekday()
                if weekday is None:
                    return AgentResult(content="每周提醒需要指定星期几（0-6）。", needs_persona_formatting=False)

            next_local = self._next_recurrence_local(
                now_local=now_local,
                recurrence=recurrence,
                time_of_day=time_of_day,
                weekday=weekday,
            )

        next_utc = next_local.astimezone(timezone.utc)
        reminder_id = await self._memory.add_reminder(
            chat_id=chat_id,
            content=content,
            recurrence_type=recurrence,
            next_trigger_at=next_utc,
            weekday=weekday,
            time_of_day=time_of_day,
        )
        if not reminder_id:
            return AgentResult(content="提醒保存失败了，请稍后再试。", needs_persona_formatting=False)

        recurrence_text = self._format_recurrence_label(recurrence, weekday, time_of_day)
        return AgentResult(
            content=(
                f"已添加提醒 #{reminder_id}：{content}"
                f"（{recurrence_text}，下次触发 {next_local.strftime('%Y-%m-%d %H:%M')}）"
            ),
            needs_persona_formatting=True,
        )

    async def _handle_list_reminders(self, chat_id: str) -> AgentResult:
        reminders = await self._memory.list_reminders(chat_id)
        if not reminders:
            return AgentResult(content="当前没有提醒任务。", needs_persona_formatting=False)

        local_tz = self._local_timezone()
        lines = ["当前提醒："]
        for item in reminders:
            recurrence_text = self._format_recurrence_label(
                str(item.get("recurrence_type") or "once"),
                item.get("weekday"),
                item.get("time_of_day"),
            )
            next_local = self._format_utc_to_local(item.get("next_trigger_at"), local_tz)
            lines.append(
                f"- #{item['id']} {item['content']}（{recurrence_text}，下次 {next_local}）"
            )
        return AgentResult(content="\n".join(lines), needs_persona_formatting=False)

    async def _handle_cancel_reminder(self, chat_id: str, command: dict) -> AgentResult:
        reminder_id = self._parse_id(command.get("reminder_id") or command.get("todo_id"))
        if reminder_id is None:
            return AgentResult(content="请告诉我要取消哪一条提醒。", needs_persona_formatting=False)

        success = await self._memory.cancel_reminder(chat_id, reminder_id)
        if not success:
            return AgentResult(content="没有这条提醒。", needs_persona_formatting=False)
        return AgentResult(content=f"已取消提醒 #{reminder_id}。", needs_persona_formatting=True)

    def _is_reminder_command(self, command: dict, domain: str, action: str) -> bool:
        if domain == "reminder":
            return True
        if action in {"reminder_add", "reminder_list", "reminder_cancel", "cancel"}:
            return True
        if command.get("reminder_id") not in (None, "", "null"):
            return True
        if command.get("recurrence_type") or command.get("recurrence"):
            return True
        if command.get("trigger_at") or command.get("time_of_day"):
            return True
        return False

    def _parse_id(self, raw_value) -> int | None:
        try:
            item_id = int(raw_value)
        except (TypeError, ValueError):
            return None
        return item_id if item_id > 0 else None

    def _normalize_due_date(self, raw_value) -> str | None:
        if raw_value in (None, "", "null"):
            return None

        due_date = str(raw_value).strip()
        if not _DATE_PATTERN.match(due_date):
            return None

        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            return None
        return due_date

    def _normalize_recurrence(self, raw_value) -> str | None:
        value = str(raw_value or "once").strip().lower()
        return value if value in _VALID_REMINDER_RECURRENCES else None

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

    def _normalize_trigger_at(self, raw_value, local_tz) -> datetime | None:
        if raw_value in (None, "", "null"):
            return None

        text = str(raw_value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None

        if parsed is None:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue

        if parsed is None:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=local_tz)
        else:
            parsed = parsed.astimezone(local_tz)
        return parsed

    def _next_recurrence_local(
        self,
        now_local: datetime,
        recurrence: str,
        time_of_day: str,
        weekday: int | None,
    ) -> datetime:
        hour, minute = map(int, time_of_day.split(":"))

        if recurrence == "daily":
            candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now_local:
                candidate = candidate + timedelta(days=1)
            return candidate

        if recurrence == "weekly":
            target_weekday = weekday if weekday is not None else now_local.weekday()
            day_offset = (target_weekday - now_local.weekday()) % 7
            candidate = (now_local + timedelta(days=day_offset)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now_local:
                candidate = candidate + timedelta(days=7)
            return candidate

        return now_local

    def _format_recurrence_label(self, recurrence: str, weekday: int | None, time_of_day: str | None) -> str:
        if recurrence == "daily":
            return f"每天 {time_of_day or '--:--'}"
        if recurrence == "weekly":
            weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            if weekday is None or not (0 <= int(weekday) <= 6):
                weekday_text = "每周"
            else:
                weekday_text = weekday_names[int(weekday)]
            return f"{weekday_text} {time_of_day or '--:--'}"
        return "一次性"

    def _format_utc_to_local(self, value: str | None, local_tz) -> str:
        if not value:
            return "未知"
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)

    def _local_timezone(self):
        tz = datetime.now().astimezone().tzinfo
        return tz or timezone.utc
