"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter
from src.core.task_runtime import RuntimeDeps, TaskRuntime
from src.core.shell_policy import (
    ExecutionSessionState,
    build_shell_runtime_policy,
    extract_execution_constraints,
)
from src.core.verifier import verify_shell_constraints_status as verify_constraints
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import execute as execute_shell
from src.tools.types import ToolExecutionRequest
from config.settings import (
    MAX_HISTORY_TURNS,
    SHELL_ALLOW_SUDO,
    SHELL_DEFAULT_CWD,
    SHELL_ENABLED,
    SKILLS_DISPATCH_TOOL_WHITELIST,
)

if TYPE_CHECKING:
    from src.core.skills import SkillDefinition, SkillManager
    from src.memory.interest_tracker import InterestTracker
    from src.core.self_reflection import SelfReflection
    from src.core.knowledge_manager import KnowledgeManager
    from src.memory.vector_store import VectorStore

logger = logging.getLogger("lapwing.brain")

_RELATED_MEMORY_LIMIT = 300
class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path):
        self.router = LLMRouter()
        self.tool_registry = build_default_tool_registry()
        self.task_runtime = TaskRuntime(router=self.router, tool_registry=self.tool_registry)
        self.memory = ConversationMemory(db_path)
        self.fact_extractor = FactExtractor(self.memory, self.router)
        self.interest_tracker: InterestTracker | None = None
        self.self_reflection: SelfReflection | None = None
        self.knowledge_manager: KnowledgeManager | None = None
        self.vector_store: VectorStore | None = None
        self.skill_manager: SkillManager | None = None
        self.event_bus = None
        self._system_prompt: str | None = None
        self.dispatcher = None  # Set externally by main.py (AgentDispatcher | None)

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
        """懒加载 system prompt（基础人格）。"""
        if self._system_prompt is None:
            self._system_prompt = load_prompt("lapwing")
            logger.info("已加载 Lapwing 人格 prompt")
        return self._system_prompt

    def reload_persona(self) -> None:
        """重新加载人格 prompt（修改 prompts/lapwing.md 后调用）。"""
        from src.core.prompt_loader import reload_prompt
        self._system_prompt = reload_prompt("lapwing")
        logger.info("已重新加载 Lapwing 人格 prompt")

    def reload_skills(self) -> None:
        if self.skill_manager is None:
            return
        self.skill_manager.reload()

    def _split_facts(self, facts: list[dict]) -> tuple[list[dict], list[dict]]:
        """将普通事实与 memory summary 分离。"""
        regular_facts: list[dict] = []
        memory_summaries: list[dict] = []
        for fact in facts:
            if str(fact.get("fact_key", "")).startswith("memory_summary_"):
                memory_summaries.append(fact)
            else:
                regular_facts.append(fact)
        return regular_facts, memory_summaries

    def _format_recent_memory_summaries(self, summaries: list[dict]) -> str:
        """格式化最近聊过的事摘要。"""
        latest = sorted(
            summaries,
            key=lambda item: str(item.get("fact_key", "")),
            reverse=True,
        )[:3]
        return "\n".join(
            f"- {item['fact_key'].removeprefix('memory_summary_')}: {item['fact_value']}"
            for item in latest
        )

    def _summary_dates(self, summaries: list[dict]) -> set[str]:
        return {
            str(item.get("fact_key", "")).removeprefix("memory_summary_")
            for item in summaries
            if str(item.get("fact_key", "")).startswith("memory_summary_")
        }

    def _truncate_related_memory(self, text: str) -> str:
        stripped = text.strip()
        if len(stripped) <= _RELATED_MEMORY_LIMIT:
            return stripped
        return stripped[: _RELATED_MEMORY_LIMIT - 3].rstrip() + "..."

    def _format_related_history_hits(
        self,
        hits: list[dict],
        existing_dates: set[str],
    ) -> str:
        lines: list[str] = []
        for hit in hits:
            metadata = hit.get("metadata") or {}
            text = self._truncate_related_memory(str(hit.get("text", "")))
            if not text:
                continue

            date_str = str(metadata.get("date", "")).strip()
            if date_str and date_str in existing_dates:
                continue

            if date_str:
                lines.append(f"- {date_str}: {text}")
            else:
                lines.append(f"- {text}")

        return "\n".join(lines)

    def _tool_runtime_instruction(self) -> str:
        if SHELL_ENABLED:
            return (
                "## 本地执行规则\n\n"
                "你拥有 execute_shell、read_file、write_file 工具，可以在当前服务器上执行真实的操作。\n\n"
                "### 执行原则\n"
                "- 用户要求你做任何涉及文件、命令、系统操作的事情时，**立刻调用工具去做**，不要先回复文字再等下一轮\n"
                "- **绝对不要**把选择题抛给用户。遇到问题（比如权限不够）自己判断最合理的替代方案直接执行\n"
                "- **绝对不要**伪造命令输出。你必须真正调用工具，用真实结果回复\n"
                "- 如果遇到权限错误（Permission denied），**立刻用 `sudo` 重试同一条命令**，不要询问用户\n"
                "- 如果一个命令失败了，分析错误原因，换一种方式重试，直到完成为止\n"
                "- 复杂任务需要多个步骤时，连续调用多次工具一口气做完\n"
                "- 做完后简短告知结果：'搞定了，文件在 /home/xxx。' 不需要列出你执行的每一条命令\n\n"
                "### 禁止行为\n"
                "- 禁止回复 '有两个方案：1. xxx 2. xxx，你选哪个？'\n"
                "- 禁止回复 '我来帮你检查一下' 然后就没有下文了\n"
                "- 禁止回复 '遇到了权限问题，你想怎么处理？' —— 自己换路径解决\n"
                "- 禁止在回复中展示命令代码块但不实际执行\n\n"
                f"当前工作目录：{SHELL_DEFAULT_CWD}\n"
                f"当前用户：可用 whoami 确认\n"
            )

        return (
            "## 本地执行规则\n\n"
            "本地 shell 执行当前已禁用。"
            "如果用户要求你在当前机器上执行命令或修改本地文件，"
            "必须明确说明执行功能当前关闭，不能编造结果。"
        )

    async def _complete_chat(
        self,
        chat_id: str,
        messages: list[dict],
        user_message: str,
        approved_directory: str | None = None,
        include_skill_activation_tool: bool = False,
        status_callback=None,
    ) -> str:
        constraints = extract_execution_constraints(
            user_message,
            approved_directory=approved_directory,
        )
        tools = self.task_runtime.chat_tools(
            shell_enabled=SHELL_ENABLED,
            skill_activation_enabled=include_skill_activation_tool,
        )
        services = {}
        if include_skill_activation_tool and self.skill_manager is not None:
            services["skill_manager"] = self.skill_manager

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
        )

    async def _build_system_prompt(self, chat_id: str, user_message: str = "") -> str:
        """组合基础人格 prompt、用户画像信息和相关知识笔记。"""
        base = self.system_prompt
        facts = await self.memory.get_user_facts(chat_id)
        sections = [base]
        summary_dates: set[str] = set()

        if facts:
            regular_facts, memory_summaries = self._split_facts(facts)
            summary_dates = self._summary_dates(memory_summaries)

            if regular_facts:
                facts_text = "\n".join(
                    f"- {fact['fact_key']}: {fact['fact_value']}" for fact in regular_facts
                )
                sections.append(
                    "## 你对这个用户的了解\n\n"
                    "以下是你从之前对话中了解到的关于这个用户的信息。"
                    "在合适的时候可以自然地引用，但不要刻意提起。\n\n"
                    f"{facts_text}"
                )

            if memory_summaries:
                summaries_text = self._format_recent_memory_summaries(memory_summaries)
                sections.append(
                    "## 最近聊过的事\n\n"
                    "以下是你们最近几次对话的重要脉络。"
                    "当用户延续之前的话题时，可以自然接上。\n\n"
                    f"{summaries_text}"
                )

        if user_message and self.vector_store is not None:
            try:
                hits = await self.vector_store.search(chat_id, user_message, n_results=2)
            except Exception as exc:
                logger.warning(f"[{chat_id}] 检索相关历史记忆失败: {exc}")
            else:
                related_text = self._format_related_history_hits(hits, summary_dates)
                if related_text:
                    sections.append(
                        "## 相关历史记忆\n\n"
                        "以下是通过语义检索找到的相关历史片段。"
                        "仅当它确实能帮助当前回复时再自然引用。\n\n"
                        f"{related_text}"
                    )

        # 注入相关知识笔记
        if user_message and self.knowledge_manager is not None:
            notes = self.knowledge_manager.get_relevant_notes(user_message)
            if notes:
                notes_text = "\n\n".join(
                    f"### {note['topic']}\n{note['content']}"
                    for note in notes
                )
                sections.append(
                    "## 你积累的相关知识\n\n"
                    "以下是你之前浏览网页时记录的笔记，与当前话题可能相关。"
                    "如果对话中用到了，可以自然地引用或补充。\n\n"
                    f"{notes_text}"
                )

        if self.skill_manager is not None and self.skill_manager.has_model_visible_skills():
            skills_catalog = self.skill_manager.render_catalog_for_prompt()
            if skills_catalog:
                sections.append(
                    "## 可用技能目录\n\n"
                    "以下是当前可用的技能，你可以在确实需要时调用 `activate_skill` 按需加载。\n\n"
                    f"{skills_catalog}"
                )

        if user_message:
            sections.append(self._tool_runtime_instruction())

        return "\n\n".join(sections)

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

    async def think(self, chat_id: str, user_message: str, status_callback=None) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户发送的消息

        Returns:
            Lapwing 的回复文本
        """
        await self.memory.append(chat_id, "user", user_message)

        # 通知提取器有新消息（异步触发轮次/空闲计时逻辑）
        self.fact_extractor.notify(chat_id)
        if self.interest_tracker is not None:
            self.interest_tracker.notify(chat_id)

        effective_user_message, approved_directory, immediate_reply = (
            self.task_runtime.resolve_pending_confirmation(chat_id, user_message)
        )
        if immediate_reply is not None:
            await self.memory.append(chat_id, "assistant", immediate_reply)
            return immediate_reply

        # 实时纠正检测：异步触发自省，不阻塞主回复流程
        if self.self_reflection is not None:
            from src.core.self_reflection import is_correction
            if is_correction(user_message):
                import asyncio
                history = await self.memory.get(chat_id)
                asyncio.create_task(
                    self.self_reflection.reflect_on_correction(
                        chat_id, user_message, list(history)
                    )
                )

        # Try agent dispatch first
        if self.dispatcher is not None:
            try:
                agent_reply = await self.dispatcher.try_dispatch(chat_id, effective_user_message)
                if agent_reply is not None:
                    await self.memory.append(chat_id, "assistant", agent_reply)
                    return agent_reply
            except Exception as e:
                logger.warning(f"[{chat_id}] Agent dispatch failed, falling back: {e}")

        history = await self.memory.get(chat_id)
        recent_messages = self._recent_messages(
            history,
            user_message=effective_user_message,
            original_user_message=user_message,
        )

        # 动态组合 system prompt（基础人格 + 用户画像 + 知识笔记）
        system_content = await self._build_system_prompt(chat_id, effective_user_message)

        messages = [
            {"role": "system", "content": system_content},
            *recent_messages,
        ]

        try:
            reply = await self._complete_chat(
                chat_id,
                messages,
                effective_user_message,
                approved_directory=approved_directory,
                include_skill_activation_tool=self._skill_activation_tool_enabled(),
                status_callback=status_callback,
            )
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
