"""
业务事件日志系统。

双输出：SQLite 主存储 + 人类可读文本文件。
所有业务事件通过 EventLogger.log() 写入，不直接用 Python logging。

使用方式：
    from src.logging.event_logger import events

    events.log("conversation", "outgoing", message="明天早上9点10分", channel="qq")
    events.log("tool_call", "execute", tool="web_search", args={"query": "..."}, duration=1.2, success=True)
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("lapwing.event_logger")

# ── 类别配置 ──

CATEGORIES = {
    "conversation": {"file": "conversation.log", "max_bytes": 2 * 1024 * 1024, "retain_days": 7,  "style": "multi"},
    "tool_call":    {"file": "tool_call.log",    "max_bytes": 3 * 1024 * 1024, "retain_days": 5,  "style": "single"},
    "llm_call":     {"file": "llm_call.log",     "max_bytes": 3 * 1024 * 1024, "retain_days": 5,  "style": "single"},
    "thinking":     {"file": "thinking.log",     "max_bytes": 2 * 1024 * 1024, "retain_days": 3,  "style": "multi"},
    "consciousness":{"file": "consciousness.log","max_bytes": 2 * 1024 * 1024, "retain_days": 7,  "style": "multi"},
    "memory":       {"file": "memory.log",       "max_bytes": 1 * 1024 * 1024, "retain_days": 14, "style": "single"},
    "evolution":    {"file": "evolution.log",     "max_bytes": 1 * 1024 * 1024, "retain_days": 30, "style": "multi"},
    "system":       {"file": "system.log",        "max_bytes": 2 * 1024 * 1024, "retain_days": 7,  "style": "single"},
    "debug":        {"file": "debug.log",         "max_bytes": 1 * 1024 * 1024, "retain_days": 3,  "style": "single"},
    "tool_loop":    {"file": "tool_loop.log",    "max_bytes": 2 * 1024 * 1024, "retain_days": 5,  "style": "single"},
}


class EventLogger:
    """业务事件日志的唯一写入口。线程安全。"""

    def __init__(self, log_dir: str = "logs", db_path: str = "data/events.db"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._initialized = False

    def _ensure_init(self):
        """延迟初始化：首次写入时才打开 DB 和文件。"""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._init_db()
            self._initialized = True

    def _init_db(self):
        """初始化 SQLite 表。"""
        self._db = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_category_time
            ON event_log(category, created_at)
        """)
        self._db.commit()

    # ── 主写入方法 ──

    def log(self, category: str, event_type: str, **data):
        """
        记录一条业务事件。

        Args:
            category: 事件类别（conversation / tool_call / llm_call 等）
            event_type: 事件子类型（如 "incoming" / "outgoing" / "execute" 等）
            **data: 事件数据，任意键值对
        """
        if category not in CATEGORIES:
            logger.warning("未知事件类别: %s", category)
            return

        self._ensure_init()

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        created_at = time.time()

        # 写 SQLite
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO event_log (timestamp, category, event_type, data, created_at) VALUES (?, ?, ?, ?, ?)",
                    (timestamp, category, event_type, json.dumps(data, ensure_ascii=False, default=str), created_at),
                )
                self._db.commit()
        except Exception as e:
            logger.error("事件写入 SQLite 失败: %s", e)

        # 写文本文件
        try:
            config = CATEGORIES[category]
            text = self._format_text(category, event_type, timestamp, data, config["style"])
            file_path = self._log_dir / config["file"]

            with open(file_path, "a", encoding="utf-8") as f:
                f.write(text)

            # 检查文件大小，超限则截断
            self._check_truncate(file_path, config["max_bytes"])

        except Exception as e:
            logger.error("事件写入文本文件失败: %s", e)

    # ── 文本格式化 ──

    def _format_text(self, category: str, event_type: str, timestamp: str, data: dict, style: str) -> str:
        """根据类别格式化文本输出。"""
        ts = timestamp[5:]  # 去掉年份，只保留 MM-DD HH:MM:SS
        if style == "single":
            return self._format_single_line(category, event_type, ts, data)
        return self._format_multi_line(category, event_type, ts, data)

    def _format_single_line(self, category: str, event_type: str, ts: str, data: dict) -> str:
        """单行格式。"""
        icon = _ICONS.get(category, "•")

        if category == "tool_call":
            tool = data.get("tool", "?")
            duration = data.get("duration", "?")
            success = "✓" if data.get("success") else "✗"
            args_summary = _truncate(json.dumps(data.get("args", {}), ensure_ascii=False), 80)
            return f"[{ts}] {icon} {tool} | {args_summary} | {duration}s | {success}\n"

        if category == "llm_call":
            slot = data.get("slot", "?")
            model = data.get("model", "?")
            input_tokens = data.get("input_tokens", "?")
            output_tokens = data.get("output_tokens", "?")
            duration = data.get("duration", "?")
            purpose = data.get("purpose", "?")
            return f"[{ts}] {icon} {slot}/{model} | in:{input_tokens} out:{output_tokens} | {duration}s | {purpose}\n"

        if category == "memory":
            action = data.get("action", event_type)
            content = _truncate(data.get("content", ""), 100)
            return f"[{ts}] {icon} {action} | {content}\n"

        if category == "system":
            message = data.get("message", event_type)
            return f"[{ts}] {icon} {message}\n"

        if category == "debug":
            module = data.get("module", "?")
            message = data.get("message", "")
            return f"[{ts}] {icon} [{module}] {_truncate(message, 120)}\n"

        # 通用单行
        summary = _truncate(json.dumps(data, ensure_ascii=False), 100)
        return f"[{ts}] {icon} {event_type} | {summary}\n"

    def _format_multi_line(self, category: str, event_type: str, ts: str, data: dict) -> str:
        """多行格式。"""
        icon = _ICONS.get(category, "•")
        lines = [f"── {ts} ── {icon} {event_type} ──"]

        if category == "conversation":
            direction = data.get("direction", "?")
            channel = data.get("channel", "?")
            message = data.get("message", "")
            if direction == "outgoing":
                lines.append(f"  → [{channel}] {message}")
            elif direction == "incoming":
                lines.append(f"  ← [{channel}] {message}")
            else:
                lines.append(f"  {message}")

        elif category == "thinking":
            content = data.get("content", "")
            trigger = data.get("trigger", "")
            if trigger:
                lines.append(f"  触发: {trigger}")
            for line in content.split("\n")[:20]:
                lines.append(f"  {line}")
            if content.count("\n") > 20:
                lines.append(f"  ...（共 {content.count(chr(10)) + 1} 行）")

        elif category == "consciousness":
            decision = data.get("decision", "")
            next_interval = data.get("next_interval", "?")
            actions = data.get("actions", [])
            if decision:
                lines.append(f"  决定: {decision}")
            if actions:
                for a in actions[:5]:
                    lines.append(f"  - {a}")
            lines.append(f"  下次间隔: {next_interval}s")

        elif category == "evolution":
            change_type = data.get("change_type", event_type)
            diff = data.get("diff", "")
            file = data.get("file", "")
            lines.append(f"  文件: {file}")
            lines.append(f"  类型: {change_type}")
            if diff:
                for line in diff.split("\n")[:15]:
                    lines.append(f"  {line}")

        else:
            for key, value in data.items():
                lines.append(f"  {key}: {_truncate(str(value), 200)}")

        lines.append("")  # 空行分隔
        return "\n".join(lines) + "\n"

    # ── 文件大小管理 ──

    def _check_truncate(self, file_path: Path, max_bytes: int):
        """文件超限时截断保留后半部分。"""
        try:
            size = file_path.stat().st_size
            if size <= max_bytes:
                return

            keep_bytes = max_bytes * 2 // 3
            content = file_path.read_text(encoding="utf-8")
            cut_point = len(content) - keep_bytes
            newline_pos = content.find("\n", cut_point)
            if newline_pos == -1:
                newline_pos = cut_point

            truncated = "[...日志已截断，保留最近内容...]\n" + content[newline_pos + 1:]
            file_path.write_text(truncated, encoding="utf-8")

        except Exception as e:
            logger.debug("日志截断失败 %s: %s", file_path, e)

    # ── SQLite 清理 ──

    def cleanup_old_events(self):
        """清理过期的 SQLite 事件记录。由维护任务调用。"""
        if not self._initialized or self._db is None:
            return

        try:
            with self._lock:
                for category, config in CATEGORIES.items():
                    retain_days = config["retain_days"]
                    cutoff = time.time() - (retain_days * 86400)
                    self._db.execute(
                        "DELETE FROM event_log WHERE category = ? AND created_at < ?",
                        (category, cutoff),
                    )
                self._db.commit()
            logger.info("事件日志清理完成")
        except Exception as e:
            logger.error("事件日志清理失败: %s", e)

    # ── 查询方法（供 API 使用）──

    def query(
        self,
        category: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        查询事件记录。供桌面端 API 使用。

        Args:
            category: 按类别过滤
            event_type: 按事件子类型过滤
            since: 起始时间戳
            until: 截止时间戳
            limit: 最大返回条数

        Returns:
            事件列表，每条包含 id, timestamp, category, event_type, data
        """
        if not self._initialized:
            self._ensure_init()

        conditions = []
        params: list[Any] = []

        if category:
            conditions.append("category = ?")
            params.append(category)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        if until:
            conditions.append("created_at <= ?")
            params.append(until)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        try:
            cursor = self._db.execute(
                f"SELECT id, timestamp, category, event_type, data FROM event_log WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "category": r[2],
                    "event_type": r[3],
                    "data": json.loads(r[4]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("事件查询失败: %s", e)
            return []

    def close(self):
        """关闭数据库连接。"""
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass


# ── 图标映射 ──

_ICONS = {
    "conversation": "💬",
    "tool_call": "🔧",
    "llm_call": "🧠",
    "thinking": "💭",
    "consciousness": "🌀",
    "memory": "📝",
    "evolution": "🧬",
    "system": "⚙️",
    "debug": "🔍",
}


def _truncate(text: str, max_len: int) -> str:
    """截断文本。"""
    from src.utils.text import truncate
    return truncate(text, max_len)


# ── 全局单例 ──

_instance: EventLogger | None = None


def get_event_logger() -> EventLogger:
    """获取全局 EventLogger 单例。"""
    global _instance
    if _instance is None:
        _instance = EventLogger()
    return _instance


# 便捷别名
events = get_event_logger()
