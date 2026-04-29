"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import asyncio
import dataclasses
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.auth.service import AuthManager
from src.core.llm_router import LLMRouter
from src.core.prompt_loader import load_prompt
from src.core.reasoning_tags import strip_internal_thinking_tags
from src.core.state_serializer import serialize as _serialize_state
from src.core.state_view import TrajectoryTurn
from src.core.state_view_builder import StateViewBuilder
from src.core.task_runtime import RuntimeDeps, RuntimeOptions, TaskRuntime
from src.core.trajectory_store import trajectory_entries_to_messages
from src.core.shell_policy import (
    ExecutionSessionState,
    build_shell_runtime_policy,
    extract_execution_constraints,
)
from src.core.verifier import verify_shell_constraints_status as verify_constraints
from src.core.trajectory_store import TrajectoryEntryType
from src.core.authority_gate import AuthLevel, identify as identify_auth
from src.logging.state_mutation_log import MutationType
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import execute as execute_shell
from src.tools.types import ToolExecutionRequest
from config.settings import (
    BROWSER_ENABLED,
    CHAT_WEB_TOOLS_ENABLED,
    FOCUS_ENABLED,
    INTENT_ROUTER_ENABLED,
    MAX_HISTORY_TURNS,
    SHELL_ALLOW_SUDO,
    SHELL_DEFAULT_CWD,
    SHELL_ENABLED,
    SOUL_PATH,
)

if TYPE_CHECKING:
    from src.memory.vector_store import VectorStore

logger = logging.getLogger("lapwing.core.brain")
# Dedicated metrics logger — bypasses lapwing.core.brain's WARNING-level
# noisy-module throttle in main.py:setup_logging. Keeps per-turn path /
# latency / response_length INFO records visible in lapwing.log.
_metrics_logger = logging.getLogger("lapwing.metrics.complete_chat")

# 直接输出模式：模型裸文本 = 用户可见消息。工具调用是内部操作。
# send_message 工具仅用于主动消息场景（意识 tick / 定时提醒等无对话上下文时）。

_TASK_PROFILE_HINTS = (
    "跑", "执行", "运行", "shell", "命令", "代码", "git", "pytest",
    "deploy", "部署", "当前目录", "列一下", "查看目录", "文件", "文件夹",
    "创建", "新建", "写入", "修改", "删除",
)


