"""Framework-level "send to user" that mirrors tell_user's recording.

The MVP invariant "``tell_user`` is the LLM's sole exit" covers LLM-mediated
speech: every byte the model produces for the user must flow through the
``tell_user`` tool. There is a second, legitimate class of user-visible
output that the framework emits on the LLM's behalf — not the model
speaking:

  * Confirmations resolved by ``TaskRuntime.resolve_pending_confirmation``
    (e.g. "OK, I'll use that directory"). The model has already committed
    to the action; the framework just needs to acknowledge the confirm.
  * Error surfacing when the LLM call itself throws. The model can't be
    asked to say "LLM call failed" — it's the one that failed.
  * Reminder fires from ``DurableScheduler`` in ``notify`` mode. The user
    chose "notify me, don't ask the LLM" — so this is literally a timer
    echoing the reminder text.
  * ``DurableScheduler`` ``agent``-mode fallback when the agent run raises
    or when the final summary couldn't be delivered through ``tell_user``
    (silent send_fn used inside ``think_conversational``).

These are not LLM speech; they shouldn't be shoehorned through the
``tell_user`` tool. But they *are* user-visible output, and they must
appear in ``trajectory_store`` and ``mutation_log`` so the audit trail is
complete. This helper is the unified recording path — exact same shape as
``tell_user_executor`` in ``src/tools/tell_user.py``, just invoked by
framework paths instead of by the LLM.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from src.logging.state_mutation_log import (
    MutationType,
    current_iteration_id,
)
from src.core.expression_gate import get_default_expression_gate, source_from_legacy

logger = logging.getLogger("lapwing.core.system_send")

SendFn = Callable[[str], Awaitable[Any]]


async def send_system_message(
    send_fn: SendFn,
    text: str,
    *,
    source: str,
    chat_id: str | None = None,
    adapter: str | None = None,
    trajectory_store: Any = None,
    mutation_log: Any = None,
    focus_id: str | None = None,
    expression_gate: Any = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Deliver a framework-level message to the user and record it.

    Parameters
    ----------
    send_fn
        The channel-bound coroutine that actually puts bytes on the wire.
    text
        Message body.
    source
        Where this message originated. Used as the ``source`` tag in both
        trajectory and mutation-log payloads so audits can distinguish
        framework sends from LLM-mediated ``tell_user`` tool calls
        (which omit this field / use ``"llm"``). Callers should pass one
        of: ``"confirmation"``, ``"llm_error"``, ``"reminder_notify"``,
        ``"reminder_agent_result"``, ``"reminder_agent_fallback"``.
    chat_id / adapter
        Contextual metadata, recorded alongside the message. May be
        ``None`` for scheduler fires where no chat is bound.
    trajectory_store / mutation_log
        Optional stores to record into. Failures are logged and
        swallowed — the live delivery is the priority; recording
        is best-effort.

    Returns
    -------
    bool
        ``True`` if ``send_fn`` completed without raising,
        ``False`` otherwise. Recording failures do not change the
        return value.
    """
    gate = expression_gate or get_default_expression_gate()
    try:
        from src.config import get_settings
        gate_enabled = bool(get_settings().expression_gate.enabled)
    except Exception:
        gate_enabled = True
    if gate_enabled:
        delivered = await gate.send(
            text,
            source=source_from_legacy(source),
            chat_id=chat_id,
            send_fn=send_fn,
            trajectory_store=trajectory_store,
            mutation_log=mutation_log,
            adapter=adapter,
            focus_id=focus_id,
            metadata={"legacy_source": source, **(metadata or {})},
        )
        if not delivered:
            logger.warning(
                "system_send %s 投递失败 chat=%s",
                source,
                chat_id,
            )
        return delivered

    delivered = False
    try:
        await send_fn(text)
        delivered = True
    except Exception:
        logger.warning(
            "system_send %s 投递失败 chat=%s",
            source, chat_id, exc_info=True,
        )
        # Fall through to still record the attempt.

    trajectory_id: int | None = None
    if trajectory_store is not None:
        try:
            from src.core.trajectory_store import TrajectoryEntryType

            trajectory_id = await trajectory_store.append(
                TrajectoryEntryType.TELL_USER,
                chat_id,
                "system",
                {
                    "text": text,
                    "source": source,
                    "delivered": delivered,
                },
                related_iteration_id=current_iteration_id(),
                focus_id=focus_id,
            )
        except Exception:
            logger.warning("system_send trajectory append 失败", exc_info=True)

    if mutation_log is not None:
        try:
            await mutation_log.record(
                MutationType.TELL_USER,
                {
                    "text": text,
                    "chat_id": chat_id,
                    "adapter": adapter,
                    "source": source,
                    "delivered": delivered,
                    "trajectory_id": trajectory_id,
                },
                iteration_id=current_iteration_id(),
                chat_id=chat_id,
            )
        except Exception:
            logger.warning("system_send mutation_log mirror 失败", exc_info=True)

    return delivered
