"""Agent 分发器 — 判断用户消息是否需要交给某个 Agent 处理。"""

import json
import logging
import re

from src.agents.base import AgentRegistry, AgentTask, AgentResult
from src.core.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

# 触发 researcher 的搜索意图关键词模式
_SEARCH_PATTERNS = [
    # 有明确前缀："帮我搜X" / "帮我查X" / "帮我找X"
    r"(?:帮我|帮忙|麻烦)(?:搜|查|找|检索)(.{2,})",
    # 有连词："搜一下X" / "查一查X"（连词存在即为明确搜索指令）
    r"(?:搜|查|找|检索)(?:索|一下|一搜|一查|一查|一找)(.{2,})",
    # 明确的搜索词汇："搜索X" / "查询X" / "搜搜X"
    r"(?:搜索|查询|查找|检索|查一查|搜一搜|搜搜)(.{2,})",
    # "X的最新/最近消息/信息/新闻"
    r"(.{2,}?)(?:的)?(?:最新|最近)(?:的)?(?:消息|新闻|信息|动态|情况|进展)",
    # "X今天/今日的行情/价格" 或 "今天X行情"
    r"(.{2,}?)(?:今天|今日|现在|当前)(?:的)?(?:行情|价格|股价|汇率|状态|情况)",
    r"(?:今天|今日|现在|当前)(.{2,}?)(?:的)?(?:行情|价格|股价|汇率|状态|情况)",
]

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


class AgentDispatcher:
    """将用户消息路由到合适的 Agent，或返回 None 交由 Lapwing 直接回应。"""

    def __init__(self, registry: AgentRegistry, router, memory) -> None:
        self._registry = registry
        self._router = router
        self._memory = memory
        self._persona_prompt = load_prompt("lapwing")

    async def try_dispatch(self, chat_id: str, user_message: str) -> str | None:
        """尝试将用户消息分发给合适的 Agent。

        Returns:
            Agent 的回复文本（可能经过人格格式化），或 None（回退到普通对话）。
        """
        # 1. 注册表为空时直接跳过，避免任何开销
        if self._registry.is_empty():
            return None

        try:
            # 2. 分类：判断需要哪个 Agent（如果有的话）
            agent_name = await self._classify(user_message)
            if agent_name is None:
                return None

            # 3. 查找 Agent
            agent = self._registry.get_by_name(agent_name)
            if agent is None:
                logger.warning(f"Dispatcher selected unknown agent '{agent_name}'")
                return None

            # 4. 从记忆中获取历史和用户画像，构建 AgentTask
            history = await self._memory.get(chat_id)
            user_facts = await self._memory.get_user_facts(chat_id)
            task = AgentTask(
                chat_id=chat_id,
                user_message=user_message,
                history=history,
                user_facts=user_facts,
            )

            # 5. 执行 Agent
            result: AgentResult = await agent.execute(task, self._router)

            # 6. 按需进行人格格式化
            if result.needs_persona_formatting:
                return await self._format_with_persona(result.content)
            return result.content

        except Exception as e:
            logger.warning(f"Agent dispatch failed, falling back to normal: {e}")
            return None

    def _quick_match(self, user_message: str) -> str | None:
        """关键词快速匹配：跳过 LLM，直接派发明确的搜索意图。"""
        if any(re.search(pattern, user_message, flags=re.IGNORECASE) for pattern in _WEATHER_PATTERNS):
            logger.info(f"[dispatcher] 关键词快速匹配 → weather")
            return "weather"

        if any(re.search(pattern, user_message, flags=re.IGNORECASE) for pattern in _TODO_PATTERNS):
            logger.info(f"[dispatcher] 关键词快速匹配 → todo")
            return "todo"

        for pattern in _SEARCH_PATTERNS:
            if re.search(pattern, user_message):
                logger.info(f"[dispatcher] 关键词快速匹配 → researcher")
                return "researcher"
        return None

    async def _classify(self, user_message: str) -> str | None:
        """使用 LLM（tool 模型）判断用户消息是否需要 Agent 处理。

        Returns:
            Agent 名称字符串，或 None（由 Lapwing 直接回应）。
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
        raw = await self._router.complete(
            [{"role": "user", "content": prompt}],
            purpose="tool",
            max_tokens=512,
        )
        return self._parse_decision(raw)

    def _parse_decision(self, raw: str) -> str | None:
        """防御性解析 LLM 返回的决策 JSON。

        Returns:
            Agent 名称字符串，或 None（解析失败或 agent 为 null）。
        """
        if raw is None:
            return None
        # 去除 markdown 代码块标记
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        agent = data.get("agent")
        if agent is None:
            return None
        if not isinstance(agent, str):
            return None
        return agent if agent.strip() else None

    def _looks_like_shell_request(self, user_message: str) -> bool:
        text = re.sub(r"https?://\S+", "", user_message)

        if re.search(r"/(?!/)[A-Za-z0-9._~/-]+", text):
            return True

        return any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in _SHELL_COMMAND_PATTERNS
        )

    async def _format_with_persona(self, content: str) -> str:
        """通过 Lapwing 的人格对原始 Agent 输出进行润色转述。"""
        messages = [
            {"role": "system", "content": self._persona_prompt},
            {"role": "user", "content": f"请用你的风格将以下内容转述给用户：\n\n{content}"},
        ]
        result = await self._router.complete(messages, purpose="chat")
        return result if result else content
