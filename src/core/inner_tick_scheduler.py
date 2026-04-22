"""InnerTickScheduler — drives self-initiated thought pulses.

Blueprint v2.0 Step 4 §M3.a. Replaces ``ConsciousnessEngine``'s built-in
``_loop`` (which both decided "when to think next" and ran the thinking
itself). With Step 4's MainLoop in charge, scheduling and dispatch
become two separate concerns:

  * **InnerTickScheduler** owns *when* to wake up — backoff on idle,
    [NEXT: Xm] hints from the LLM, urgency-driven early fires, pause
    while the user is actively conversing.
  * **MainLoop._handle_inner_tick** owns *what to do* on each wake — it
    pulls the urgency-queue contents, calls ``brain.think_inner()``, and
    reports the result back via ``note_tick_result``.

This split mirrors the queue-vs-consumer separation we already use for
adapters: the scheduler is just another producer that puts
``InnerTickEvent`` instances on the shared ``EventQueue``.

The scheduler does **not** keep a reference to the brain — the only
thing it knows how to do is "fire a tick" by enqueuing an event. That
keeps the interrupt path clean: cancelling a tick is MainLoop's
responsibility (it cancels the in-flight handler task), and the
scheduler only sees the eventual ``note_tick_result`` callback.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from config.settings import (
    CONSCIOUSNESS_AFTER_CHAT_INTERVAL,
    CONSCIOUSNESS_DEFAULT_INTERVAL,
    CONSCIOUSNESS_MAX_INTERVAL,
    CONSCIOUSNESS_MIN_INTERVAL,
    DATA_DIR,
)
from src.core.events import InnerTickEvent

if TYPE_CHECKING:  # pragma: no cover
    from src.core.event_queue import EventQueue

logger = logging.getLogger("lapwing.core.inner_tick_scheduler")


_NEXT_PATTERN = re.compile(r"\[T?NEXT:\s*(\d+)\s*(s|m|h|min)\]", re.IGNORECASE)

_INNER_NO_OP_RESPONSES = frozenset({"无事", "无事。", "无事，", "nothing"})


def is_inner_did_nothing(text: str) -> bool:
    """True when ``text`` is the canonical "no action this tick" response."""
    return text.strip() in _INNER_NO_OP_RESPONSES


def build_inner_prompt(urgent_items: list[dict] | None = None) -> str:
    """Construct the synthetic "user" prompt for one inner tick.

    Migrated from ``consciousness.py._build_consciousness_prompt`` so the
    prompt-shape lives next to the scheduler that fires the tick. The
    structure is unchanged from Phase 4: free-time framing → optional
    urgency block → working-memory recap → reflection prompts → rules
    (tool usage, [NEXT: Xm] suffix). MainLoop's tick handler hands this
    string to ``brain.think_inner`` exactly as the consciousness loop
    used to hand it to ``brain.think``.
    """
    from pathlib import Path
    from src.core.time_utils import now as _now

    now = _now().strftime("%Y-%m-%d %H:%M %A")
    parts = [
        f"[内部意识 tick — {now}]",
        "",
        "这是你的自由时间。你可以做任何你觉得应该做的事，或者什么都不做。",
        "没有人在等你回复。你不需要跟任何人说话，除非你自己想。",
        "",
        "【重要】这不是用户对话。没有人刚才跟你说了什么话。",
        "不要说「你能再说一次吗」「抱歉走神了」「你好」之类的话——没有人在跟你说话。",
        "如果没有需要做的事，回复\"无事\"即可。",
        "",
    ]

    if urgent_items:
        parts.append("## ⚡ 紧急事件（请优先处理）\n")
        for item in urgent_items:
            event_type = item.get("type", "unknown")
            content = item.get("content", "")
            parts.append(f"- [{event_type}] {content}")
        parts.append("")
        parts.append("以上事件需要你立即响应，请先处理完再做其他事。")
        parts.append("")

    scratch_pad_path = DATA_DIR / "consciousness" / "scratch_pad.md"
    if scratch_pad_path.exists():
        try:
            text = scratch_pad_path.read_text(encoding="utf-8").strip()
            if text:
                parts.append("## 你上次在做的事\n")
                parts.append(text[:2000])
                parts.append("")
        except Exception:
            pass

    parts.append("## 你可以想想这些方面\n")
    parts.append("- 刚才跟他聊的有没有什么你说错的或者可以做得更好的")
    parts.append("- 你手头有没有没做完的事")
    parts.append("- 他最近有没有什么需要你提前准备的")
    parts.append("- 你有没有什么想看的想学的")
    parts.append("- 你的身体（系统）状态正常吗")
    parts.append("- 有没有什么能力上的不足是你可以尝试解决的")
    parts.append("- 你想不想主动找他说点什么")
    parts.append("- 你学会了哪些新技能？有没有还不够稳定需要再练习的？")
    parts.append("- 你可以想象 Kevin 可能会让你做什么，提前自己模拟练习")
    parts.append("- 你有没有可以写成技能的重复性操作？")
    parts.append("")
    parts.append("## 规则\n")
    parts.append("- 你可以使用任何工具来做你想做的事")
    parts.append("- 如果你做了什么，用 write_note 记录下来")
    parts.append("- 如果你想找他说话，调用 send_message 工具")
    parts.append(f"- 如果你想在工作记忆中记录进度，用 write_file 写到 {DATA_DIR / 'consciousness' / 'scratch_pad.md'}")
    parts.append("- 什么都不想做也完全可以，回复\"无事\"即可")
    parts.append("- 在回复的最后一行，写上你希望多久后再被叫醒，格式：[NEXT: 数字m] 或 [NEXT: 数字h]")
    parts.append("  例如 [NEXT: 10m] 表示 10 分钟后，[NEXT: 2h] 表示 2 小时后")
    parts.append("  如果你觉得现在该休息了，可以写 [NEXT: 6h] 之类的长间隔")

    return "\n".join(parts)


def parse_next_interval(text: str) -> tuple[str, int | None]:
    """Extract ``[NEXT: Xm]`` / ``[TNEXT: Xs]`` suffix from ``text``.

    Returns ``(text_with_marker_stripped, seconds_or_None)``. Migrated
    from ``consciousness.py._parse_and_strip_next`` so the parser lives
    next to the scheduler that consumes it.
    """
    match = _NEXT_PATTERN.search(text)
    if not match:
        return text, None
    value = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"s": 1, "m": 60, "min": 60, "h": 3600}
    seconds = value * multiplier.get(unit, 60)
    return _NEXT_PATTERN.sub("", text).strip(), seconds


class InnerTickScheduler:
    """Owns the cadence of inner ticks; produces ``InnerTickEvent``."""

    BASE_INTERVAL = CONSCIOUSNESS_DEFAULT_INTERVAL
    MIN_INTERVAL = CONSCIOUSNESS_MIN_INTERVAL
    MAX_INTERVAL = CONSCIOUSNESS_MAX_INTERVAL
    BACKOFF_FACTOR = 1.5

    def __init__(self, event_queue: "EventQueue") -> None:
        self._queue = event_queue
        self._task: asyncio.Task | None = None
        self._alive = False

        # Scheduling state
        self._next_interval: int = self.BASE_INTERVAL
        self._idle_streak: int = 0
        self._last_conversation_end: float = 0.0
        self._in_conversation: bool = False
        self._conversation_event = asyncio.Event()
        self._conversation_event.set()

        # Urgency: items added here cause the scheduler to fire ASAP.
        # MainLoop's tick handler drains this on each fire.
        self._urgency_queue: asyncio.Queue = asyncio.Queue()
        self._wake_event = asyncio.Event()

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._alive = True
        self._task = asyncio.create_task(self._run(), name="inner-tick-scheduler")
        logger.info("InnerTickScheduler 已启动 — 初始间隔 %ds", self._next_interval)

    async def stop(self) -> None:
        self._alive = False
        self._wake_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("InnerTickScheduler 已停止")

    # ── Public API ───────────────────────────────────────────────────

    def push_urgency(self, item: dict) -> None:
        """Drop an item that should fire a tick ASAP.

        ``item`` shape: ``{"type": "reminder"|"agent_done"|"system",
        "content": str, ...}``. MainLoop drains the queue before each
        tick and forwards the contents to ``brain.think_inner``.
        """
        self._urgency_queue.put_nowait(item)
        self._wake_event.set()

    def drain_urgency(self) -> list[dict]:
        items: list[dict] = []
        while not self._urgency_queue.empty():
            try:
                items.append(self._urgency_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def note_conversation_start(self) -> None:
        """User started talking — pause inner ticks until they finish."""
        self._in_conversation = True
        self._conversation_event.clear()

    def note_conversation_end(self) -> None:
        """User finished — resume with the post-chat interval (short)."""
        self._in_conversation = False
        self._last_conversation_end = time.time()
        self._next_interval = CONSCIOUSNESS_AFTER_CHAT_INTERVAL
        self._conversation_event.set()
        self._wake_event.set()

    def note_tick_result(
        self,
        *,
        did_something: bool,
        llm_next_interval: int | None,
    ) -> None:
        """Adjust the next-interval after a tick handler finishes.

        ``llm_next_interval`` (parsed from ``[NEXT: Xm]``) wins over
        backoff. If the LLM didn't suggest one, fall back to the silence-
        based default (post-chat / idle decay).
        """
        if llm_next_interval is not None:
            self._next_interval = max(
                self.MIN_INTERVAL, min(self.MAX_INTERVAL, int(llm_next_interval)),
            )
            self._idle_streak = 0 if did_something else self._idle_streak + 1
            return

        if did_something:
            self._idle_streak = 0
            if self._next_interval > self.BASE_INTERVAL:
                self._next_interval = self.BASE_INTERVAL
            else:
                self._next_interval = self._silence_based_interval()
        else:
            self._idle_streak += 1
            backoff = self.BASE_INTERVAL * (self.BACKOFF_FACTOR ** self._idle_streak)
            self._next_interval = min(int(backoff), self.MAX_INTERVAL)
            logger.debug(
                "Inner-tick idle backoff: streak=%d next=%ds",
                self._idle_streak, self._next_interval,
            )

    def note_tick_failed(self) -> None:
        """Tick handler raised — back off by 2x to avoid hot-looping."""
        backoff = min(self._next_interval * 2, self.MAX_INTERVAL)
        self._next_interval = max(self.MIN_INTERVAL, backoff)

    # ── Inspection (used by tests + observability) ───────────────────

    @property
    def next_interval_seconds(self) -> int:
        return self._next_interval

    @property
    def idle_streak(self) -> int:
        return self._idle_streak

    # ── Main loop ────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._alive:
            try:
                # Wait until either: configured interval elapses, OR an
                # explicit wake (push_urgency / note_conversation_end).
                # If urgency arrived while we were dispatching the previous
                # tick, _wake_event is already set and wait_for returns
                # without sleeping — that's the desired behaviour.
                if not self._wake_event.is_set():
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(),
                            timeout=self._next_interval,
                        )
                    except asyncio.TimeoutError:
                        pass
                self._wake_event.clear()

                if not self._alive:
                    break

                # Pause ticks while a conversation is active.
                if self._in_conversation:
                    await self._conversation_event.wait()
                    if not self._alive:
                        break

                reason = "urgency" if not self._urgency_queue.empty() else "periodic"
                await self._queue.put(InnerTickEvent.make(reason=reason))

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("InnerTickScheduler loop crashed; backing off 30s")
                await asyncio.sleep(30)

    def _silence_based_interval(self) -> int:
        if self._last_conversation_end <= 0:
            return self.BASE_INTERVAL
        silence = time.time() - self._last_conversation_end
        if silence > 7200:      # 2h+ → 1h
            return 3600
        if silence > 1800:      # 30m+ → 30m
            return 1800
        return self.BASE_INTERVAL
