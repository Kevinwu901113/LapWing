"""Agent 基础框架 — 基类、数据结构、注册表。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentTask:
    """Agent 执行所需的全部上下文。"""
    chat_id: str
    user_message: str
    history: list[dict] = field(default_factory=list)
    user_facts: list[dict] = field(default_factory=list)


@dataclass
class AgentResult:
    """Agent 执行结果。"""
    content: str
    needs_persona_formatting: bool = True
    metadata: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """所有 Agent 实现的抽象基类。"""
    name: str
    description: str
    capabilities: list[str]

    @abstractmethod
    async def execute(self, task: AgentTask, router) -> AgentResult:
        """执行 Agent 任务，返回结果。"""
        ...


class AgentRegistry:
    """注册并检索 Agent 实例。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """注册一个 Agent。同名 Agent 会被覆盖。"""
        self._agents[agent.name] = agent

    def get_by_name(self, name: str) -> BaseAgent | None:
        """按名称查找 Agent，未找到返回 None。"""
        return self._agents.get(name)

    def list_all(self) -> list[BaseAgent]:
        """返回所有已注册的 Agent 列表。"""
        return list(self._agents.values())

    def is_empty(self) -> bool:
        """是否没有任何已注册的 Agent。"""
        return len(self._agents) == 0

    def as_descriptions(self) -> list[dict]:
        """序列化所有 Agent 的描述信息，供 LLM 分发 prompt 使用。"""
        return [
            {
                "name": a.name,
                "description": a.description,
                "capabilities": a.capabilities,
            }
            for a in self._agents.values()
        ]
