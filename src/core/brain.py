"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import json
import logging
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter, ToolCallRequest
from src.core.shell_policy import (
    AlternativeProposal,
    ExecutionSessionState,
    PendingShellConfirmation,
    analyze_command,
    build_followup_message,
    extract_execution_constraints,
    failure_type_from_result,
    infer_permission_denied_alternative,
    is_confirmation_message,
    is_rejection_message,
    should_request_consent_for_command,
    should_validate_after_success,
    verify_constraints,
)
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from src.tools.shell_executor import ShellResult, execute as execute_shell
from config.settings import MAX_HISTORY_TURNS, SHELL_ALLOW_SUDO, SHELL_DEFAULT_CWD, SHELL_ENABLED

if TYPE_CHECKING:
    from src.memory.interest_tracker import InterestTracker
    from src.core.self_reflection import SelfReflection
    from src.core.knowledge_manager import KnowledgeManager
    from src.memory.vector_store import VectorStore

logger = logging.getLogger("lapwing.brain")

_RELATED_MEMORY_LIMIT = 300
_MAX_TOOL_ROUNDS = 8


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path):
        self.router = LLMRouter()
        self.memory = ConversationMemory(db_path)
        self.fact_extractor = FactExtractor(self.memory, self.router)
        self.interest_tracker: InterestTracker | None = None
        self.self_reflection: SelfReflection | None = None
        self.knowledge_manager: KnowledgeManager | None = None
        self.vector_store: VectorStore | None = None
        self.event_bus = None
        self._system_prompt: str | None = None
        self.dispatcher = None  # Set externally by main.py (AgentDispatcher | None)
        self._pending_shell_confirmations: dict[str, PendingShellConfirmation] = {}

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    async def clear_short_term_memory(self, chat_id: str) -> None:
        """仅清除短期对话记忆。"""
        self._pending_shell_confirmations.pop(chat_id, None)
        await self.memory.clear(chat_id)

    async def clear_all_memory(self, chat_id: str) -> None:
        """清除指定 chat 的长短期记忆。"""
        self._pending_shell_confirmations.pop(chat_id, None)
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

    def _chat_tools(self) -> list[dict]:
        if not SHELL_ENABLED:
            return []

        return [
            {
                "type": "function",
                "function": {
                    "name": "execute_shell",
                    "description": (
                        "在服务器上执行 shell 命令。"
                        "用于创建文件/目录、查看文件内容、安装软件、运行脚本等任何命令行操作。"
                        "遇到权限问题时自动尝试替代路径，不要询问用户。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "要执行的 shell 命令",
                            }
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取服务器上的文件内容。用于查看配置文件、日志、代码等。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "文件的绝对路径",
                            }
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "将内容写入文件。如果文件不存在会自动创建，包括必要的父目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "文件的绝对路径",
                            },
                            "content": {
                                "type": "string",
                                "description": "要写入的内容",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
        ]

    def _with_shell_state_context(
        self,
        messages: list[dict],
        state: ExecutionSessionState,
    ) -> list[dict]:
        needs_context = (
            state.constraints.is_write_request
            or state.constraints.has_hard_path_constraints
            or bool(state.failure_reason)
            or state.consent_required
        )
        if not needs_context:
            return messages

        state_content = state.as_system_context()
        if messages and messages[0].get("role") == "system":
            merged_system = dict(messages[0])
            base_content = str(merged_system.get("content", "")).strip()
            merged_system["content"] = (
                f"{base_content}\n\n{state_content}" if base_content else state_content
            )
            return [merged_system, *messages[1:]]
        state_message = {"role": "system", "content": state_content}
        return [state_message, *messages]

    def _resolve_pending_confirmation(
        self,
        chat_id: str,
        user_message: str,
    ) -> tuple[str, str | None, str | None]:
        pending = self._pending_shell_confirmations.get(chat_id)
        if pending is None:
            return user_message, None, None

        if (
            is_confirmation_message(user_message)
            or pending.alternative_directory in user_message
        ):
            self._pending_shell_confirmations.pop(chat_id, None)
            return (
                build_followup_message(pending),
                pending.alternative_directory,
                None,
            )

        if is_rejection_message(user_message):
            self._pending_shell_confirmations.pop(chat_id, None)
            return (
                user_message,
                None,
                "好，我先不改到那个替代位置。原请求还没有完成。",
            )

        self._pending_shell_confirmations.pop(chat_id, None)
        return user_message, None, None

    def _record_pending_confirmation(
        self,
        chat_id: str,
        state: ExecutionSessionState,
    ) -> str:
        alternative = state.alternative
        if alternative is None:
            return state.failure_message()

        self._pending_shell_confirmations[chat_id] = PendingShellConfirmation(
            original_user_message=state.constraints.original_user_message,
            alternative_directory=alternative.directory,
            reason=state.failure_reason or alternative.reason,
        )
        return state.consent_message()

    def _shell_failure_reason(self, result: ShellResult) -> str:
        # 被主动拦截（blocked/timed_out）时直接使用 reason
        if result.reason and (result.blocked or result.timed_out):
            return result.reason

        # 优先使用 stderr，比 shell_executor 生成的通用 reason 更有用
        stderr = result.stderr.strip()
        if stderr:
            return stderr

        if result.reason:
            return result.reason

        if result.timed_out:
            return "命令执行超时了。"

        if result.return_code != 0:
            return f"命令执行失败，退出码 {result.return_code}。"

        return "命令执行失败了。"

    async def _execute_tool_call(
        self,
        tool_call: ToolCallRequest,
        state: ExecutionSessionState,
    ) -> tuple[str, dict]:
        payload: dict

        if tool_call.name == "read_file":
            path = str(tool_call.arguments.get("path", "")).strip()
            if not path:
                payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
                return json.dumps(payload, ensure_ascii=False), payload
            result = await execute_shell(f"cat {shlex.quote(path)}")
            payload = {"path": path, **result.to_dict()}
            return json.dumps(payload, ensure_ascii=False), payload

        if tool_call.name == "write_file":
            path = str(tool_call.arguments.get("path", "")).strip()
            content = str(tool_call.arguments.get("content", ""))
            if not path:
                payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
                return json.dumps(payload, ensure_ascii=False), payload
            dir_cmd = f"mkdir -p $(dirname {shlex.quote(path)})"
            await execute_shell(dir_cmd)
            write_cmd = f"cat > {shlex.quote(path)} << 'LAPWING_EOF'\n{content}\nLAPWING_EOF"
            result = await execute_shell(write_cmd)
            payload = {"path": path, "action": "written", **result.to_dict()}
            return json.dumps(payload, ensure_ascii=False), payload

        if tool_call.name != "execute_shell":
            state.record_failure(f"未知工具：{tool_call.name}", "blocked")
            payload = {
                "command": "",
                "stdout": "",
                "stderr": "",
                "return_code": -1,
                "timed_out": False,
                "blocked": True,
                "reason": f"未知工具：{tool_call.name}",
                "cwd": SHELL_DEFAULT_CWD,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
            return json.dumps(payload, ensure_ascii=False), payload

        command = str(tool_call.arguments.get("command", "")).strip()
        if not command:
            state.record_failure("工具参数缺少 command。", "blocked")
            payload = {
                "command": "",
                "stdout": "",
                "stderr": "",
                "return_code": -1,
                "timed_out": False,
                "blocked": True,
                "reason": "工具参数缺少 command。",
                "cwd": SHELL_DEFAULT_CWD,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
            return json.dumps(payload, ensure_ascii=False), payload

        intent = analyze_command(command)
        state.record_intent(intent)

        proposal = should_request_consent_for_command(
            state.constraints,
            intent,
            state,
        )
        if proposal is not None:
            state.require_consent(proposal)
            target_directory = state.constraints.active_directory or state.constraints.target_directory
            reason = (
                f"这条命令会把目标从 `{target_directory}` 改到 "
                f"`{proposal.directory}`，需要先征求用户同意。"
            )
            if not state.failure_reason:
                state.record_failure(reason, "requires_consent")
            payload = {
                "command": command,
                "stdout": "",
                "stderr": "",
                "return_code": -1,
                "timed_out": False,
                "blocked": True,
                "reason": reason,
                "cwd": SHELL_DEFAULT_CWD,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
            return json.dumps(payload, ensure_ascii=False), payload

        result: ShellResult = await execute_shell(command)
        failure_type = failure_type_from_result(result)
        if failure_type is not None:
            state.record_failure(self._shell_failure_reason(result), failure_type)
            # 权限拒绝时推断替代路径；如果 sudo 可用，让 LLM 自行决定是否 sudo，不打断用户
            if (
                failure_type == "permission_denied"
                and not state.consent_required
                and state.constraints.target_directory is not None
                and not SHELL_ALLOW_SUDO
            ):
                alt = infer_permission_denied_alternative(state.constraints)
                if alt is not None:
                    state.require_consent(AlternativeProposal(
                        directory=alt,
                        reason=state.failure_reason,
                        blocked_command=command,
                    ))
        elif should_validate_after_success(state.constraints, intent, result):
            verification = verify_constraints(state.constraints)
            if verification.completed:
                state.mark_completed(verification)
            else:
                state.record_failure(verification.reason, "verification_failed")

        payload = {
            "command": command,
            **result.to_dict(),
        }
        return json.dumps(payload, ensure_ascii=False), payload

    def _tool_fallback_reply(self, payload: dict | None) -> str:
        if not payload:
            return "我这次没有整理出可回复的结果。"

        command = str(payload.get("command", "")).strip()
        if payload.get("blocked"):
            return f"本地命令没有执行。{payload.get('reason', '命令被拦截了。')}"

        if payload.get("timed_out"):
            if command:
                return f"本地命令执行超时了：`{command}`。"
            return "本地命令执行超时了。"

        return_code = int(payload.get("return_code", -1))
        if return_code != 0:
            stderr = str(payload.get("stderr", "")).strip()
            if stderr:
                return f"命令执行失败，退出码 {return_code}。\n\n```\n{stderr}\n```"
            return f"命令执行失败，退出码 {return_code}。"

        stdout = str(payload.get("stdout", "")).strip()
        if stdout:
            return f"命令已经执行完了，输出是：\n\n```\n{stdout}\n```"

        return "命令已经执行完了，但没有输出。"

    async def _complete_chat(
        self,
        chat_id: str,
        messages: list[dict],
        user_message: str,
        approved_directory: str | None = None,
        status_callback=None,
    ) -> str:
        tools = self._chat_tools()
        if not tools:
            return await self.router.complete(messages, purpose="chat")

        state = ExecutionSessionState(
            constraints=extract_execution_constraints(
                user_message,
                approved_directory=approved_directory,
            )
        )
        last_payload: dict | None = None
        for round_index in range(_MAX_TOOL_ROUNDS):
            turn = await self.router.complete_with_tools(
                self._with_shell_state_context(messages, state),
                tools=tools,
                purpose="chat",
            )

            if not turn.tool_calls:
                if state.consent_required:
                    return self._record_pending_confirmation(chat_id, state)
                if state.completed:
                    return state.success_message()
                if state.constraints.is_write_request and state.constraints.objective != "generic":
                    return state.failure_message()
                return turn.text or self._tool_fallback_reply(last_payload)

            if len(turn.tool_calls) > 1:
                logger.warning(
                    f"[brain] 模型返回了 {len(turn.tool_calls)} 个 tool calls，"
                    "当前将按顺序只处理第一个。"
                )

            tool_call = turn.tool_calls[0]

            if turn.continuation_message is not None:
                messages.append(turn.continuation_message)

            if status_callback and round_index >= 1:
                try:
                    await status_callback(chat_id, f"第 {round_index} 步完成，继续处理中...")
                except Exception:
                    pass

            tool_result_text, last_payload = await self._execute_tool_call(
                tool_call,
                state=state,
            )
            if state.consent_required:
                return self._record_pending_confirmation(chat_id, state)
            messages.append(
                self.router.build_tool_result_message(
                    purpose="chat",
                    tool_results=[(tool_call, tool_result_text)],
                )
            )
            logger.info(f"[brain] 完成第 {round_index + 1} 轮 tool call: {tool_call.name}")
            if state.completed:
                return state.success_message()

        logger.warning("[brain] tool call 循环超过上限，返回兜底说明")
        if state.consent_required:
            return self._record_pending_confirmation(chat_id, state)
        if state.completed:
            return state.success_message()
        if state.constraints.is_write_request and state.constraints.objective != "generic":
            return state.failure_message()
        return self._tool_fallback_reply(last_payload)

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

        if user_message:
            sections.append(self._tool_runtime_instruction())

        return "\n\n".join(sections)

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
            self._resolve_pending_confirmation(chat_id, user_message)
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
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history
        recent_messages = [dict(item) for item in recent]
        if effective_user_message != user_message:
            if recent_messages and recent_messages[-1].get("role") == "user":
                recent_messages[-1]["content"] = effective_user_message
            else:
                recent_messages.append({"role": "user", "content": effective_user_message})

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
                status_callback=status_callback,
            )
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
