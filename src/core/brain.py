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
from src.core.reasoning_tags import (
    split_on_markers,
    split_on_paragraphs,
    strip_internal_thinking_tags,
    strip_split_markers,
)
from src.core.task_runtime import RuntimeDeps, TaskRuntime
from src.core.shell_policy import (
    ExecutionSessionState,
    build_shell_runtime_policy,
    extract_execution_constraints,
)
from src.core.verifier import verify_shell_constraints_status as verify_constraints
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from src.logging.event_logger import events
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import execute as execute_shell
from src.tools.types import ToolExecutionRequest
from config.settings import (
    CHAT_WEB_TOOLS_ENABLED,
    MAX_HISTORY_TURNS,
    MESSAGE_SPLIT_DELAY_BASE,
    MESSAGE_SPLIT_DELAY_MAX,
    MESSAGE_SPLIT_DELAY_PER_CHAR,
    MESSAGE_SPLIT_ENABLED,
    MESSAGE_SPLIT_FALLBACK_NEWLINE,
    MESSAGE_SPLIT_SINGLE_NL_MIN_LEN,
    SHELL_ALLOW_SUDO,
    SHELL_DEFAULT_CWD,
    SHELL_ENABLED,
    SKILLS_DISPATCH_TOOL_WHITELIST,
    SOUL_PATH,
)

if TYPE_CHECKING:
    from src.core.skills import SkillDefinition, SkillManager
    from src.core.experience_skills import ExperienceSkill, ExperienceSkillManager
    from src.core.trace_recorder import SkillUsageInfo, TraceRecorder
    from src.memory.interest_tracker import InterestTracker
    from src.core.self_reflection import SelfReflection
    from src.core.knowledge_manager import KnowledgeManager
    from src.memory.vector_store import VectorStore
    from src.core.constitution_guard import ConstitutionGuard
    from src.core.tactical_rules import TacticalRules
    from src.core.evolution_engine import EvolutionEngine

logger = logging.getLogger("lapwing.core.brain")

# ── 中间文字过滤：屏蔽搜索过程的内部独白 ─────────────────────────────

_INTERNAL_MONOLOGUE_PATTERNS = [
    "等我重新搜",
    "奇怪",
    "不对我再",
    "我再看看",
    "搜到的好像",
    "让我确认",
    "我再查",
    "等等，",
    "我试试",
    "有些还没更新",
    "我再仔细",
    "可能每个数据源",
    "等我搜",
    "我搜一下",
    "我查一下",
    "让我看看",
    "我翻一下",
    "我找一下",
    "啊等等",
    "不对不对",
    "嗯让我",
]


def _is_internal_monologue(text: str) -> bool:
    """判断文字是否属于搜索过程中的内部独白，不应发给用户。"""
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _INTERNAL_MONOLOGUE_PATTERNS:
        if pattern in stripped:
            return True
    return False