@dataclasses.dataclass
class _ThinkCtx:
    """think() / think_conversational() 共享前置逻辑的结果。"""
    messages: list[dict]
    effective_user_message: str
    approved_directory: str | None
    early_reply: str | None = None
    focus_id: str | None = None


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path, *, model_config=None):
        self.auth_manager = AuthManager()
        self._model_config = model_config
        self.router = LLMRouter(auth_manager=self.auth_manager, model_config=model_config)
        from config.settings import PHASE0_MODE
        if PHASE0_MODE:
            from src.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry()  # Phase 0: 空注册表
        else:
            self.tool_registry = build_default_tool_registry()
        self._db_path = db_path
        from config.settings import TASK_NO_ACTION_BUDGET, TASK_ERROR_BURST_THRESHOLD
        self.task_runtime = TaskRuntime(
            router=self.router,
            tool_registry=self.tool_registry,
            no_action_budget=TASK_NO_ACTION_BUDGET,
            error_burst_threshold=TASK_ERROR_BURST_THRESHOLD,
        )
        # v2.0 Step 3: StateViewBuilder is the sole prompt-assembly entry.
        # Default builder has no store wiring — every section but identity
        # docs collapses to empty. AppContainer replaces this at prepare()
        # with a fully wired instance. Replacing rather than mutating
        # keeps this attribute "live" throughout brain's lifetime so
        # render paths never have to guard against ``None``.
        self.state_view_builder: StateViewBuilder = StateViewBuilder()
        self.vector_store: VectorStore | None = None
        self.event_bus = None
        self._system_prompt: str | None = None
        self.reminder_scheduler = None  # Set externally (ReminderScheduler | None)
        self.channel_manager = None  # Set externally (ChannelManager | None)
        # Step 4 M7: ConsciousnessEngine retired. InnerTickScheduler owns
        # inner thinking; MaintenanceTimer owns periodic actions.
        self.inner_tick_scheduler = None  # Set externally — Step 4 M3
        self.attention_manager = None  # Set externally (AttentionManager | None) — v2.0 Step 2
        self.trajectory_store = None  # Set externally (TrajectoryStore | None) — v2.0 Step 2f
        self.focus_manager = None  # Set externally (FocusManager | None)
        self._conversation_end_task: asyncio.Task | None = None

    async def init_db(self) -> None:
        """Ensure the data directory exists.

        Table creation lives in the individual stores (TrajectoryStore /
        CommitmentStore / DurableScheduler).
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _record_turn(
        self,
        chat_id: str,
        role: str,
        content: str,
        *,
        is_inner: bool = False,
        focus_id: str | None = None,
    ) -> None:
        """Write a conversation turn directly to TrajectoryStore."""
        if self.trajectory_store is None:
            return
        try:
            if is_inner:
                entry_type = TrajectoryEntryType.INNER_THOUGHT
                source_chat_id = None
                actor = "lapwing" if role == "assistant" else "system"
                payload: dict = {"text": content, "trigger_type": "brain_direct"}
            elif role == "user":
                entry_type = TrajectoryEntryType.USER_MESSAGE
                source_chat_id = chat_id
                actor = "user"
                payload = {"text": content}
            elif role == "assistant":
                entry_type = TrajectoryEntryType.ASSISTANT_TEXT
                source_chat_id = chat_id
                actor = "lapwing"
                payload = {"text": content}
            else:
                return
            await self.trajectory_store.append(
                entry_type, source_chat_id, actor, payload, focus_id=focus_id,
            )
            if focus_id and self.focus_manager is not None:
                await self.focus_manager.accumulate(focus_id)
        except Exception:
            logger.warning(
                "trajectory write failed for chat %s (role=%s)",
                chat_id, role, exc_info=True,
            )

    async def _load_history(
        self, chat_id: str, focus_id: str | None = None,
    ) -> list[dict]:
        """Legacy-shape conversation history for the LLM context.

        Reads from ``TrajectoryStore.relevant_to_chat`` and projects via
        ``trajectory_entries_to_messages``. Returns empty when the store
        isn't wired (unit tests, phase-0). ``include_inner=False``
        preserves the legacy semantics — consciousness-loop rows stay
        out of the user-facing exchange.
        """
        if self.trajectory_store is not None:
            if focus_id:
                rows = await self.trajectory_store.entries_by_focus(
                    focus_id, n=MAX_HISTORY_TURNS * 2,
                )
            else:
                rows = await self.trajectory_store.relevant_to_chat(
                    chat_id, n=MAX_HISTORY_TURNS * 2, include_inner=False,
                )
            return trajectory_entries_to_messages(rows)
        return []

    async def clear_short_term_memory(self, chat_id: str) -> None:
        """仅清除短期对话记忆。"""
        self.task_runtime.clear_chat_state(chat_id)

    async def clear_all_memory(self, chat_id: str) -> None:
        """清除指定 chat 的长短期记忆。"""
        self.task_runtime.clear_chat_state(chat_id)

        if self.vector_store is not None:
            try:
                await self.vector_store.delete_chat(chat_id)
            except Exception as exc:
                logger.warning(f"[{chat_id}] 清除向量记忆失败: {exc}")

    @property
    def system_prompt(self) -> str:
        """懒加载 system prompt（核心人格 soul）。优先从 data/identity/soul.md 加载。"""
        if self._system_prompt is None:
            from config.settings import PHASE0_MODE
            if PHASE0_MODE:
                from src.core.phase0 import build_phase0_prompt
                self._system_prompt = build_phase0_prompt()
                logger.info("Phase 0 模式：使用极简 prompt（soul_test + constitution_test + 时间）")
            elif SOUL_PATH.exists():
                self._system_prompt = SOUL_PATH.read_text(encoding="utf-8")
                logger.info(f"已从 {SOUL_PATH} 加载 Lapwing 人格 prompt")
            else:
                self._system_prompt = load_prompt("lapwing_soul")
                logger.info("已从 prompts/lapwing_soul.md 加载 Lapwing 人格 prompt（fallback）")
        return self._system_prompt

    @property
    def desktop_connected(self) -> bool:
        """Whether any desktop client is currently connected."""
        return getattr(self, "_desktop_connected", False)

    def reload_persona(self) -> None:
        """重新加载人格 prompt。"""
        from src.core.prompt_loader import reload_prompt, clear_cache
        clear_cache()
        if SOUL_PATH.exists():
            self._system_prompt = SOUL_PATH.read_text(encoding="utf-8")
        else:
            self._system_prompt = reload_prompt("lapwing_soul")
        reload_prompt("lapwing_voice")
        logger.info("已重新加载所有 prompt 缓存")

    def _chat_session_key(self, chat_id: str) -> str:
        return f"chat:{chat_id}"

    def list_model_options(self) -> list[dict[str, Any]]:
        return self.router.list_model_options()

    def model_status(self, chat_id: str) -> dict[str, Any]:
        return self.router.model_status(session_key=self._chat_session_key(chat_id))

    def switch_model(self, chat_id: str, selector: str) -> dict[str, Any]:
        return self.router.switch_session_model(
            session_key=self._chat_session_key(chat_id),
            selector=selector,
        )

    def reset_model(self, chat_id: str) -> dict[str, Any]:
        return self.router.clear_session_model(session_key=self._chat_session_key(chat_id))

    def _reset_session_budgets(self) -> None:
        """Reset per-session counters at session boundaries.

        Called at the entry of ``think_inner`` and ``think_conversational``
        so BrowserGuard's per-session action budget starts fresh on every
        new autonomous tick or user turn — otherwise the counter
        accumulates across independent sessions and the budget ceases
        to be a real cap. Best-effort: missing guard / errors don't
        block the turn.
        """
        bg = getattr(self.task_runtime, "_browser_guard", None)
        if bg is None:
            return
        try:
            bg.reset_budget()
        except Exception:
            logger.debug("BrowserGuard.reset_budget failed", exc_info=True)

    def _build_services(self) -> dict[str, Any]:
        """Construct the services dict passed into TaskRuntime.complete_chat.

        Centralised so both ``_complete_chat`` (regular user turns) and
        ``compose_proactive`` (autonomous outbound) see the same set of
        injected services. Every ref is read via ``getattr`` so a stub
        brain (test fixture, Phase 0) can omit any ref without breaking.
        """
        services: dict[str, Any] = {}
        if self.trajectory_store is not None:
            services["trajectory_store"] = self.trajectory_store
        if self.focus_manager is not None:
            services["focus_manager"] = self.focus_manager
        commitment_store = getattr(self, "_commitment_store_ref", None)
        if commitment_store is not None:
            services["commitment_store"] = commitment_store
        if self.reminder_scheduler is not None:
            services["reminder_scheduler"] = self.reminder_scheduler
        if self.channel_manager is not None:
            services["channel_manager"] = self.channel_manager
        agent_registry = getattr(self, "_agent_registry", None)
        if agent_registry is not None:
            services["agent_registry"] = agent_registry
        agent_policy = getattr(self, "_agent_policy", None)
        if agent_policy is not None:
            services["agent_policy"] = agent_policy
        dispatcher = getattr(self, "_dispatcher_ref", None)
        if dispatcher is not None:
            services["dispatcher"] = dispatcher
        mutation_log = getattr(self, "_mutation_log_ref", None)
        if mutation_log is not None:
            services["mutation_log"] = mutation_log
        services["router"] = self.router
        note_store = getattr(self, "_note_store", None)
        if note_store is not None:
            services["note_store"] = note_store
        memory_vector_store = getattr(self, "_memory_vector_store", None)
        if memory_vector_store is not None:
            services["vector_store"] = memory_vector_store
        durable_scheduler = getattr(self, "_durable_scheduler_ref", None)
        if durable_scheduler is not None:
            services["durable_scheduler"] = durable_scheduler
        from config.settings import QQ_KEVIN_ID
        if QQ_KEVIN_ID:
            services["owner_qq_id"] = QQ_KEVIN_ID
        browser_manager = getattr(self, "browser_manager", None)
        if browser_manager is not None:
            services["browser_manager"] = browser_manager
        vlm_client = getattr(self, "_vlm_client_ref", None)
        if vlm_client is not None:
            services["vlm"] = vlm_client
        research_engine = getattr(self, "_research_engine", None)
        if research_engine is not None:
            services["research_engine"] = research_engine
        skill_store = getattr(self, "_skill_store", None)
        if skill_store is not None:
            services["skill_store"] = skill_store
        skill_executor = getattr(self, "_skill_executor", None)
        if skill_executor is not None:
            services["skill_executor"] = skill_executor
        services["tool_registry"] = self.tool_registry
        ambient_store = getattr(self, "_ambient_store", None)
        if ambient_store is not None:
            services["ambient_store"] = ambient_store
        interest_profile = getattr(self, "_interest_profile", None)
        if interest_profile is not None:
            services["interest_profile"] = interest_profile
        circuit_breaker = getattr(self, "_circuit_breaker_ref", None)
        if circuit_breaker is not None:
            services["circuit_breaker"] = circuit_breaker
        correction_manager = getattr(self, "_correction_manager", None)
        if correction_manager is not None:
            services["correction_manager"] = correction_manager
        if getattr(self, "router", None) is not None:
            services["llm_router"] = self.router
        # Per-turn BudgetLedger (Blueprint §5) — fresh ledger every call so
        # Brain + delegated agents share the same caps across the turn but
        # new turns start with full budget.
        try:
            from src.agents.budget import BudgetLedger
            from src.config import get_settings
            bcfg = get_settings().budget
            services["budget_ledger"] = BudgetLedger(
                max_llm_calls=bcfg.max_llm_calls,
                max_tool_calls=bcfg.max_tool_calls,
                max_total_tokens=bcfg.max_total_tokens,
                max_wall_time_seconds=bcfg.max_wall_time_seconds,
                max_delegation_depth=bcfg.max_delegation_depth,
            )
        except Exception:
            logger.debug("BudgetLedger not initialised", exc_info=True)
        # ProactiveMessageGate — sent to send_message executor so proactive
        # paths (inner_tick, compose_proactive) can be rate-limited /
        # quiet-hours-gated. Direct chat replies use bare text and never
        # reach send_message, so this never throttles user-visible output.
        proactive_gate = getattr(self, "_proactive_message_gate_ref", None)
        if proactive_gate is not None:
            services["proactive_message_gate"] = proactive_gate
        return services

    async def _complete_chat(
        self,
        chat_id: str,
        messages: list[dict],
        user_message: str,
        approved_directory: str | None = None,
        status_callback=None,
        on_interim_text=None,
        on_typing=None,
        adapter: str = "",
        user_id: str = "",
        send_fn=None,
        focus_id: str | None = None,
        profile_override: str | None = None,
        runtime_options: RuntimeOptions | None = None,
    ) -> str:
        constraints = extract_execution_constraints(
            user_message,
            approved_directory=approved_directory,
        )
        services = self._build_services()
        if profile_override is not None:
            # Caller pinned a profile (e.g. think_inner uses "inner_tick").
            # Skip IntentRouter — the caller already knows the surface it
            # needs; routing again would either invalidate the contract or
            # silently widen tool exposure.
            profile_name = profile_override
        else:
            profile_name = self._fallback_profile_for_message(user_message, constraints)
            if INTENT_ROUTER_ENABLED:
                intent_router = getattr(self, "intent_router", None)
                if intent_router is not None and profile_name not in {"task_execution", "local_execution"}:
                    decision = await intent_router.route(chat_id, user_message)
                    profile_name = decision.profile_name

        # High-risk/operator profiles are opt-in only: never auto-route into them.
        operator_profiles = {
            "task_execution",
            "local_execution",
            "agent_admin_operator",
            "identity_operator",
            "browser_operator",
            "skill_operator",
        }
        if profile_name in operator_profiles:
            explicit_override = profile_override in operator_profiles
            owner_or_agent = self._local_execution_authorized(adapter=adapter, user_id=user_id)
            if not explicit_override or not owner_or_agent:
                ml = services.get("mutation_log")
                if ml is not None:
                    try:
                        await ml.record(
                            MutationType.TOOL_DENIED,
                            {
                                "tool": f"profile:{profile_name}",
                                "guard": "profile_escalation",
                                "reason": f"{profile_name}_requires_explicit_owner_or_agent",
                                "auth_level": int(identify_auth(adapter, user_id) if adapter else AuthLevel.OWNER),
                                "requested_profile": profile_name,
                                "explicit_override": explicit_override,
                            },
                        )
                    except Exception:
                        logger.debug("profile escalation deny audit failed", exc_info=True)
                profile_name = "standard"
            else:
                await self._record_profile_escalation(
                    services=services,
                    chat_id=chat_id,
                    adapter=adapter,
                    user_id=user_id,
                    profile_name=profile_name,
                )

        # Zero-tool fast path: pure-chat turns skip the OpenAI tool-call
        # protocol entirely. We still route through TaskRuntime.complete_chat
        # to keep ITERATION audit records aligned with the tool path —
        # TaskRuntime's `if not tools` branch dispatches directly to
        # router.complete(slot="main_conversation").
        zero_tools_path = (
            profile_override is None
            and profile_name == "zero_tools"
        )
        tools = [] if zero_tools_path else self.task_runtime.tools_for_profile(profile_name)

        deps = RuntimeDeps(
            execute_shell=execute_shell,
            policy=build_shell_runtime_policy(verify_constraints_fn=verify_constraints),
            shell_default_cwd=SHELL_DEFAULT_CWD,
            shell_allow_sudo=SHELL_ALLOW_SUDO,
        )

        t_start = time.monotonic()
        reply = await self.task_runtime.complete_chat(
            chat_id=chat_id,
            messages=messages,
            constraints=constraints,
            tools=tools,
            deps=deps,
            status_callback=status_callback,
            event_bus=self.event_bus,
            on_consent_required=lambda state: self.task_runtime.record_pending_confirmation(chat_id, state),
            services=services,
            profile=profile_name,
            on_interim_text=on_interim_text,
            on_typing=on_typing,
            adapter=adapter,
            user_id=user_id,
            send_fn=send_fn,
            focus_id=focus_id,
            runtime_options=runtime_options,
        )
        elapsed_ms = (time.monotonic() - t_start) * 1000
        _metrics_logger.info(
            "[brain.complete_chat] path=%s profile=%s latency_ms=%.0f response_length=%d",
            "zero_tools" if zero_tools_path else "tool_call",
            profile_name,
            elapsed_ms,
            len(reply),
        )
        return reply

    @staticmethod
    def _local_execution_authorized(*, adapter: str, user_id: str) -> bool:
        if adapter == "agent":
            return True
        level = identify_auth(adapter, user_id) if adapter else AuthLevel.OWNER
        return level >= AuthLevel.OWNER

    async def _record_profile_escalation(
        self,
        *,
        services: dict[str, Any],
        chat_id: str,
        adapter: str,
        user_id: str,
        profile_name: str,
    ) -> None:
        mutation_log = services.get("mutation_log")
        if mutation_log is None:
            return
        try:
            await mutation_log.record(
                MutationType.PROFILE_ESCALATED,
                {
                    "profile": profile_name,
                    "chat_id": chat_id,
                    "adapter": adapter,
                    "user_id": user_id,
                    "reason": f"explicit_{profile_name}_override",
                },
            )
        except Exception:
            logger.debug("profile escalation audit failed", exc_info=True)

    @staticmethod
    def _fallback_profile_for_message(user_message: str, constraints) -> str:
        # local_execution is operator-only. Default fallback stays on standard;
        # coding/file/shell user requests should route via delegate_to_coder.
        return "standard"

    async def _render_messages(
        self,
        chat_id: str,
        recent_messages: list[dict],
        *,
        adapter: str = "",
        user_id: str = "",
        auth_level: int = 3,
        group_id: str | None = None,
        inner: bool = False,
    ) -> list[dict]:
        """Assemble the full LLM messages list via StateSerializer.

        v2.0 Step 3 §3.1. ``recent_messages`` is the list brain already
        assembled (effective user-message swapped in, trust tagging
        applied, etc.); we carry it into StateView via the builder's
        ``trajectory_turns_override`` so the serializer renders exactly
        what the LLM needs without re-reading the trajectory.

        Returns the final ``[{system}, ...serialized, ...]`` list.
        Voice is part of the system prompt (stable prefix for caching).
        Caller layers image blocks on top using
        ``_inject_images_into_last_user_message``.
        """
        from config.settings import PHASE0_MODE

        # Phase 0: tests and minimal boots use the soul-only prompt and
        # skip the full StateView assembly entirely.
        if PHASE0_MODE:
            system_content = self.system_prompt
            return [{"role": "system", "content": system_content}, *recent_messages]

        # Convert already-processed dicts into TrajectoryTurn values. Only
        # string-content messages fit; multimodal image blocks get applied
        # later by ``_inject_images_into_last_user_message``.
        turns = tuple(
            TrajectoryTurn(role=str(m.get("role", "")), content=m["content"])
            for m in recent_messages
            if isinstance(m.get("content"), str)
        )

        if inner:
            state_view = await self.state_view_builder.build_for_inner(
                trajectory_turns_override=turns,
            )
        else:
            state_view = await self.state_view_builder.build_for_chat(
                chat_id,
                channel=adapter or "desktop",
                actor_id=user_id or None,
                actor_name=None,
                auth_level=auth_level,
                group_id=group_id,
                trajectory_turns_override=turns,
            )

        serialized = _serialize_state(state_view)
        system_content = serialized.system_prompt

        # Rebuild with any non-string (multimodal) entries preserved in
        # their original positions: the serializer dropped them when
        # building its output, so we splice them back by index.
        rendered = list(serialized.messages)
        for idx, m in enumerate(recent_messages):
            if isinstance(m.get("content"), list):
                # Multimodal block — insert at its original depth so
                # image alignment with text stays correct.
                rendered.insert(idx, dict(m))

        return [{"role": "system", "content": system_content}, *rendered]

    def _schedule_identity_shadow_retrieval(self, user_message: str) -> None:
        """Fire-and-forget shadow query against the identity retriever.

        Pure observation: writes a row to identity_retrieval_traces so we
        can study score distribution before plumbing claims into the
        prompt. Never affects the reply path; all errors swallowed.
        """
        retriever = getattr(self, "_identity_retriever", None)
        flags = getattr(self, "_identity_flags", None)
        if retriever is None:
            return
        if flags is not None and getattr(flags, "identity_system_killswitch", False):
            return
        query = (user_message or "").strip()[:200]
        if not query:
            return

        async def _shadow() -> None:
            try:
                from src.identity.auth import create_system_auth
                await retriever.retrieve(
                    query=query,
                    auth=create_system_auth(),
                    top_k=5,
                    min_confidence=0.0,
                )
            except Exception:
                logger.debug("identity shadow retrieval failed", exc_info=True)

        try:
            asyncio.create_task(_shadow())
        except RuntimeError:
            # No running loop (sync context) — safe to skip.
            pass

    def _schedule_conversation_end(self, chat_id: str | None = None) -> None:
        """延迟判定对话结束。用户最后一条消息后 N 秒无新消息算结束。

        Step 4 M6: closes the AttentionManager session window and
        notifies the InnerTickScheduler so inner ticks resume on the
        post-chat schedule.

        Focus owns content boundaries. Session end only marks the
        conversing→idle transition; episodic extraction runs when a focus
        becomes dormant.
        """
        if (
            self.inner_tick_scheduler is None
            and self.attention_manager is None
        ):
            return
        if self._conversation_end_task is not None:
            self._conversation_end_task.cancel()

        from config.settings import CONSCIOUSNESS_CONVERSATION_END_DELAY

        async def _delayed_end():
            await asyncio.sleep(CONSCIOUSNESS_CONVERSATION_END_DELAY)
            if self.inner_tick_scheduler is not None:
                self.inner_tick_scheduler.note_conversation_end()
            if self.attention_manager is not None:
                try:
                    await self.attention_manager.end_session()
                except Exception:
                    logger.warning("attention_manager.end_session failed", exc_info=True)

        self._conversation_end_task = asyncio.create_task(_delayed_end())

    def _recent_messages(
        self,
        history: list[dict],
        *,
        user_message: str,
        original_user_message: str,
    ) -> list[dict]:
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history
        recent_messages = [dict(item) for item in recent]
        if user_message != original_user_message:
            if recent_messages and recent_messages[-1].get("role") == "user":
                recent_messages[-1]["content"] = user_message
            else:
                recent_messages.append({"role": "user", "content": user_message})
        return recent_messages

    @staticmethod
    def _inject_images_into_last_user_message(
        messages: list[dict], images: list[dict]
    ) -> None:
        """将图片以 Anthropic content blocks 格式注入到最后一条 user 消息中。

        images 列表中每个 dict 支持两种格式：
          - {"base64": str, "media_type": str}  — base64 编码图片
          - {"url": str}                        — 图片 URL（直接传给 LLM）
        """
        # 找到最后一条 user 消息
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg["content"]
                # 将现有文本内容转为 content block 列表
                if isinstance(content, str):
                    blocks: list[dict] = []
                    if content.strip():
                        blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    blocks = list(content)
                else:
                    blocks = []

                # 追加图片 content blocks（Anthropic 格式）
                for img in images:
                    if "base64" in img:
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.get("media_type", "image/jpeg"),
                                "data": img["base64"],
                            },
                        })
                    elif "url" in img:
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": img["url"],
                            },
                        })

                msg["content"] = blocks
                break

    async def _prepare_think(
        self,
        chat_id: str,
        user_message: str,
        send_fn=None,
        images: list[dict] | None = None,
        adapter: str = "",
        user_id: str = "",
        auth_level: int = 3,
        group_id: str | None = None,
    ) -> "_ThinkCtx":
        """共享前置逻辑：记忆写入、trust tagging、context 组装。

        send_fn 非空时，immediate_reply / agent_reply 会通过它发送（用于 conversational 模式）。
        返回 _ThinkCtx；若 early_reply 非 None 则表示已完成回复，调用方直接返回该值即可。
        """
        focus_id: str | None = None
        if (
            FOCUS_ENABLED
            and self.focus_manager is not None
            and not chat_id.startswith("_")
        ):
            try:
                focus = await self.focus_manager.resolve_focus(chat_id, user_message)
                focus_id = focus.id
            except Exception:
                logger.warning("focus resolution failed for %s", chat_id, exc_info=True)

        # 存储文本到记忆（图片不持久化，只在当前 LLM 调用中传递）
        stored_text = user_message
        if images:
            img_tag = f"[用户发送了{len(images)}张图片]" if len(images) > 1 else "[用户发送了图片]"
            stored_text = f"{user_message}\n{img_tag}" if user_message.strip() else img_tag

        await self._record_turn(chat_id, "user", stored_text, focus_id=focus_id)

        # Shadow identity retrieval — observe-only, fire-and-forget.
        # Writes identity_retrieval_traces but does not affect prompt or reply.
        self._schedule_identity_shadow_retrieval(user_message)

        effective_user_message, approved_directory, immediate_reply = (
            self.task_runtime.resolve_pending_confirmation(chat_id, user_message)
        )
        if immediate_reply is not None:
            await self._record_turn(
                chat_id, "assistant", immediate_reply, focus_id=focus_id,
            )
            if send_fn is not None:
                from src.core.system_send import send_system_message
                await send_system_message(
                    send_fn,
                    immediate_reply,
                    source="confirmation",
                    chat_id=chat_id,
                    adapter=adapter,
                    trajectory_store=self.trajectory_store,
                    mutation_log=getattr(self, "_mutation_log_ref", None),
                    focus_id=focus_id,
                )
            return _ThinkCtx(messages=[], effective_user_message=effective_user_message,
                             approved_directory=approved_directory,
                             early_reply=immediate_reply, focus_id=focus_id)

        # 组装 messages
        history = await self._load_history(chat_id, focus_id=focus_id)
        recent_messages = self._recent_messages(
            history,
            user_message=effective_user_message,
            original_user_message=user_message,
        )

        # Trust tagging：在消息进入 LLM 上下文时包装（不改变 memory 中的存储）
        from src.core.trust_tagger import TrustTagger
        from src.core.vitals import now_local
        now_str = now_local().isoformat()

        if auth_level == 3 and adapter in ("qq", "desktop", ""):
            # OWNER — Kevin
            for msg in recent_messages:
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    msg["content"] = TrustTagger.tag_kevin(
                        msg["content"], source=adapter or "desktop", timestamp=now_str
                    )
        elif adapter == "qq_group":
            trust = "trusted" if auth_level >= 2 else "guest"
            for msg in recent_messages:
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    msg["content"] = TrustTagger.tag_group(
                        msg["content"], sender_id=user_id, sender_name="", trust=trust
                    )

        messages = await self._render_messages(
            chat_id,
            recent_messages,
            adapter=adapter,
            user_id=user_id,
            auth_level=auth_level,
            group_id=group_id,
        )

        # 多模态：将图片注入到最后一条 user 消息中（Anthropic content blocks 格式）
        if images:
            self._inject_images_into_last_user_message(messages, images)

        return _ThinkCtx(
            messages=messages,
            effective_user_message=effective_user_message,
            approved_directory=approved_directory,
            focus_id=focus_id,
        )

    async def think(self, chat_id: str, user_message: str, status_callback=None) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: 对话 ID
            user_message: 用户发送的消息

        Returns:
            Lapwing 的回复文本
        """
        ctx = await self._prepare_think(chat_id, user_message)
        if ctx.early_reply is not None:
            return ctx.early_reply

        start_time = time.monotonic()
        try:
            reply = await self._complete_chat(
                chat_id,
                ctx.messages,
                ctx.effective_user_message,
                approved_directory=ctx.approved_directory,
                status_callback=status_callback,
                focus_id=ctx.focus_id,
            )
            reply = strip_internal_thinking_tags(reply)

            try:
                await self._record_turn(
                    chat_id, "assistant", reply, focus_id=ctx.focus_id,
                )
                logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            except Exception as post_exc:
                logger.warning(
                    "[brain] 后处理失败（回复已生成，不影响调用方）: %s",
                    post_exc, exc_info=True,
                )
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 内部调用（意识循环等）不应返回面向用户的 fallback，直接抛出让调用方处理
            if chat_id.startswith("__"):
                raise
            return f"出错了：{e}"

    async def think_inner(
        self,
        *,
        urgent_items: list[dict] | None = None,
        timeout_seconds: int | None = None,
    ) -> tuple[str, int | None, bool]:
        """One self-initiated thinking pulse — no external user message.

        Step 4 M3 entry point. Builds the inner-tick prompt (urgency-
        block + working-memory + reflection prompts), runs the standard
        tool loop, writes both prompt and reply to trajectory as
        ``INNER_THOUGHT`` with ``source_chat_id = NULL`` (no
        ``__inner__`` sentinel), and parses ``[NEXT: Xm]`` from the
        reply so the scheduler can pick the next interval.

        ``urgent_items`` shape: ``[{"type": str, "content": str}, ...]``
        — drained by MainLoop from ``InnerTickScheduler.urgency_queue``.

        Returns ``(reply_text, llm_next_interval_seconds, did_something)``.
        ``did_something=False`` when the LLM returned the canonical
        "no action" string (so the scheduler can apply idle backoff).
        """
        from src.core.inner_tick_scheduler import (
            build_inner_prompt,
            is_inner_did_nothing,
            parse_next_interval,
        )
        from config.settings import (
            INNER_TICK_ERROR_BURST_THRESHOLD,
            INNER_TICK_MAX_TOOL_ROUNDS,
            INNER_TICK_NO_ACTION_BUDGET,
            INNER_TICK_TIMEOUT_SECONDS,
        )

        # Per-call budgets — tighter than chat surface so a tick yields
        # cleanly instead of burning rounds on maintenance work.
        if timeout_seconds is None:
            timeout_seconds = INNER_TICK_TIMEOUT_SECONDS
        inner_options = RuntimeOptions(
            max_tool_rounds=INNER_TICK_MAX_TOOL_ROUNDS,
            no_action_budget=INNER_TICK_NO_ACTION_BUDGET,
            error_burst_threshold=INNER_TICK_ERROR_BURST_THRESHOLD,
        )

        # Inner tick is a fresh autonomous session — reset BrowserGuard's
        # action budget so a previous tick (or user turn) cannot starve
        # this one. No-op when browser is disabled.
        self._reset_session_budgets()

        preparation_status: str | None = None
        prep_engine = getattr(self, "_preparation_engine", None)
        if prep_engine is not None:
            try:
                preparation_status = await prep_engine.format_for_prompt()
            except Exception:
                logger.debug("preparation_engine.format_for_prompt failed", exc_info=True)

        inner_prompt = build_inner_prompt(
            urgent_items,
            preparation_status=preparation_status,
        )

        # Internal session key for TaskRuntime / memory cache. Never
        # written as source_chat_id — trajectory rows go in with NULL.
        # The leading underscore keeps it cleanly distinct from real
        # adapter chat_ids without re-introducing the ``__inner__``
        # sentinel literal.
        session_key = "_inner_tick"

        await self._record_turn(
            session_key, "user", inner_prompt, is_inner=True,
        )

        # Inner rows land with source_chat_id=NULL, so _load_history
        # (include_inner=False) would return []. Build the recent list
        # directly from inner_prompt — past inner thoughts aren't replayed
        # into the message window; StateView surfaces runtime state.
        recent = [{"role": "user", "content": inner_prompt}]
        messages = await self._render_messages(
            session_key, recent, inner=True,
        )

        try:
            reply = await asyncio.wait_for(
                self._complete_chat(
                    session_key,
                    messages,
                    inner_prompt,
                    focus_id=None,
                    profile_override="inner_tick",
                    runtime_options=inner_options,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError:
            # Step 4 M4: OWNER preempt cancelled the inner tick.
            await self._persist_interrupted(
                chat_id=session_key,
                partial_text="",  # complete_chat is synchronous wrt streaming for inner ticks
                reason="owner_message_preempt",
                kind="inner",
            )
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "think_inner timed out after %ds — letting scheduler back off",
                timeout_seconds,
            )
            return "", None, False
        except Exception:
            logger.exception("think_inner LLM call failed")
            return "", None, False

        reply = strip_internal_thinking_tags(reply or "")

        try:
            await self._record_turn(
                session_key, "assistant", reply, is_inner=True,
            )
        except Exception:
            logger.warning(
                "think_inner reply persistence failed (reply already returned)",
                exc_info=True,
            )

        clean_text, next_interval = parse_next_interval(reply)
        did_something = bool(
            clean_text.strip()
            and not is_inner_did_nothing(clean_text)
        )

        return clean_text, next_interval, did_something

    async def think_conversational(
        self,
        chat_id: str,
        user_message: str,
        send_fn,
        typing_fn=None,
        status_callback=None,
        adapter: str = "",
        user_id: str = "",
        metadata: dict | None = None,
        images: list[dict] | None = None,
    ) -> str:
        """边查边说模式：中间文字通过 send_fn 实时发出。

        Args:
            chat_id: 对话 ID
            user_message: 用户消息（已经过消息合并）
            send_fn: 发送一条消息给用户的异步回调
            typing_fn: 发送 typing indicator 的异步回调
            status_callback: 桌面端状态回调（透传给 task_runtime）
            metadata: 额外元数据（保留给调用方扩展；Step 1 起不再承载任何分支逻辑）
            images: 图片列表，每个元素为 {"base64": str, "media_type": str} 或 {"url": str}

        Returns:
            完整回复文本（所有中间文字 + 最终文字拼接），用于记录到记忆
        """
        if self.inner_tick_scheduler is not None:
            self.inner_tick_scheduler.note_conversation_start()

        # New user turn — reset per-session BrowserGuard budget so a
        # prior turn's spend doesn't starve this one. Counters are
        # per-session by design, not global.
        self._reset_session_budgets()

        # v2.0 Step 2: focus moves to this conversation at the entry point.
        # Other call sites (inner loop, action start) get wired in Step 3/4.
        if self.attention_manager is not None:
            await self.attention_manager.update(
                current_conversation=chat_id, mode="conversing"
            )

        logger.debug("[%s] incoming: %s", chat_id, user_message[:200])

        ctx = await self._prepare_think(
            chat_id, user_message, send_fn=send_fn, images=images,
            adapter=adapter, user_id=user_id,
        )
        if ctx.early_reply is not None:
            self._schedule_conversation_end(chat_id)
            return ctx.early_reply

        # 直接输出模式：模型裸文本 → 发给用户。
        spoken_parts: list[str] = []

        async def on_model_text(text: str, **_kw) -> None:
            """模型裸文本 → 清理后直接发送给用户。"""
            from src.core.output_sanitizer import sanitize_outgoing

            stripped = strip_internal_thinking_tags(text).strip()
            if not stripped:
                return
            segments = [s.strip() for s in stripped.split("\n\n") if s.strip()]
            for segment in segments:
                segment = sanitize_outgoing(segment)
                if segment:
                    await send_fn(segment)
                    spoken_parts.append(segment)

        async def on_typing() -> None:
            if typing_fn is not None:
                try:
                    await typing_fn()
                except Exception:
                    pass

        start_time = time.monotonic()
        try:
            full_reply = await self._complete_chat(
                chat_id,
                ctx.messages,
                ctx.effective_user_message,
                approved_directory=ctx.approved_directory,
                status_callback=status_callback,
                on_interim_text=on_model_text,
                on_typing=on_typing,
                adapter=adapter,
                user_id=user_id,
                send_fn=send_fn,
                focus_id=ctx.focus_id,
            )

            # 最后一轮的裸文本也需要发送给用户
            tail = strip_internal_thinking_tags(full_reply or "").strip()
            if tail:
                from src.core.output_sanitizer import sanitize_outgoing
                segments = [s.strip() for s in tail.split("\n\n") if s.strip()]
                for segment in segments:
                    segment = sanitize_outgoing(segment)
                    if segment:
                        await send_fn(segment)
                        spoken_parts.append(segment)

            # 后处理：记录"她真正说出口的话"
            memory_text = "\n\n".join(spoken_parts) if spoken_parts else ""
            try:
                if memory_text:
                    await self._record_turn(
                        chat_id, "assistant", memory_text, focus_id=ctx.focus_id,
                    )
                logger.debug(
                    "[%s] spoken_parts=%d 条",
                    chat_id, len(spoken_parts),
                )
            except Exception as post_exc:
                logger.warning(
                    "[brain] 后处理失败（消息已发出，不影响用户）: %s",
                    post_exc, exc_info=True,
                )
            return memory_text

        except asyncio.CancelledError:
            partial = "\n\n".join(spoken_parts) if spoken_parts else ""
            await self._persist_interrupted(
                chat_id=chat_id,
                partial_text=partial,
                reason="owner_message_preempt",
                adapter=adapter,
                kind="conversational",
                focus_id=ctx.focus_id,
            )
            raise
        except Exception as e:
            logger.error(f"LLM 调用失败（conversational）: {e}")
            error_msg = f"LLM 调用失败：{e}"
            from src.core.system_send import send_system_message
            await send_system_message(
                send_fn,
                error_msg,
                source="llm_error",
                chat_id=chat_id,
                adapter=adapter,
                trajectory_store=self.trajectory_store,
                mutation_log=getattr(self, "_mutation_log_ref", None),
                focus_id=ctx.focus_id,
            )
            return error_msg
        finally:
            self._schedule_conversation_end(chat_id)

    async def _persist_interrupted(
        self,
        *,
        chat_id: str,
        partial_text: str,
        reason: str,
        adapter: str = "",
        kind: str = "conversational",
        focus_id: str | None = None,
    ) -> None:
        """Write an INTERRUPTED trajectory entry for a cancelled handler.

        Step 4 M4. Best-effort; never re-raises (the cancellation must
        propagate). The entry carries the partial text we managed to
        stream/produce, so observers can see what got cut off.
        """
        if self.trajectory_store is None:
            return
        try:
            from src.core.trajectory_store import TrajectoryEntryType

            payload: dict[str, Any] = {
                "text": partial_text,
                "reason": reason,
                "kind": kind,  # "conversational" | "inner"
                "partial_chars": len(partial_text),
            }
            if adapter:
                payload["adapter"] = adapter

            source_chat_id = None if kind == "inner" else chat_id
            await self.trajectory_store.append(
                TrajectoryEntryType.INTERRUPTED,
                source_chat_id,
                "lapwing",
                payload,
                focus_id=focus_id,
            )
        except Exception:
            logger.warning(
                "INTERRUPTED trajectory write failed for %s (kind=%s)",
                chat_id, kind, exc_info=True,
            )

    async def compose_proactive(
        self,
        purpose: str,
        context_prompt: str,
        *,
        sense_context: dict | None = None,
        tools: list[str] | None = None,
        max_tokens: int = 300,
        chat_id: str | None = None,
    ) -> str | None:
        """Generate a proactive message with full persona pipeline.

        Unlike think_conversational(), this does NOT require a user message.
        Used by heartbeat/consciousness actions for user-facing proactive messages.

        Args:
            purpose: Human-readable reason (e.g., "主动消息", "兴趣分享")
            context_prompt: The action-specific prompt with context
            sense_context: Optional environment context dict
            tools: Optional list of tool names to allow. None = no tools.
            max_tokens: Max tokens for the LLM response
            chat_id: Target chat_id. If None, uses channel_manager default.

        Returns:
            Generated message text, or None if the model decides not to speak.
        """
        resolved_chat_id = chat_id
        if resolved_chat_id is None and self.channel_manager is not None:
            resolved_chat_id = getattr(self.channel_manager, "default_chat_id", None)
        if resolved_chat_id is None:
            logger.warning("[compose_proactive] 无法确定 chat_id，跳过")
            return None

        # 1. 构造用户提示（proactive 没有真实的 user turn，用 sense + context
        #    做合成输入让模型知道环境上下文）
        sense_text = ""
        if sense_context:
            sense_text = "[当前环境]\n"
            for k, v in sense_context.items():
                sense_text += f"- {k}: {v}\n"
            sense_text += "\n"

        proactive_user = {"role": "user", "content": f"{sense_text}{context_prompt}"}

        # 2. StateSerializer 组装：单条合成用户消息作为 recent_messages。
        #    total = 2 < 4 → 走短对话分支，voice 折进 system prompt。
        messages = await self._render_messages(
            resolved_chat_id,
            [proactive_user],
        )

        # 4. 生成回复
        if tools:
            # 有工具需求：走 TaskRuntime
            from src.core.shell_policy import extract_execution_constraints
            tool_specs = self.task_runtime.chat_tools(
                shell_enabled=False,
                browser_enabled=BROWSER_ENABLED,
            )
            # 过滤为仅允许的工具名
            allowed = set(tools)
            tool_specs = [t for t in tool_specs if t.get("function", {}).get("name") in allowed]

            deps = RuntimeDeps(
                execute_shell=execute_shell,
                policy=build_shell_runtime_policy(verify_constraints_fn=verify_constraints),
                shell_default_cwd=SHELL_DEFAULT_CWD,
                shell_allow_sudo=SHELL_ALLOW_SUDO,
            )
            constraints = extract_execution_constraints("")

            # compose_proactive is an autonomous outbound path — every
            # send_message invocation must consult ProactiveMessageGate.
            # Flag the services dict so the executor knows the call is
            # proactive even though the runtime profile is not
            # "inner_tick" (compose_proactive may run on chat profiles).
            services = self._build_services()
            services["proactive_send_active"] = True

            response_text = await self.task_runtime.complete_chat(
                chat_id=resolved_chat_id,
                messages=messages,
                constraints=constraints,
                tools=tool_specs,
                deps=deps,
                services=services,
                adapter="",
                user_id="",
                focus_id=None,
            )
        else:
            # 无工具：单次 LLM 调用
            response_text = await self.router.complete(
                messages,
                slot="heartbeat_proactive",
                max_tokens=max_tokens,
                session_key=f"chat:{resolved_chat_id}",
                origin=f"compose_proactive.{purpose}",
            )

        if not response_text or not response_text.strip():
            return None

        return response_text
