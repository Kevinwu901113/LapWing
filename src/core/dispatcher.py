"""Agent 分发器 — 判断用户消息是否需要交给某个 Agent 处理。"""

import json
import logging
import re
from dataclasses import dataclass

from src.agents.base import AgentRegistry, AgentTask, AgentResult, AgentMode
from src.core.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

_WEATHER_PATTERNS = [
    r"(?:帮我|帮忙|麻烦|请|想知道|告诉我|查下|查一下|看下|看一下|问下).{0,8}(?:天气|气温|温度|风速)",
    r"[\u4e00-\u9fffA-Za-z·\-\s]{2,20}(?:今天|明天|后天|现在|当前)?(?:的)?(?:天气|气温|温度|风速)(?:怎么样|如何|呢)?",
    r"[\u4e00-\u9fffA-Za-z·\-\s]{2,20}(?:现在|今天|明天|后天)?(?:多少度|几度|冷不冷|热不热)",
]

_TODO_PATTERNS = [
    r"(?:待办|todo)",
    r"(?:添加|新增|记个|记一条|加入).{0,8}(?:任务|事项|待办)",
    r"(?:列出|查看|看看|显示).{0,8}(?:待办|todo|任务清单)",
    r"(?:完成|删除|删掉|移除).{0,8}(?:待办|todo|任务)",
    r"(?:提醒|闹钟|定时提醒|定时任务)",
    r"(?:列出|查看|看看|显示).{0,8}(?:提醒|闹钟)",
    r"(?:取消|删除|移除|关闭).{0,8}(?:提醒|闹钟)",
    r"(?:每天|每周).{0,8}(?:提醒)",
]
_SHELL_COMMAND_PATTERNS = [
    r"\b(?:ls|pwd|cat|mkdir|touch|mv|cp|chmod|chown|find|grep)\b",
    r"(?:执行|运行).{0,8}(?:命令|shell|终端)",
]

_VALID_AGENT_MODES: set[str] = {"default", "snippet", "workspace_patch"}

_DISPATCH_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": ["string", "null"],
            "description": "要调用的 agent 名称，或 null 表示不需要 agent",
        },
        "mode": {
            "type": "string",
            "enum": ["auto", "confirm", "plan"],
            "description": "执行模式",
        },
    },
    "required": ["agent"],
}


@dataclass(frozen=True)
class DispatchDecision:
    agent_name: str
    mode: AgentMode