@dataclasses.dataclass
class _ThinkCtx:
    """think() / think_conversational() 共享前置逻辑的结果。"""
    messages: list[dict]
    effective_user_message: str
    approved_directory: str | None
    early_reply: str | None = None
    matched_experience_skills: list | None = None  # list[ExperienceSkill]
    session_id: str | None = None


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path, *, model_config=None):
        self.auth_manager = AuthManager()
        self._model_config = model_config
        self.router = LLMRouter(auth_manager=self.auth_manager, model_config=model_config)
        self.tool_registry = build_default_tool_registry()
        self.memory = ConversationMemory(db_path)
        self.task_runtime = TaskRuntime(router=self.router, tool_registry=self.tool_registry, memory=self.memory)
        self.fact_extractor = FactExtractor(self.memory, self.router)
        from src.memory.compactor import ConversationCompactor
        self.compactor = ConversationCompactor(self.memory, self.router)
        from src.core.prompt_builder import PromptSnapshotManager
        self._prompt_snapshot = PromptSnapshotManager()
        self.interest_tracker: InterestTracker | None = None
        self.self_reflection: SelfReflection | None = None
        self.knowledge_manager: KnowledgeManager | None = None
        self.vector_store: VectorStore | None = None
        self.skill_manager: SkillManager | None = None
        self.experience_skill_manager: ExperienceSkillManager | None = None
        self.event_bus = None
        self._system_prompt: str | None = None
        self.constitution_guard: ConstitutionGuard | None = None
        self.tactical_rules: TacticalRules | None = None
        self.evolution_engine: EvolutionEngine | None = None
        self.session_manager = None  # Set externally (SessionManager | None)
        self.auto_memory_extractor = None  # Set externally (AutoMemoryExtractor | None)
        self.reminder_scheduler = None  # Set externally (ReminderScheduler | None)
        self.channel_manager = None  # Set externally (ChannelManager | None)
        self.memory_index = None  # Set externally (MemoryIndex | None)
        self.task_flow_manager = None  # Set externally (TaskFlowManager | None)
        self.quality_checker = None  # Set externally (ReplyQualityChecker | None)
        self.delegation_manager = None  # Set externally (DelegationManager | None)
        self.consciousness_engine = None  # Set externally (ConsciousnessEngine | None)
        self._conversation_end_task: asyncio.Task | None = None

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    async def clear_short_term_memory(self, chat_id: str) -> None:
        """仅清除短期对话记忆。"""
        self.task_runtime.clear_chat_state(chat_id)
        if self.session_manager is not None:
            active = self.session_manager._get_active(chat_id)
            if active is not None:
                await self.session_manager.deactivate(active)
                await self.memory.clear_session_cache(active.id)
        await self.memory.clear(chat_id)

    async def clear_all_memory(self, chat_id: str) -> None:
        """清除指定 chat 的长短期记忆。"""
        self.task_runtime.clear_chat_state(chat_id)
        await self.fact_extractor.clear_chat_state(chat_id)
        if self.interest_tracker is not None:
            await self.interest_tracker.clear_chat_state(chat_id)

        await self.memory.clear_chat_all(chat_id)

        if self.vector_store is not None:
            try:
                await self.vector_store.delete_chat(chat_id)
            except Exception as exc:
                logger.warning(f"[{chat_id}] 清除向量记忆失败: {exc}")

    @property
    def system_prompt(self) -> str:
        """懒加载 system prompt（核心人格 soul）。优先从 data/identity/soul.md 加载。"""
        if self._system_prompt is None:
            if SOUL_PATH.exists():
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
        reload_prompt("lapwing_capabilities")
        self._prompt_snapshot.invalidate()
        logger.info("已重新加载所有 prompt 缓存")

    def reload_skills(self) -> None:
        if self.skill_manager is None:
            return
        self.skill_manager.reload()

    def _chat_session_key(self, chat_id: str) -> str:
        return f"chat:{chat_id}"

    def list_model_options(self) -> list[dict[str, Any]]:
        return self.router.list_model_options()

    def model_status(self, chat_id: str) -> dict[str, Any]:
        return self.router.model_status(session_key=self._chat_session_key(chat_id))

    def switch_model(self, chat_id: str, selector: str) -> dict[str, Any]:
        self._prompt_snapshot.invalidate()
        return self.router.switch_session_model(
            session_key=self._chat_session_key(chat_id),
            selector=selector,
        )

    def reset_model(self, chat_id: str) -> dict[str, Any]:
        self._prompt_snapshot.invalidate()
        return self.router.clear_session_model(session_key=self._chat_session_key(chat_id))

    async def _complete_chat(
        self,
        chat_id: str,
        messages: list[dict],
        user_message: str,
        approved_directory: str | None = None,
        include_skill_activation_tool: bool = False,
        status_callback=None,
        on_interim_text=None,
        on_typing=None,
        adapter: str = "",
        user_id: str = "",
    ) -> str:
        constraints = extract_execution_constraints(
            user_message,
            approved_directory=approved_directory,
        )
        tools = self.task_runtime.chat_tools(
            shell_enabled=SHELL_ENABLED,
            web_enabled=CHAT_WEB_TOOLS_ENABLED,
            skill_activation_enabled=include_skill_activation_tool,
        )
        services = {}
        if include_skill_activation_tool and self.skill_manager is not None:
            services["skill_manager"] = self.skill_manager
        if self.reminder_scheduler is not None:
            services["reminder_scheduler"] = self.reminder_scheduler
        if self.channel_manager is not None:
            services["channel_manager"] = self.channel_manager
        if self.memory_index is not None:
            services["memory_index"] = self.memory_index
        delegation_manager = getattr(self, "delegation_manager", None)
        if delegation_manager is not None:
            services["delegation_manager"] = delegation_manager
        services["router"] = self.router

        deps = RuntimeDeps(
            execute_shell=execute_shell,
            policy=build_shell_runtime_policy(verify_constraints_fn=verify_constraints),
            shell_default_cwd=SHELL_DEFAULT_CWD,
            shell_allow_sudo=SHELL_ALLOW_SUDO,
        )

        return await self.task_runtime.complete_chat(
            chat_id=chat_id,
            messages=messages,
            constraints=constraints,
            tools=tools,
            deps=deps,
            status_callback=status_callback,
            event_bus=self.event_bus,
            on_consent_required=lambda state: self.task_runtime.record_pending_confirmation(chat_id, state),
            services=services,
            on_interim_text=on_interim_text,
            on_typing=on_typing,
            adapter=adapter,
            user_id=user_id,
        )

    async def _build_system_prompt(self, chat_id: str, user_message: str = "") -> str:
        """按优先级分层组装 system prompt — delegates to prompt_builder."""
        from src.core.prompt_builder import build_system_prompt
        return await build_system_prompt(
            system_prompt=self.system_prompt,
            chat_id=chat_id,
            user_message=user_message,
            memory=self.memory,
            vector_store=self.vector_store,
            knowledge_manager=self.knowledge_manager,
            skill_manager=self.skill_manager,
            memory_index=self.memory_index,
        )

    def _inject_voice_reminder(self, messages: list[dict]) -> None:
        from src.core.prompt_builder import inject_voice_reminder
        inject_voice_reminder(messages)

    def _schedule_conversation_end(self) -> None:
        """延迟判定对话结束。用户最后一条消息后 N 秒无新消息算结束。"""
        if self.consciousness_engine is None:
            return
        if self._conversation_end_task is not None:
            self._conversation_end_task.cancel()

        from config.settings import CONSCIOUSNESS_CONVERSATION_END_DELAY

        async def _delayed_end():
            await asyncio.sleep(CONSCIOUSNESS_CONVERSATION_END_DELAY)
            if self.consciousness_engine is not None:
                self.consciousness_engine.on_conversation_end()

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

    def _skill_activation_tool_enabled(self) -> bool:
        return self.skill_manager is not None and self.skill_manager.has_model_visible_skills()

    async def run_skill_command(
        self,
        *,
        chat_id: str,
        raw_user_message: str,
        skill_name: str,
        user_input: str = "",
        status_callback=None,
    ) -> str:
        """执行用户显式技能命令。"""
        await self.memory.append(chat_id, "user", raw_user_message)

        self.fact_extractor.notify(chat_id)
        if self.interest_tracker is not None:
            self.interest_tracker.notify(chat_id)

        if self.skill_manager is None or not self.skill_manager.enabled:
            reply = "技能系统当前未启用。"
            await self.memory.append(chat_id, "assistant", reply)
            return reply

        skill = self.skill_manager.get(skill_name)
        if skill is None:
            reply = f"未找到技能 `{skill_name}`。"
            await self.memory.append(chat_id, "assistant", reply)
            return reply
        if not skill.user_invocable:
            reply = f"技能 `{skill.name}` 不允许用户直接调用。"
            await self.memory.append(chat_id, "assistant", reply)
            return reply

        dialogue_generated = skill.command_dispatch != "tool"
        try:
            if skill.command_dispatch == "tool":
                reply = await self._run_skill_direct_dispatch(skill=skill, user_input=user_input)
            else:
                reply = await self._run_skill_dialogue(
                    chat_id=chat_id,
                    raw_user_message=raw_user_message,
                    skill=skill,
                    user_input=user_input,
                    status_callback=status_callback,
                )
        except Exception as exc:
            logger.warning("[skills] 执行技能 `%s` 失败: %s", skill.name, exc)
            reply = f"技能 `{skill.name}` 执行失败：{exc}"

        # 仅对模型对话分支清洗，工具直派分支保留原始工具输出。
        if dialogue_generated:
            reply = strip_internal_thinking_tags(reply)

        await self.memory.append(chat_id, "assistant", reply)
        return reply

    async def _run_skill_dialogue(
        self,
        *,
        chat_id: str,
        raw_user_message: str,
        skill: "SkillDefinition",
        user_input: str,
        status_callback=None,
    ) -> str:
        assert self.skill_manager is not None
        activation = self.skill_manager.activate(skill.name, user_input=user_input)
        skill_context = str(activation.get("wrapped_content", "")).strip()
        model_user_message = user_input.strip() or f"请按照技能 `{skill.name}` 的说明完成任务。"

        history = await self.memory.get(chat_id)
        recent_messages = self._recent_messages(
            history,
            user_message=model_user_message,
            original_user_message=raw_user_message,
        )
        system_content = await self._build_system_prompt(chat_id, model_user_message)

        if skill_context:
            system_content = (
                f"{system_content}\n\n"
                "## 显式激活技能\n\n"
                f"{skill_context}"
            )

        messages = [
            {"role": "system", "content": system_content},
            *recent_messages,
        ]

        self._inject_voice_reminder(messages)

        return await self._complete_chat(
            chat_id,
            messages,
            model_user_message,
            include_skill_activation_tool=self._skill_activation_tool_enabled(),
            status_callback=status_callback,
        )

    async def _run_skill_direct_dispatch(
        self,
        *,
        skill: "SkillDefinition",
        user_input: str,
    ) -> str:
        if skill.command_dispatch != "tool" or not skill.command_tool:
            return f"技能 `{skill.name}` 未声明可直派工具。"

        command_tool = skill.command_tool
        if command_tool not in SKILLS_DISPATCH_TOOL_WHITELIST:
            return f"技能 `{skill.name}` 请求的工具 `{command_tool}` 不在白名单中。"

        command_text = user_input.strip()
        if skill.command_arg_mode == "raw" and not command_text:
            return f"技能 `{skill.name}` 需要参数输入。"

        constraints_text = command_text or f"执行技能 `{skill.name}` 的工具直派任务"
        constraints = extract_execution_constraints(constraints_text)
        state = ExecutionSessionState(constraints=constraints)
        deps = RuntimeDeps(
            execute_shell=execute_shell,
            policy=build_shell_runtime_policy(verify_constraints_fn=verify_constraints),
            shell_default_cwd=SHELL_DEFAULT_CWD,
            shell_allow_sudo=SHELL_ALLOW_SUDO,
        )
        result = await self.task_runtime.execute_tool(
            request=ToolExecutionRequest(
                name=command_tool,
                arguments={
                    "command": command_text,
                    "commandName": f"/{skill.name}",
                    "skillName": skill.name,
                },
            ),
            profile="chat_shell",
            state=state,
            deps=deps,
            services={"skill_manager": self.skill_manager} if self.skill_manager is not None else None,
        )
        return self._format_dispatched_tool_result(skill_name=skill.name, result=result.payload, success=result.success)

    def _format_dispatched_tool_result(
        self,
        *,
        skill_name: str,
        result: dict,
        success: bool,
    ) -> str:
        if not success:
            reason = str(result.get("reason", "")).strip() or "工具执行失败。"
            return f"技能 `{skill_name}` 直派执行失败：{reason}"

        stdout = str(result.get("stdout", "")).strip()
        stderr = str(result.get("stderr", "")).strip()
        if stdout:
            return stdout
        if stderr:
            return stderr
        if "content" in result and str(result.get("content", "")).strip():
            return str(result.get("content", "")).strip()
        return f"技能 `{skill_name}` 已执行完成。"

    async def _prepare_think(
        self,
        chat_id: str,
        user_message: str,
        send_fn=None,
    ) -> "_ThinkCtx":
        """共享前置逻辑：记忆写入、纠正检测、agent dispatch、context 组装。

        send_fn 非空时，immediate_reply / agent_reply 会通过它发送（用于 conversational 模式）。
        返回 _ThinkCtx；若 early_reply 非 None 则表示已完成回复，调用方直接返回该值即可。
        """
        # Session 解析（启用时）
        session_id = None
        if self.session_manager is not None:
            try:
                session = await self.session_manager.resolve_session(chat_id, user_message)
                session_id = session.id
            except Exception as e:
                logger.warning(f"[{chat_id}] Session resolution failed, using legacy path: {e}")

        if session_id is not None:
            await self.memory.append_to_session(chat_id, session_id, "user", user_message)
        else:
            await self.memory.append(chat_id, "user", user_message)

        self.fact_extractor.notify(chat_id)
        if self.interest_tracker is not None:
            self.interest_tracker.notify(chat_id)

        effective_user_message, approved_directory, immediate_reply = (
            self.task_runtime.resolve_pending_confirmation(chat_id, user_message)
        )
        if immediate_reply is not None:
            if session_id is not None:
                await self.memory.append_to_session(chat_id, session_id, "assistant", immediate_reply)
            else:
                await self.memory.append(chat_id, "assistant", immediate_reply)
            if send_fn is not None:
                await send_fn(immediate_reply)
            return _ThinkCtx(messages=[], effective_user_message=effective_user_message,
                             approved_directory=approved_directory, early_reply=immediate_reply,
                             session_id=session_id)

        # 实时纠正检测：异步触发规则提取，不阻塞主回复流程
        if self.tactical_rules is not None:
            if self.tactical_rules.might_be_correction(user_message):
                if session_id is not None:
                    history = await self.memory.get_session_messages(session_id)
                else:
                    history = await self.memory.get(chat_id)
                asyncio.create_task(
                    self.tactical_rules.process_correction(
                        chat_id, user_message, list(history)
                    )
                )

        # 压缩 + 组装 messages
        await self.compactor.try_compact(chat_id, session_id=session_id)
        if session_id is not None:
            history = await self.memory.get_session_messages(session_id)
        else:
            history = await self.memory.get(chat_id)
        recent_messages = self._recent_messages(
            history,
            user_message=effective_user_message,
            original_user_message=user_message,
        )
        # System prompt 快照：同一 session 内复用冻结的 prompt（prefix cache 优化）
        cached = self._prompt_snapshot.get(session_id) if session_id else None
        if cached is not None:
            system_content = cached
        else:
            system_content = await self._build_system_prompt(chat_id, effective_user_message)
            if session_id:
                self._prompt_snapshot.freeze(session_id, system_content)

        messages = [
            {"role": "system", "content": system_content},
            *recent_messages,
        ]

        # 经验技能检索与注入（Pattern 5：注入为 user message 保护 prefix cache）
        matched_experience_skills = None
        if self.experience_skill_manager is not None and effective_user_message:
            try:
                matched_experience_skills = await self.experience_skill_manager.retrieve(
                    effective_user_message
                )
                if matched_experience_skills:
                    from config.settings import EXPERIENCE_SKILLS_MAX_INJECT_TOKENS
                    injection = self.experience_skill_manager.format_injection(
                        matched_experience_skills,
                        max_tokens=EXPERIENCE_SKILLS_MAX_INJECT_TOKENS,
                    )
                    if injection:
                        # 注入为合成 user message（而非追加到 system prompt）
                        # 这样 system prompt 保持稳定，provider 的 prefix cache 不失效
                        skill_msg = {
                            "role": "user",
                            "content": f"[System Note]\n## 参考经验\n\n{injection}\n[/System Note]",
                        }
                        # 插入到用户实际消息之前（messages 末尾是用户消息）
                        messages.insert(len(messages) - 1, skill_msg)
                        logger.debug(
                            "[%s] 注入 %d 个经验技能（user message）: %s",
                            chat_id,
                            len(matched_experience_skills),
                            [s.meta.id for s in matched_experience_skills],
                        )
            except Exception as exc:
                logger.warning("[%s] 经验技能检索失败: %s", chat_id, exc)

        self._inject_voice_reminder(messages)

        return _ThinkCtx(
            messages=messages,
            effective_user_message=effective_user_message,
            approved_directory=approved_directory,
            matched_experience_skills=matched_experience_skills,
            session_id=session_id,
        )

    async def think(self, chat_id: str, user_message: str, status_callback=None) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: Telegram 对话 ID
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
                include_skill_activation_tool=self._skill_activation_tool_enabled(),
                status_callback=status_callback,
            )
            reply = strip_internal_thinking_tags(reply)
            if ctx.session_id is not None:
                await self.memory.append_to_session(chat_id, ctx.session_id, "assistant", reply)
            else:
                await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            duration = time.monotonic() - start_time
            self._schedule_trace_recording(user_message, reply, ctx.matched_experience_skills, duration)
            if self.quality_checker is not None:
                import asyncio as _asyncio
                _asyncio.create_task(self._check_reply_quality(chat_id, ctx.messages, reply))
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            if ctx.session_id is not None:
                await self.memory.remove_last_session(ctx.session_id)
            else:
                await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"

    async def think_conversational(
        self,
        chat_id: str,
        user_message: str,
        send_fn,
        typing_fn=None,
        status_callback=None,
        adapter: str = "",
        user_id: str = "",
    ) -> str:
        """边查边说模式：中间文字通过 send_fn 实时发出，供 Telegram 对话使用。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户消息（已经过消息合并）
            send_fn: 发送一条消息给用户的异步回调
            typing_fn: 发送 typing indicator 的异步回调
            status_callback: 桌面端状态回调（透传给 task_runtime）

        Returns:
            完整回复文本（所有中间文字 + 最终文字拼接），用于记录到记忆
        """
        # 通知意识引擎：对话开始
        if self.consciousness_engine is not None:
            self.consciousness_engine.on_conversation_start()

        events.log("conversation", "incoming",
            message=user_message[:500],
            channel=adapter,
            chat_id=chat_id,
            user_id=user_id,
        )

        ctx = await self._prepare_think(chat_id, user_message, send_fn=send_fn)
        if ctx.early_reply is not None:
            self._schedule_conversation_end()  # ← ADD THIS
            return ctx.early_reply

        # 跟踪通过流式回调已发出的文字片段
        parts_sent: list[str] = []
        # 原始（未拆分）文本，用于 already_sent 比较
        originals_sent: list[str] = []

        async def _send_with_split(text: str) -> None:
            """按 [SPLIT] 拆分后逐条发送，片段间模拟打字延迟。

            如果模型未输出 [SPLIT] 但文本含多个段落（\\n\\n），
            在 MESSAGE_SPLIT_FALLBACK_NEWLINE 开启时自动按段落拆分。
            """
            if not MESSAGE_SPLIT_ENABLED:
                await send_fn(text)
                parts_sent.append(text)
                originals_sent.append(text)
                return

            # 优先按 [SPLIT] 标记拆分
            if "[" in text:
                segments = split_on_markers(text)
            else:
                segments = [text]

            # Fallback 1：模型没输出 [SPLIT]，按 \n\n 段落拆分
            if len(segments) <= 1 and MESSAGE_SPLIT_FALLBACK_NEWLINE and "\n" in text:
                segments = split_on_paragraphs(text)

            # Fallback 2：仍为单段且长度超阈值，按单 \n 拆分
            if len(segments) <= 1 and MESSAGE_SPLIT_FALLBACK_NEWLINE and "\n" in text:
                line_segments = [s.strip() for s in text.split("\n") if s.strip()]
                if len(line_segments) >= 2 and len(text) >= MESSAGE_SPLIT_SINGLE_NL_MIN_LEN:
                    segments = line_segments

            originals_sent.append(text)
            for i, seg in enumerate(segments):
                if i > 0:
                    await on_typing()
                    delay = min(
                        MESSAGE_SPLIT_DELAY_BASE + len(seg) * MESSAGE_SPLIT_DELAY_PER_CHAR,
                        MESSAGE_SPLIT_DELAY_MAX,
                    )
                    await asyncio.sleep(delay)
                await send_fn(seg)
                parts_sent.append(seg)

        async def on_interim_text(text: str) -> None:
            stripped = strip_internal_thinking_tags(text)
            if stripped and not _is_internal_monologue(stripped):
                await _send_with_split(stripped)

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
                include_skill_activation_tool=self._skill_activation_tool_enabled(),
                status_callback=status_callback,
                on_interim_text=on_interim_text,
                on_typing=on_typing,
                adapter=adapter,
                user_id=user_id,
            )
            full_reply = strip_internal_thinking_tags(full_reply)

            # 如果最终回复没有通过流式发出（无工具场景 / 特殊状态消息），则现在发送
            _reply_clean = strip_split_markers(full_reply).strip()
            already_sent = any(
                _reply_clean == strip_split_markers(orig).strip()
                for orig in originals_sent
            ) if originals_sent else False

            if not already_sent and full_reply:
                await _send_with_split(full_reply)

            # 合并所有片段存入记忆（[SPLIT] 不写入记忆）
            memory_text = "\n\n".join(parts_sent) if parts_sent else strip_split_markers(full_reply)
            if ctx.session_id is not None:
                await self.memory.append_to_session(chat_id, ctx.session_id, "assistant", memory_text)
            else:
                await self.memory.append(chat_id, "assistant", memory_text)
            logger.debug(f"[{chat_id}] 流式回复完成，片段数: {len(parts_sent)}")
            events.log("conversation", "outgoing",
                message=memory_text[:500],
                channel=adapter,
                chat_id=chat_id,
            )
            duration = time.monotonic() - start_time
            self._schedule_trace_recording(user_message, memory_text, ctx.matched_experience_skills, duration)
            if self.quality_checker is not None:
                import asyncio as _asyncio
                _asyncio.create_task(self._check_reply_quality(chat_id, ctx.messages, memory_text))
            return memory_text

        except Exception as e:
            logger.error(f"LLM 调用失败（conversational）: {e}")
            if ctx.session_id is not None:
                await self.memory.remove_last_session(ctx.session_id)
            else:
                await self.memory.remove_last(chat_id)
            error_msg = "抱歉，我刚才走神了一下。你能再说一次吗？"
            await send_fn(error_msg)
            return error_msg
        finally:
            self._schedule_conversation_end()

    def _schedule_trace_recording(
        self,
        user_message: str,
        reply: str,
        matched_skills: list | None,
        duration_seconds: float,
    ) -> None:
        """异步（非阻塞）记录执行轨迹和更新使用统计。"""
        if self.experience_skill_manager is None:
            return

        esm = self.experience_skill_manager

        async def _record() -> None:
            try:
                from src.core.trace_recorder import SkillUsageInfo

                skill_usage: SkillUsageInfo | None = None
                skill_id: str | None = None
                match_level: str | None = None

                if matched_skills:
                    # 取第一个匹配技能作为主要技能记录
                    first = matched_skills[0]
                    skill_id = first.meta.id
                    match_level = "quick"  # Phase 1 简化，Phase 2 从 MatchResult 获取
                    skill_usage = SkillUsageInfo(
                        id=skill_id,
                        version=first.meta.version,
                        match_level=match_level,
                    )
                    # 更新技能使用统计
                    esm.update_skill_stats(skill_id, used=True)

                trace = esm.trace_recorder.build_trace(
                    user_request=user_message,
                    output_summary=reply,
                    duration_seconds=duration_seconds,
                    skill_used=skill_usage,
                )
                esm.trace_recorder.record_trace(trace)

                esm.registry_manager.record_execution(
                    skill_id=skill_id,
                    match_level=match_level,
                    request_summary=user_message[:100],
                )
            except Exception as exc:
                logger.warning("轨迹记录失败: %s", exc)

        asyncio.create_task(_record())

    async def _check_reply_quality(self, chat_id: str, messages: list[dict], reply: str) -> None:
        """异步回复质量检查（不阻塞主回复路径）。"""
        try:
            await self.quality_checker.check(messages, reply)
        except Exception as e:
            logger.debug("[%s] 质量检查异常: %s", chat_id, e)
