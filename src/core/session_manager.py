"""Session 管理器 — 为每个 chat_id 维护多个话题窗口。

Phase 1：仅时间间隔检测，Active / Dormant / Deleted 三级生命周期。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from datetime import datetime, timezone

from src.core.time_utils import parse_iso_datetime

from config.settings import (
    SESSION_DORMANT_TTL_HOURS,
    SESSION_MAX_DORMANT_PER_CHAT,
    SESSION_MIN_MESSAGES_TO_KEEP,
    SESSION_TIMEOUT_MINUTES,
)

logger = logging.getLogger("lapwing.core.session_manager")


@dataclasses.dataclass
class Session:
    id: str
    chat_id: str
    status: str  # "active" | "dormant" | "condensed" | "deleted"
    topic_summary: str
    topic_keywords: list[str]
    snapshot_path: str | None
    created_at: datetime
    last_active_at: datetime
    condensed_at: datetime | None
    message_count: int
    # Session Lineage（压缩后的会话谱系）
    parent_session_id: str | None = None
    lineage_root_id: str | None = None
    compression_summary: str | None = None




def _row_to_session(row) -> Session:
    return Session(
        id=row[0],
        chat_id=row[1],
        status=row[2],
        topic_summary=row[3],
        topic_keywords=json.loads(row[4] or "[]"),
        snapshot_path=row[5],
        created_at=datetime.fromisoformat(row[6]),
        last_active_at=datetime.fromisoformat(row[7]),
        condensed_at=parse_iso_datetime(row[8]),
        message_count=row[9],
        parent_session_id=row[10] if len(row) > 10 else None,
        lineage_root_id=row[11] if len(row) > 11 else None,
        compression_summary=row[12] if len(row) > 12 else None,
    )


class SessionManager:
    """管理 chat_id 下的多个话题 session。"""

    def __init__(self, memory, db) -> None:
        self._memory = memory        # ConversationMemory 实例
        self._db = db                # aiosqlite.Connection（共享 memory 的连接）
        self._sessions_cache: dict[str, Session] = {}  # key = session.id

    async def init(self) -> None:
        """从 DB 加载所有 active + dormant session 到内存缓存。"""
        try:
            async with self._db.execute(
                "SELECT id, chat_id, status, topic_summary, topic_keywords, snapshot_path, "
                "created_at, last_active_at, condensed_at, message_count, "
                "parent_session_id, lineage_root_id, compression_summary "
                "FROM sessions WHERE status IN ('active', 'dormant')"
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                s = _row_to_session(row)
                self._sessions_cache[s.id] = s
            logger.info(f"SessionManager 已加载 {len(rows)} 个 session")
        except Exception as e:
            logger.error(f"SessionManager 初始化失败: {e}")

    # ── 核心方法 ────────────────────────────────────────────────────────────

    async def resolve_session(self, chat_id: str, user_message: str) -> Session:
        """为本次消息确定应使用的 session。

        Phase 1 逻辑：仅时间间隔检测。
        """
        active = self._get_active(chat_id)

        if active is None:
            return await self.create_session(chat_id)

        # 检查超时
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - active.last_active_at).total_seconds() / 60
        if elapsed_minutes >= SESSION_TIMEOUT_MINUTES:
            logger.debug(
                f"[{chat_id}] Session {active.id} 超时 ({elapsed_minutes:.1f}min)，创建新 session"
            )
            await self.deactivate(active)
            return await self.create_session(chat_id)

        # 更新 last_active_at 和 message_count
        active.last_active_at = now
        active.message_count += 1
        await self._update_last_active(active.id, now, active.message_count)
        return active

    async def get_or_create_active(self, chat_id: str) -> Session:
        """获取当前 active session；没有则创建（用于 heartbeat 主动消息）。"""
        active = self._get_active(chat_id)
        if active is not None:
            return active
        return await self.create_session(chat_id)

    async def create_session(self, chat_id: str, topic_summary: str = "") -> Session:
        """创建新 active session，现有 active 自动降级为 dormant。"""
        existing_active = self._get_active(chat_id)
        if existing_active is not None:
            await self.deactivate(existing_active)

        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            chat_id=chat_id,
            status="active",
            topic_summary=topic_summary,
            topic_keywords=[],
            snapshot_path=None,
            created_at=now,
            last_active_at=now,
            condensed_at=None,
            message_count=0,
        )

        await self._insert_session(session)
        self._sessions_cache[session_id] = session
        logger.debug(f"[{chat_id}] 新 session 创建: {session_id}")
        return session

    async def deactivate(self, session: Session) -> None:
        """Active → Dormant（或消息太少时直接 Deleted）。"""
        if session.message_count < SESSION_MIN_MESSAGES_TO_KEEP:
            logger.debug(
                f"[{session.chat_id}] Session {session.id} 仅 {session.message_count} 条消息，直接删除"
            )
            await self.delete_session(session)
            return

        await self._update_session_status(session.id, status="dormant")
        session.status = "dormant"
        logger.debug(f"[{session.chat_id}] Session {session.id} → dormant")

        # 总量控制
        await self._enforce_max_dormant(session.chat_id)

    async def delete_session(self, session: Session) -> None:
        """标记 session 为 deleted，清除内存缓存。"""
        await self._update_session_status(session.id, status="deleted")
        session.status = "deleted"
        await self._memory.clear_session_cache(session.id)
        self._sessions_cache.pop(session.id, None)
        logger.debug(f"[{session.chat_id}] Session {session.id} → deleted")

    async def reap_expired(self, chat_id: str) -> tuple[int, int]:
        """清理过期 sessions。Phase 1：dormant → deleted（无 condensed）。

        返回 (condensed_count, deleted_count)。
        """
        now = datetime.now(timezone.utc)
        deleted_count = 0

        dormant_sessions = [
            s for s in self._sessions_cache.values()
            if s.chat_id == chat_id and s.status == "dormant"
        ]

        for s in dormant_sessions:
            elapsed_hours = (now - s.last_active_at).total_seconds() / 3600
            if elapsed_hours >= SESSION_DORMANT_TTL_HOURS:
                await self.delete_session(s)
                deleted_count += 1

        return 0, deleted_count

    async def split_on_compression(self, old_session_id: str, summary: str) -> str:
        """压缩后创建新 session，建立谱系链接。

        旧 session → condensed（保留压缩摘要）。
        新 session → active（继承 lineage_root_id）。

        Returns:
            新 session 的 id。
        """
        old = self._sessions_cache.get(old_session_id)
        if not old:
            return old_session_id

        # 标记旧 session 为 condensed
        old.status = "condensed"
        old.condensed_at = datetime.now(timezone.utc)
        old.compression_summary = summary
        try:
            await self._db.execute(
                "UPDATE sessions SET status = 'condensed', condensed_at = ?, compression_summary = ? "
                "WHERE id = ?",
                (old.condensed_at.isoformat(), summary, old_session_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error("更新 condensed session 失败: %s", e)

        # 创建新 session，建立谱系
        new_session = await self.create_session(old.chat_id)
        new_session.parent_session_id = old_session_id
        new_session.lineage_root_id = old.lineage_root_id or old_session_id
        try:
            await self._db.execute(
                "UPDATE sessions SET parent_session_id = ?, lineage_root_id = ? WHERE id = ?",
                (new_session.parent_session_id, new_session.lineage_root_id, new_session.id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error("设置 session 谱系失败: %s", e)

        logger.debug(
            "[%s] Session lineage: %s → condensed, new %s (root: %s)",
            old.chat_id, old_session_id, new_session.id,
            new_session.lineage_root_id,
        )
        return new_session.id

    async def get_lineage(self, session_id: str) -> list[Session]:
        """追溯完整谱系链（从 root 到当前）。"""
        chain: list[Session] = []
        current_id: str | None = session_id

        # 先向上追溯到 root
        visited: set[str] = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            session = self._sessions_cache.get(current_id)
            if session is None:
                # 从 DB 加载（可能是 condensed session，不在缓存中）
                try:
                    async with self._db.execute(
                        "SELECT id, chat_id, status, topic_summary, topic_keywords, snapshot_path, "
                        "created_at, last_active_at, condensed_at, message_count, "
                        "parent_session_id, lineage_root_id, compression_summary "
                        "FROM sessions WHERE id = ?",
                        (current_id,),
                    ) as cursor:
                        row = await cursor.fetchone()
                    if row:
                        session = _row_to_session(row)
                    else:
                        break
                except Exception:
                    break
            chain.append(session)
            current_id = session.parent_session_id

        chain.reverse()  # root 在前
        return chain

    def list_sessions(self, chat_id: str, status: str | None = None) -> list[Session]:
        """列出指定 chat_id 的 session（从内存缓存）。"""
        sessions = [s for s in self._sessions_cache.values() if s.chat_id == chat_id]
        if status is not None:
            sessions = [s for s in sessions if s.status == status]
        return sessions

    # ── 内部辅助方法 ────────────────────────────────────────────────────────

    def _get_active(self, chat_id: str) -> Session | None:
        """从缓存中找到 chat_id 的 active session。"""
        for s in self._sessions_cache.values():
            if s.chat_id == chat_id and s.status == "active":
                return s
        return None

    async def _insert_session(self, session: Session) -> None:
        try:
            await self._db.execute(
                "INSERT INTO sessions (id, chat_id, status, topic_summary, topic_keywords, "
                "snapshot_path, created_at, last_active_at, condensed_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.chat_id,
                    session.status,
                    session.topic_summary,
                    json.dumps(session.topic_keywords, ensure_ascii=False),
                    session.snapshot_path,
                    session.created_at.isoformat(),
                    session.last_active_at.isoformat(),
                    session.condensed_at.isoformat() if session.condensed_at else None,
                    session.message_count,
                ),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"Session 写入 DB 失败: {e}")

    async def _update_session_status(self, session_id: str, *, status: str) -> None:
        try:
            await self._db.execute(
                "UPDATE sessions SET status = ? WHERE id = ?",
                (status, session_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"Session 状态更新失败: {e}")

    async def _update_last_active(
        self, session_id: str, last_active_at: datetime, message_count: int
    ) -> None:
        try:
            await self._db.execute(
                "UPDATE sessions SET last_active_at = ?, message_count = ? WHERE id = ?",
                (last_active_at.isoformat(), message_count, session_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"Session last_active_at 更新失败: {e}")

    async def _enforce_max_dormant(self, chat_id: str) -> None:
        """确保 dormant session 总数不超过 SESSION_MAX_DORMANT_PER_CHAT。"""
        dormant = [
            s for s in self._sessions_cache.values()
            if s.chat_id == chat_id and s.status == "dormant"
        ]
        dormant.sort(key=lambda s: s.last_active_at)

        while len(dormant) > SESSION_MAX_DORMANT_PER_CHAT:
            oldest = dormant.pop(0)
            await self.delete_session(oldest)
            logger.debug(
                f"[{chat_id}] 超出 dormant 上限，删除最老 session {oldest.id}"
            )