class AgentDispatcher:
    """将用户消息路由到合适的 Agent，或返回 None 交由 Lapwing 直接回应。"""

    def __init__(self, registry: AgentRegistry, router, memory) -> None:
        self._registry = registry
        self._router = router
        self._memory = memory
        self._persona_prompt = load_prompt("lapwing_soul")

    async def try_dispatch(
        self, chat_id: str, user_message: str, *, session_id: str | None = None
    ) -> str | None:
        """尝试将用户消息分发给合适的 Agent。

        Returns:
            Agent 的回复文本（可能经过人格格式化），或 None（回退到普通对话）。
        """
        # 1. 注册表为空时直接跳过，避免任何开销
        if self._registry.is_empty():
            return None

        try:
            # 2. 分类：判断需要哪个 Agent（如果有的话）
            decision = await self._classify(chat_id, user_message)
            if decision is None:
                return None

            # 3. 查找 Agent
            agent = self._registry.get_by_name(decision.agent_name)
            if agent is None:
                logger.warning(f"Dispatcher selected unknown agent '{decision.agent_name}'")
                return None

            # 4. 从记忆中获取历史和用户画像，构建 AgentTask
            if session_id is not None:
                history = await self._memory.get_session_messages(session_id)
            else:
                history = await self._memory.get(chat_id)
            user_facts = await self._memory.get_user_facts(chat_id)
            task = AgentTask(
                chat_id=chat_id,
                user_message=user_message,
                history=history,
                user_facts=user_facts,
                mode=decision.mode,
            )

            # 5. 执行 Agent
            result: AgentResult = await agent.execute(task, self._router)

            # 6. 按需进行人格格式化
            if result.needs_persona_formatting:
                return await self._format_with_persona(chat_id, result.content)
            return result.content

        except Exception as e:
            logger.warning(f"Agent dispatch failed, falling back to normal: {e}")
            return None

    def _quick_match(self, user_message: str) -> DispatchDecision | None:
        """关键词快速匹配：仅处理 weather/todo，搜索交给主对话工具闭环。"""
        if any(re.search(pattern, user_message, flags=re.IGNORECASE) for pattern in _WEATHER_PATTERNS):
            logger.info(f"[dispatcher] 关键词快速匹配 → weather")
            return DispatchDecision(agent_name="weather", mode="default")

        if any(re.search(pattern, user_message, flags=re.IGNORECASE) for pattern in _TODO_PATTERNS):
            logger.info(f"[dispatcher] 关键词快速匹配 → todo")
            return DispatchDecision(agent_name="todo", mode="default")
        return None

    async def _classify(self, chat_id: str, user_message: str) -> DispatchDecision | None:
        """使用 LLM（tool 模型）判断用户消息是否需要 Agent 处理。

        Returns:
            DispatchDecision，或 None（由 Lapwing 直接回应）。
        """
        # 快速关键词匹配，命中则直接返回，跳过 LLM 调用
        quick = self._quick_match(user_message)
        if quick is not None:
            return quick

        if self._looks_like_shell_request(user_message):
            logger.info("[dispatcher] 检测到本地 shell 请求，保留给 Lapwing 主对话工具链")
            return None

        agents_json = json.dumps(self._registry.as_descriptions(), ensure_ascii=False)
        prompt = (
            load_prompt("agent_dispatcher")
            .replace("{available_agents}", agents_json)
            .replace("{user_message}", user_message)
        )
        try:
            result = await self._router.complete_structured(
                [{"role": "user", "content": prompt}],
                result_schema=_DISPATCH_DECISION_SCHEMA,
                result_tool_name="dispatch_decision",
                result_tool_description="决定将用户请求分派给哪个 agent",
                slot="lightweight_judgment",
                max_tokens=512,
                session_key=f"chat:{chat_id}",
                origin="core.dispatcher.classify",
            )
        except Exception:
            return None

        agent = result.get("agent")
        if not agent or not isinstance(agent, str):
            return None
        agent_name = agent.strip()
        if not agent_name:
            return None

        mode_raw = result.get("mode")
        if isinstance(mode_raw, str) and mode_raw.strip() in _VALID_AGENT_MODES:
            mode: AgentMode = mode_raw.strip()  # type: ignore[assignment]
        else:
            mode = self._default_mode_for_agent(agent_name)

        return DispatchDecision(agent_name=agent_name, mode=mode)

    def _default_mode_for_agent(self, agent_name: str) -> AgentMode:
        if agent_name == "coder":
            return "snippet"
        return "default"

    def _looks_like_shell_request(self, user_message: str) -> bool:
        text = re.sub(r"https?://\S+", "", user_message)

        if re.search(r"/(?!/)[A-Za-z0-9._~/-]+", text):
            return True

        return any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in _SHELL_COMMAND_PATTERNS
        )

    async def _format_with_persona(self, chat_id: str, content: str) -> str:
        """通过 Lapwing 的人格对原始 Agent 输出进行润色转述。"""
        persona_parts = [self._persona_prompt]  # soul
        try:
            examples = load_prompt("lapwing_examples")
            if examples:
                persona_parts.append(examples)
        except Exception:
            pass
        persona_parts.append(load_prompt("lapwing_voice"))
        persona_context = "\n\n".join(persona_parts)

        messages = [
            {"role": "system", "content": persona_context},
            {
                "role": "user",
                "content": (
                    "把以下信息用你自己的方式告诉 Kevin，像平常和他聊天一样说。"
                    "只说最关键的内容，加入你自己的反应。\n\n"
                    f"{content}"
                ),
            },
        ]
        result = await self._router.complete(
            messages,
            slot="persona_expression",
            session_key=f"chat:{chat_id}",
            origin="core.dispatcher.persona_format",
        )
        return result if result else content
