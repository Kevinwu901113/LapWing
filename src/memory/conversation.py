"""对话记忆 facade（内存缓存 + 可选的 TrajectoryStore 镜像）。

After the 2026-04-19 MVP cleanup, the legacy SQLite tables
(``user_facts``, ``interest_topics``, ``discoveries``, ``todos``,
``reminders``) that ConversationMemory used to own have all been
dropped — their responsibilities were absorbed by:

  * ``SemanticStore`` (``data/memory/semantic/``) — persistent facts
    about kevin / lapwing / world replace ``user_facts`` +
    ``interest_topics`` + ``discoveries``.
  * ``DurableScheduler`` (``reminders_v2`` table) — replaces both
    ``todos`` and the legacy ``reminders`` table.

ConversationMemory keeps only:

  * An in-memory cache used by phase-0 and unit-test code paths that
    have no ``TrajectoryStore`` wired.
  * A thin ``append()`` that mirrors writes to ``TrajectoryStore`` when
    one is injected via ``set_trajectory()``.

The class retains its full public surface (``init_db``, ``get``,
``append``, ``replace_history``, ``remove_last``, ``clear``,
``clear_chat_all``, ``clear_all``, ``close``) because several tests and
bootstrap paths depend on it; most are now no-ops or in-memory-only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.core.trajectory_store import TrajectoryStore

logger = logging.getLogger("lapwing.memory.conversation")


class ConversationMemory:
    """In-memory conversation cache + optional trajectory mirror."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._store: dict[str, list[dict]] = {}
        self._trajectory: "TrajectoryStore | None" = None

    async def init_db(self) -> None:
        """Ensure the data directory exists. Table creation now lives in
        the individual stores (TrajectoryStore / CommitmentStore /
        DurableScheduler), so there is nothing to DDL here."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("ConversationMemory ready (db_path=%s)", self._db_path)

    async def get(self, channel_id: str) -> list[dict]:
        """Return the in-memory cache for a channel (empty list if unseen)."""
        if channel_id not in self._store:
            self._store[channel_id] = []
        return self._store[channel_id]

    def set_trajectory(self, trajectory: "TrajectoryStore | None") -> None:
        """Inject the durable trajectory store. Safe to call multiple times."""
        self._trajectory = trajectory

    async def append(
        self,
        channel_id: str,
        role: str,
        content: str,
        *,
        channel: str = "qq",
        source: str = "qq",
        trust_level: int = 3,  # kept for caller compatibility; unused
        actor_id: str | None = None,
        is_inner: bool = False,
    ) -> None:
        """Record a message. When trajectory is wired, mirrors to it;
        otherwise updates the in-memory cache (phase-0 / unit-test path)."""
        if self._trajectory is not None:
            await self._mirror_to_trajectory(
                channel_id,
                role,
                content,
                channel=channel,
                source=source,
                actor_id=actor_id,
                is_inner=is_inner,
            )
            return
        if channel_id not in self._store:
            self._store[channel_id] = []
        self._store[channel_id].append({"role": role, "content": content})

    def replace_history(self, channel_id: str, new_history: list[dict]) -> None:
        """Overwrite the in-memory cache for a channel (phase-0 / tests)."""
        self._store[channel_id] = new_history

    async def remove_last(self, channel_id: str) -> None:
        """Pop the last cached message when trajectory is not wired.

        When trajectory is wired the trajectory is append-only (every
        LLM turn, including failures, is already recorded in
        ``mutation_log``), so this becomes a no-op. The method stays
        in place for caller/test compatibility.
        """
        if self._trajectory is None:
            history = self._store.get(channel_id, [])
            if history:
                history.pop()

    async def clear(self, channel_id: str) -> None:
        """Reset the in-process cache for a channel."""
        self._store.pop(channel_id, None)

    async def clear_chat_all(self, channel_id: str) -> None:
        """Reset the in-process cache. The durable state lives in
        TrajectoryStore (append-only by contract) and DurableScheduler
        (reminders_v2); wiping those requires a dedicated migration."""
        self._store.pop(channel_id, None)

    async def clear_all(self) -> None:
        """Reset every channel's in-process cache."""
        self._store.clear()

    async def close(self) -> None:
        """Compatibility no-op — ConversationMemory no longer owns a DB connection."""
        return None

    async def _mirror_to_trajectory(
        self,
        chat_id: str,
        role: str,
        content: str,
        *,
        channel: str,
        source: str,
        actor_id: str | None,
        is_inner: bool = False,
    ) -> None:
        if self._trajectory is None:
            return
        try:
            from src.core.trajectory_store import TrajectoryEntryType

            if is_inner:
                entry_type = TrajectoryEntryType.INNER_THOUGHT
                source_chat_id = None
                if role == "assistant":
                    actor = "lapwing"
                elif role == "user":
                    actor = "system"
                else:
                    logger.warning(
                        "trajectory mirror skipped — unknown inner-thought role %r",
                        role,
                    )
                    return
                payload: dict = {"text": content, "trigger_type": "live_dual_write"}
            elif role == "user":
                entry_type = TrajectoryEntryType.USER_MESSAGE
                source_chat_id = chat_id
                actor = "user"
                payload = {"text": content, "adapter": channel, "source": source}
            elif role == "assistant":
                entry_type = TrajectoryEntryType.ASSISTANT_TEXT
                source_chat_id = chat_id
                actor = "lapwing"
                payload = {"text": content, "adapter": channel, "source": source}
            else:
                logger.warning(
                    "trajectory mirror skipped — unknown role %r for chat %s",
                    role,
                    chat_id,
                )
                return

            if actor_id:
                payload["user_id"] = actor_id

            await self._trajectory.append(
                entry_type,
                source_chat_id,
                actor,
                payload,
            )
        except Exception:
            logger.warning(
                "trajectory mirror write failed for chat %s (role=%s)",
                chat_id,
                role,
                exc_info=True,
            )
