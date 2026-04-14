"""Agent 定义注册表。

每个 Agent 定义包括：
- name: Agent 标识符
- description: 给 Lapwing 看的描述（她用这个决定委派给谁）
- system_prompt_file: prompt 文件名（不含扩展名，由 prompt_loader 加载）
- capabilities: 允许使用的工具 capability 集合
- blocked_tools: 额外禁止的工具名（在全局 BLOCKED_TOOLS 之上）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt_file: str
    capabilities: frozenset[str]
    blocked_tools: frozenset[str] = field(default_factory=frozenset)


# 所有可用的 Agent 定义
AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    "researcher": AgentDefinition(
        name="researcher",
        description="信息搜集和调研专家。擅长多步搜索、网页内容提取、信息整合和摘要撰写。",
        system_prompt_file="agent_researcher",
        capabilities=frozenset({"web"}),
        # web → web_search, web_fetch
    ),
    "coder": AgentDefinition(
        name="coder",
        description="代码编写和调试专家。擅长 Python/Shell 脚本、文件操作、代码运行和测试。",
        system_prompt_file="agent_coder",
        capabilities=frozenset({"shell", "code", "file"}),
    ),
    "browser": AgentDefinition(
        name="browser",
        description="网页浏览专家。擅长导航网页、提取内容、填写表单、截图分析。",
        system_prompt_file="agent_browser",
        capabilities=frozenset({"browser", "web"}),
    ),
}


def get_agent_definition(name: str) -> AgentDefinition | None:
    """获取 Agent 定义。"""
    return AGENT_DEFINITIONS.get(name)


def list_agent_definitions() -> list[AgentDefinition]:
    """列出所有可用 Agent 定义。"""
    return list(AGENT_DEFINITIONS.values())


def agent_names() -> list[str]:
    """所有 Agent 名称。"""
    return list(AGENT_DEFINITIONS.keys())


def agent_descriptions_for_prompt() -> str:
    """生成给 Lapwing 看的 Agent 列表描述（注入 system prompt 用）。"""
    lines = []
    for agent in AGENT_DEFINITIONS.values():
        lines.append(f"- **{agent.name}**: {agent.description}")
    return "\n".join(lines)
