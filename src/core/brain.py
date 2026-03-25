"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter, ToolCallRequest
from src.memory.conversation import ConversationMemory
from src.memory.fact_extractor import FactExtractor
from src.tools.shell_executor import execute as execute_shell
from config.settings import MAX_HISTORY_TURNS, SHELL_DEFAULT_CWD, SHELL_ENABLED

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

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

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
                "如果需要在本机执行操作，调用 `execute_shell`。"
                "先执行、看结果，如果失败就尝试其他方法，直到完成为止。"
                "操作完成后告诉用户结果。不要伪造命令输出。"
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
                        "在当前服务器上执行一个 shell 命令，"
                        "用于查看目录、创建文件、检查环境或运行非交互式命令。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "要执行的 shell 命令。",
                            }
                        },
                        "required": ["command"],
                    },
                },
            }
        ]

    async def _execute_tool(self, tool_call: ToolCallRequest) -> str:
        """执行 tool call，将真实结果（包括错误）以 JSON 形式返回给 LLM。"""
        if tool_call.name != "execute_shell":
            return json.dumps(
                {"error": f"未知工具：{tool_call.name}"},
                ensure_ascii=False,
            )

        command = str(tool_call.arguments.get("command", "")).strip()
        if not command:
            return json.dumps({"error": "缺少 command 参数"}, ensure_ascii=False)

        result = await execute_shell(command)
        return json.dumps(
            {"command": command, **result.to_dict()},
            ensure_ascii=False,
        )

    async def _complete_chat(
        self,
        chat_id: str,
        messages: list[dict],
        user_message: str,
    ) -> str:
        """Pi Agent 风格的闭环 tool loop：执行 → 观察结果 → 继续，直到 LLM 不再调用工具。"""
        tools = self._chat_tools()
        if not tools:
            return await self.router.complete(messages, purpose="chat")

        for round_index in range(_MAX_TOOL_ROUNDS):
            turn = await self.router.complete_with_tools(
                messages,
                tools=tools,
                purpose="chat",
            )

            if not turn.tool_calls:
                return turn.text or "我这次没有整理出可回复的结果。"

            if len(turn.tool_calls) > 1:
                logger.warning(
                    f"[brain] 模型返回了 {len(turn.tool_calls)} 个 tool calls，"
                    "当前将按顺序只处理第一个。"
                )

            tool_call = turn.tool_calls[0]

            if turn.continuation_message is not None:
                messages.append(turn.continuation_message)

            result_text = await self._execute_tool(tool_call)
            messages.append(
                self.router.build_tool_result_message(
                    purpose="chat",
                    tool_results=[(tool_call, result_text)],
                )
            )
            logger.info(f"[brain] 完成第 {round_index + 1} 轮 tool call: {tool_call.name}")

        logger.warning("[brain] tool call 循环超过上限，返回兜底说明")
        return "操作步骤太多了，我先暂停一下。"

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

    async def think(self, chat_id: str, user_message: str) -> str:
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
                agent_reply = await self.dispatcher.try_dispatch(chat_id, user_message)
                if agent_reply is not None:
                    await self.memory.append(chat_id, "assistant", agent_reply)
                    return agent_reply
            except Exception as e:
                logger.warning(f"[{chat_id}] Agent dispatch failed, falling back: {e}")

        history = await self.memory.get(chat_id)
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history

        # 动态组合 system prompt（基础人格 + 用户画像 + 知识笔记）
        system_content = await self._build_system_prompt(chat_id, user_message)

        messages = [
            {"role": "system", "content": system_content},
            *recent,
        ]

        try:
            reply = await self._complete_chat(chat_id, messages, user_message)
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
