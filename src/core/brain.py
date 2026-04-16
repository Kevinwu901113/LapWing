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
from src.core.output_sanitizer import sanitize_outgoing
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
    from src.core.knowledge_manager import KnowledgeManager
    from src.memory.vector_store import VectorStore

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
    session_id: str | None = None


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path, *, model_config=None):
        self.auth_manager = AuthManager()
        self._model_config = model_config
        self.router = LLMRouter(auth_manager=self.auth_manager, model_config=model_config)
        from config.settings import PHASE0_MODE
        if PHASE0_MODE == "B":
            from src.tools.phase0_tools import build_phase0_tool_registry
            self.tool_registry = build_phase0_tool_registry()
        elif PHASE0_MODE == "A":
            from src.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry()  # 空注册表 = 0 工具
        else:
            self.tool_registry = build_default_tool_registry()
        self.memory = ConversationMemory(db_path)
        from config.settings import TASK_NO_ACTION_BUDGET, TASK_ERROR_BURST_THRESHOLD
        self.task_runtime = TaskRuntime(
            router=self.router,
            tool_registry=self.tool_registry,
            memory=self.memory,
            no_action_budget=TASK_NO_ACTION_BUDGET,
            error_burst_threshold=TASK_ERROR_BURST_THRESHOLD,
        )
        from src.memory.compactor import ConversationCompactor
        self.compactor = ConversationCompactor(self.memory, self.router)
        from src.core.prompt_builder import PromptSnapshotManager, PromptBuilder
        self._prompt_snapshot = PromptSnapshotManager()
        self.prompt_builder: PromptBuilder | None = None  # Set externally (container)
        self.knowledge_manager: KnowledgeManager | None = None
        self.vector_store: VectorStore | None = None
        self.skill_manager = None  # SkillManager | None — Phase 3 重建
        self.event_bus = None
        self._system_prompt: str | None = None
        self.reminder_scheduler = None  # Set externally (ReminderScheduler | None)
        self.channel_manager = None  # Set externally (ChannelManager | None)
        self.task_flow_manager = None  # Set externally (TaskFlowManager | None)
        self.delegation_manager = None  # Set externally (DelegationManager | None)
        self.agent_registry = None  # Set externally (AgentRegistry | None)
        self.agent_dispatcher = None  # Set externally (AgentDispatcher | None)
        self.consciousness_engine = None  # Set externally (ConsciousnessEngine | None)
        self.pending_task_store = None  # Set externally (PendingTaskStore | None)
        self._conversation_end_task: asyncio.Task | None = None
        from src.core.background_review import BackgroundReviewer
        self._background_reviewer = BackgroundReviewer(interval=10)

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    async def clear_short_term_memory(self, chat_id: str) -> None:
        """仅清除短期对话记忆。"""
        self.task_runtime.clear_chat_state(chat_id)
        await self.memory.clear(chat_id)

    async def clear_all_memory(self, chat_id: str) -> None:
        """清除指定 chat 的长短期记忆。"""
        self.task_runtime.clear_chat_state(chat_id)
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
            from config.settings import PHASE0_MODE
            if PHASE0_MODE:
                from src.core.prompt_builder import build_phase0_prompt
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
        resumption_context: dict | None = None,
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
        agent_registry = getattr(self, "_agent_registry", None)
        if agent_registry is not None:
            services["agent_registry"] = agent_registry
        dispatcher = getattr(self, "_dispatcher_ref", None)
        if dispatcher is not None:
            services["dispatcher"] = dispatcher
        services["router"] = self.router
        incident_manager = getattr(self, "incident_manager", None)
        if incident_manager is not None:
            services["incident_manager"] = incident_manager
        # Phase 3 记忆系统
        note_store = getattr(self, "_note_store", None)
        if note_store is not None:
            services["note_store"] = note_store
        memory_vector_store = getattr(self, "_memory_vector_store", None)
        if memory_vector_store is not None:
            services["vector_store"] = memory_vector_store
        services["conversation_memory"] = self.memory
        # Phase 4: DurableScheduler + 个人工具所需服务
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
            resumption_context=resumption_context,
        )

    async def _build_system_prompt(
        self,
        chat_id: str,
        user_message: str = "",
        adapter: str = "",
        user_id: str = "",
        auth_level: int = 3,
        group_id: str | None = None,
    ) -> str:
        """按优先级分层组装 system prompt — delegates to prompt_builder."""
        from config.settings import PHASE0_MODE
        if PHASE0_MODE:
            return self.system_prompt

        if self.prompt_builder is not None:
            # Phase 2：class-based PromptBuilder（4 层）
            channel = adapter or "desktop"
            return await self.prompt_builder.build_system_prompt(
                channel=channel,
                actor_id=user_id or None,
                actor_name=None,
                auth_level=auth_level,
                group_id=group_id,
            )

        # fallback：极简 prompt（不应到达，但防御性保留）
        return self.system_prompt

    def _inject_voice_reminder(self, messages: list[dict]) -> None:
        from config.settings import PHASE0_MODE
        if PHASE0_MODE:
            return  # Phase 0：不注入 voice reminder
        if self.prompt_builder is not None:
            self.prompt_builder.inject_voice_reminder(messages)
        else:
            # fallback for tests that don't set prompt_builder
            from src.core.prompt_builder import PromptBuilder
            PromptBuilder().inject_voice_reminder(messages)

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
        session_id = None

        # 存储文本到记忆（图片不持久化，只在当前 LLM 调用中传递）
        stored_text = user_message
        if images:
            img_tag = f"[用户发送了{len(images)}张图片]" if len(images) > 1 else "[用户发送了图片]"
            stored_text = f"{user_message}\n{img_tag}" if user_message.strip() else img_tag

        await self.memory.append(chat_id, "user", stored_text)

        effective_user_message, approved_directory, immediate_reply = (
            self.task_runtime.resolve_pending_confirmation(chat_id, user_message)
        )
        if immediate_reply is not None:
            await self.memory.append(chat_id, "assistant", immediate_reply)
            if send_fn is not None:
                await send_fn(immediate_reply)
            return _ThinkCtx(messages=[], effective_user_message=effective_user_message,
                             approved_directory=approved_directory, early_reply=immediate_reply,
                             session_id=session_id)

        # 压缩 + 组装 messages
        await self.compactor.try_compact(chat_id, session_id=session_id)
        history = await self.memory.get(chat_id)
        recent_messages = self._recent_messages(
            history,
            user_message=effective_user_message,
            original_user_message=user_message,
        )

        # Trust tagging：在消息进入 LLM 上下文时包装（不改变 memory 中的存储）
        from src.core.trust_tagger import TrustTagger
        from src.core.vitals import now_taipei
        now_str = now_taipei().isoformat()

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

        # System prompt 快照：同一 session 内复用冻结的 prompt（prefix cache 优化）
        system_content = await self._build_system_prompt(
            chat_id, effective_user_message,
            adapter=adapter, user_id=user_id,
            auth_level=auth_level, group_id=group_id,
        )

        messages = [
            {"role": "system", "content": system_content},
            *recent_messages,
        ]

        # 多模态：将图片注入到最后一条 user 消息中（Anthropic content blocks 格式）
        if images:
            self._inject_images_into_last_user_message(messages, images)

        self._inject_voice_reminder(messages)

        return _ThinkCtx(
            messages=messages,
            effective_user_message=effective_user_message,
            approved_directory=approved_directory,
            session_id=session_id,
        )

    async def _prepare_think_for_resumption(
        self,
        chat_id: str,
        metadata: dict,
    ) -> "_ThinkCtx":
        """恢复触发专用的 _prepare_think：不写入用户消息，注入恢复上下文到 system prompt。"""
        session_id = None

        # 压缩 + 组装 messages（不追加新的 user 消息）
        await self.compactor.try_compact(chat_id, session_id=session_id)
        history = await self.memory.get(chat_id)

        max_messages = MAX_HISTORY_TURNS * 2
        recent_messages = list(history[-max_messages:]) if len(history) > max_messages else list(history)

        # System prompt
        cached = self._prompt_snapshot.get(session_id) if session_id else None
        if cached is not None:
            system_content = cached
        else:
            system_content = await self._build_system_prompt(chat_id)
            if session_id:
                self._prompt_snapshot.freeze(session_id, system_content)

        # 注入恢复上下文到 system prompt
        resumption_context = metadata.get("resumption_context", {})
        if resumption_context:
            user_req = resumption_context.get("user_request", "")
            remaining = resumption_context.get("remaining_description", "")
            system_content += (
                "\n\n## 恢复上下文\n\n"
                f"你刚才主动告诉 Kevin 要继续完成之前没做完的事。"
                f"他之前让你做的是：{user_req}。"
                f"还差的部分大概是：{remaining}。"
                f"现在继续做就好。"
            )

        messages = [
            {"role": "system", "content": system_content},
            *recent_messages,
        ]

        self._inject_voice_reminder(messages)

        return _ThinkCtx(
            messages=messages,
            effective_user_message="",
            approved_directory=None,
            session_id=session_id,
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
                include_skill_activation_tool=self._skill_activation_tool_enabled(),
                status_callback=status_callback,
            )
            reply = strip_internal_thinking_tags(reply)

            # ── 后处理：reply 已生成，失败不应返回"走神了" ──
            try:
                await self.memory.append(chat_id, "assistant", reply)
                logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            except Exception as post_exc:
                logger.warning(
                    "[brain] 后处理失败（回复已生成，不影响调用方）: %s",
                    post_exc, exc_info=True,
                )
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            # 内部调用（意识循环等）不应返回面向用户的 fallback，直接抛出让调用方处理
            if chat_id.startswith("__"):
                raise
            return f"出错了：{e}"

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
            metadata: 额外元数据（如 task_resumption 恢复触发信息）
            images: 图片列表，每个元素为 {"base64": str, "media_type": str} 或 {"url": str}

        Returns:
            完整回复文本（所有中间文字 + 最终文字拼接），用于记录到记忆
        """
        is_resumption = metadata is not None and metadata.get("source") == "task_resumption"

        # 通知意识引擎：对话开始（恢复触发不算用户对话）
        if self.consciousness_engine is not None and not is_resumption:
            self.consciousness_engine.on_conversation_start()

        if not is_resumption:
            events.log("conversation", "incoming",
                message=user_message[:500],
                channel=adapter,
                chat_id=chat_id,
                user_id=user_id,
            )

        # 恢复触发：不写入用户消息，不走 _prepare_think 中的用户消息写入
        if is_resumption and not user_message:
            ctx = await self._prepare_think_for_resumption(
                chat_id, metadata=metadata,
            )
        else:
            ctx = await self._prepare_think(
                chat_id, user_message, send_fn=send_fn, images=images,
                adapter=adapter, user_id=user_id,
            )
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
            text = sanitize_outgoing(text)  # 兜底过滤内部标记
            if not text:
                return
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

        async def on_interim_text(text: str, *, bypass_monologue_filter: bool = False) -> None:
            stripped = strip_internal_thinking_tags(text)
            if stripped and (bypass_monologue_filter or not _is_internal_monologue(stripped)):
                await _send_with_split(stripped)

        async def on_typing() -> None:
            if typing_fn is not None:
                try:
                    await typing_fn()
                except Exception:
                    pass

        resumption_context = metadata.get("resumption_context") if metadata else None

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
                resumption_context=resumption_context,
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

            # ── 后处理：回复已发出，失败不应再发"走神了" ──
            memory_text = strip_split_markers(full_reply)
            try:
                memory_text = "\n\n".join(parts_sent) if parts_sent else memory_text
                await self.memory.append(chat_id, "assistant", memory_text)
                logger.debug(f"[{chat_id}] 流式回复完成，片段数: {len(parts_sent)}")
                events.log("conversation", "outgoing",
                    message=memory_text[:500],
                    channel=adapter,
                    chat_id=chat_id,
                )
                # 背景自动回顾（每 N 轮用户消息后异步执行）
                if not is_resumption:
                    await self._background_reviewer.maybe_review(
                        router=self.router,
                        memory=self.memory,
                        chat_id=chat_id,
                    )
            except Exception as post_exc:
                logger.warning(
                    "[brain] 后处理失败（回复已发出，不影响用户）: %s",
                    post_exc, exc_info=True,
                )
            return memory_text

        except Exception as e:
            logger.error(f"LLM 调用失败（conversational）: {e}")
            await self.memory.remove_last(chat_id)
            error_msg = "抱歉，我刚才走神了一下。你能再说一次吗？"
            await send_fn(error_msg)
            return error_msg
        finally:
            self._schedule_conversation_end()

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

        # 1. 构建完整 system prompt（8 层：soul → rules → time → memory → facts → vectors → summaries → voice）
        system_content = await self._build_system_prompt(resolved_chat_id)

        # 2. 构造消息列表
        sense_text = ""
        if sense_context:
            sense_text = "[当前环境]\n"
            for k, v in sense_context.items():
                sense_text += f"- {k}: {v}\n"
            sense_text += "\n"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{sense_text}{context_prompt}"},
        ]

        # 3. voice.md depth-0 注入（与 think_conversational 共享同一个注入逻辑）
        self._inject_voice_reminder(messages)

        # 4. 生成回复
        if tools:
            # 有工具需求：走 TaskRuntime
            from src.core.shell_policy import extract_execution_constraints
            tool_specs = self.task_runtime.chat_tools(
                shell_enabled=False,
                web_enabled=True,
                skill_activation_enabled=False,
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

            response_text = await self.task_runtime.complete_chat(
                chat_id=resolved_chat_id,
                messages=messages,
                constraints=constraints,
                tools=tool_specs,
                deps=deps,
                adapter="",
                user_id="",
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

