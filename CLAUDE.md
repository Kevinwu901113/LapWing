# Lapwing Wave 1 — 工具体系重构 + 记忆增强 蓝图

> 本文档是完整的实现规范，可直接交给 Claude Code 执行。
> 所有设计基于 Claude Code 泄露源码的架构模式，适配到 Lapwing 的 Python + MiniMax/GLM 技术栈。
> 优先级：统一 Tool 接口 → 自动记忆提取 → Memory CRUD → 自主调度

---

## 目录

1. [改造目标](#一改造目标)
2. [Part A：统一 Tool 接口](#二part-a统一-tool-接口)
3. [Part B：Memory CRUD 增强](#三part-b-memory-crud-增强)
4. [Part C：自动记忆提取](#四part-c自动记忆提取)
5. [Part D：自主调度 (CronTool)](#五part-d自主调度)
6. [Part E：Feature Flags](#六part-efeature-flags)
7. [集成改动：brain.py](#七集成改动brainpy)
8. [部署顺序](#八部署顺序)
9. [测试清单](#九测试清单)
10. [文件清单](#十文件清单)

---

## 一、改造目标

### 现在的问题

1. **工具定义散落各处**——tool schema 在 brain.py 里硬编码，tool handler 在不同文件，没有统一接口，新增工具需要改多个地方
2. **memory_note 只能追加**——Lapwing 无法编辑或删除过时的记忆，记忆文件只增不减
3. **记忆完全依赖 LLM 主动调用**——如果 Lapwing 忘了调 memory_note，重要信息就丢了
4. **没有自主调度能力**——Lapwing 不能自己安排"每晚自省"或"每天看新闻"，全靠 heartbeat 硬编码触发

### 改造后

1. 所有工具统一继承 `BaseTool`，新增工具只需写一个文件
2. Lapwing 可以查看、编辑、删除、搜索自己的记忆
3. 每次对话结束后自动提取值得记住的信息
4. Lapwing 可以用 `schedule_task` 工具安排自己的定时任务

---

## 二、Part A：统一 Tool 接口

### 设计来源

Claude Code 的 `Tool.ts`：每个工具都是自包含模块，定义 `name` / `description` / `inputSchema` / `permissionModel` / `execute()`。

### 新增文件：`src/tools/base.py`

```python
"""统一工具基类。

所有 Lapwing 工具都继承这个基类。
工具注册表自动收集所有 BaseTool 子类，生成 OpenAI-compatible tool schema。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PermissionLevel(Enum):
    """工具权限等级。

    SAFE: 只读操作，自动执行（读记忆、查天气）
    MODERATE: 写操作，执行并记录日志（写记忆、创建文件）
    DANGEROUS: 需要宪法检查（执行系统命令、修改重要文件）
    FORBIDDEN: 绝不允许（修改宪法、删除核心身份文件）
    """
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


class ToolResult:
    """工具执行结果。"""

    def __init__(
        self,
        success: bool,
        output: str,
        metadata: dict[str, Any] | None = None,
    ):
        self.success = success
        self.output = output
        self.metadata = metadata or {}

    def to_str(self) -> str:
        """给 LLM 看的纯文本结果。"""
        if self.success:
            return self.output
        return f"[错误] {self.output}"


class BaseTool(ABC):
    """所有 Lapwing 工具的基类。

    子类必须实现:
        name: str              — 工具名称（LLM 调用时使用）
        description: str       — 工具描述（LLM 判断何时使用）
        parameters: dict       — JSON Schema 格式的参数定义
        execute(**kwargs)       — 实际执行逻辑

    可选覆盖:
        permission_level       — 默认 SAFE
        is_read_only           — 默认 True
        tags                   — 用于 ToolSearch 的标签
    """

    name: str
    description: str
    parameters: dict  # JSON Schema，直接塞进 OpenAI tools 格式

    permission_level: PermissionLevel = PermissionLevel.SAFE
    is_read_only: bool = True
    tags: list[str] = []

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具逻辑。kwargs 来自 LLM 的 function call arguments。"""
        ...

    def to_openai_schema(self) -> dict:
        """生成 OpenAI-compatible tool definition。

        直接给 MiniMax/GLM 的 tools 参数使用。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

### 新增文件：`src/tools/registry.py`

```python
"""工具注册表。

自动收集所有 BaseTool 子类，提供：
- 按名称查找工具
- 生成完整 tools schema（给 LLM API）
- 权限检查
- 工具搜索（ToolSearch 用）
"""

from __future__ import annotations

import logging
from typing import Optional

from src.tools.base import BaseTool, PermissionLevel, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表。brain.py 持有一个全局实例。"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具。"""
        if tool.name in self._tools:
            logger.warning(f"Tool '{tool.name}' already registered, overwriting")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def get(self, name: str) -> Optional[BaseTool]:
        """按名称获取工具。"""
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        """返回所有已注册工具。"""
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict]:
        """生成所有工具的 OpenAI-compatible schema 列表。

        直接传给 LLM API 的 tools= 参数。
        """
        return [tool.to_openai_schema() for tool in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """执行指定工具。

        包含权限检查 + 异常捕获。
        brain.py 的 tool call handler 调用这个方法。
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(success=False, output=f"未知工具: {tool_name}")

        # 权限检查
        if tool.permission_level == PermissionLevel.FORBIDDEN:
            return ToolResult(
                success=False,
                output=f"工具 {tool_name} 被禁止使用。"
            )

        if tool.permission_level == PermissionLevel.DANGEROUS:
            # TODO: 接入 ConstitutionGuard 做宪法检查
            logger.warning(
                f"Dangerous tool invoked: {tool_name}, args={arguments}"
            )

        # 执行
        try:
            result = await tool.execute(**arguments)
            logger.info(
                f"Tool {tool_name} executed: success={result.success}"
            )
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return ToolResult(success=False, output=f"工具执行出错: {e}")

    def search(self, query: str) -> list[tuple[str, str]]:
        """搜索匹配的工具。返回 [(name, description), ...]。

        用于 ToolSearchTool 和未来的 Skill 系统。
        """
        query_lower = query.lower()
        results = []
        for tool in self._tools.values():
            score = 0
            if query_lower in tool.name.lower():
                score += 3
            if query_lower in tool.description.lower():
                score += 2
            for tag in tool.tags:
                if query_lower in tag.lower():
                    score += 1
            if score > 0:
                results.append((score, tool.name, tool.description))
        results.sort(key=lambda x: x[0], reverse=True)
        return [(name, desc) for _, name, desc in results]
```

### 迁移现有工具

在 `src/tools/` 目录下为每个现有工具创建独立文件。**不破坏现有行为**，只是把散落的定义集中起来。

#### `src/tools/memory_note.py`（迁移现有 memory_note）

```python
"""memory_note 工具 — 迁移自 brain.py 中的硬编码定义。"""

from src.tools.base import BaseTool, PermissionLevel, ToolResult


class MemoryNoteTool(BaseTool):
    name = "memory_note"
    description = (
        "记录一条重要信息到长期记忆中。"
        "用于：Kevin 提到的个人事实、重要决定、值得记住的对话内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["kevin_fact", "decision", "knowledge", "interest", "todo"],
                "description": "记忆分类",
            },
            "content": {
                "type": "string",
                "description": "要记录的内容",
            },
        },
        "required": ["category", "content"],
    }
    permission_level = PermissionLevel.MODERATE
    is_read_only = False
    tags = ["memory", "note", "记忆", "记录"]

    def __init__(self, memory_manager):
        self._memory = memory_manager

    async def execute(self, category: str, content: str) -> ToolResult:
        try:
            await self._memory.add_note(category, content)
            return ToolResult(success=True, output=f"已记录: [{category}] {content}")
        except Exception as e:
            return ToolResult(success=False, output=str(e))
```

#### `src/tools/web_search.py`（迁移模板 — 类似结构）

```python
"""web_search 工具。"""

from src.tools.base import BaseTool, PermissionLevel, ToolResult


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "搜索互联网获取最新信息。用于：查新闻、查事实、查比赛、查任何需要最新数据的内容。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
        },
        "required": ["query"],
    }
    permission_level = PermissionLevel.SAFE
    is_read_only = True
    tags = ["search", "web", "搜索", "查询"]

    def __init__(self, search_engine):
        self._search = search_engine

    async def execute(self, query: str) -> ToolResult:
        # 调用现有的搜索实现
        results = await self._search.search(query)
        return ToolResult(success=True, output=results)
```

**其他工具同理迁移**：`web_browse`, `execute_command`, `create_file`, `set_reminder` 等。每个工具一个文件，都继承 `BaseTool`。

#### `src/tools/__init__.py`

```python
"""工具包初始化 — 注册所有内置工具。"""

from src.tools.registry import ToolRegistry

# 各工具类的 import 在 create_default_registry() 中按需导入，
# 避免循环依赖。


def create_default_registry(
    memory_manager,
    search_engine,
    browser,
    # ... 其他依赖注入
) -> ToolRegistry:
    """创建并返回预注册了所有内置工具的 ToolRegistry。

    在 main.py 或 Brain.__init__ 中调用一次。
    """
    from src.tools.memory_note import MemoryNoteTool
    from src.tools.web_search import WebSearchTool
    from src.tools.memory_crud import (
        MemoryReadTool,
        MemoryEditTool,
        MemoryDeleteTool,
        MemoryListTool,
        MemorySearchTool,
    )
    # from src.tools.web_browse import WebBrowseTool
    # from src.tools.execute_command import ExecuteCommandTool
    # from src.tools.create_file import CreateFileTool
    # from src.tools.set_reminder import SetReminderTool
    # from src.tools.schedule_task import ScheduleTaskTool  # Part D

    registry = ToolRegistry()

    registry.register(MemoryNoteTool(memory_manager))
    registry.register(WebSearchTool(search_engine))

    # Memory CRUD (Part B)
    registry.register(MemoryReadTool())
    registry.register(MemoryEditTool())
    registry.register(MemoryDeleteTool())
    registry.register(MemoryListTool())
    registry.register(MemorySearchTool())

    # 迁移其他现有工具 — 每个取消注释并传入依赖
    # registry.register(WebBrowseTool(browser))
    # registry.register(ExecuteCommandTool())
    # registry.register(CreateFileTool())
    # registry.register(SetReminderTool(scheduler))

    return registry
```

---

## 三、Part B: Memory CRUD 增强

### 设计来源

Claude Code 的 Memory Tool (`memory_20250818`)：view / create / str_replace / insert / delete / rename。Lapwing 适配版本，操作 `data/memory/` 目录。

### 新增文件：`src/tools/memory_crud.py`

```python
"""Memory CRUD 工具集 — 让 Lapwing 能查看、编辑、删除、搜索自己的记忆。

设计原则（来自 Claude Code Memory Tool）：
- 文件是 source of truth
- Lapwing 自己决定记忆的结构和组织方式
- 宪法保护的文件（data/identity/）不可修改
"""

from __future__ import annotations

import os
from pathlib import Path

from config import settings
from src.tools.base import BaseTool, PermissionLevel, ToolResult

# 安全边界：只允许操作这些目录
ALLOWED_DIRS = [
    Path(settings.DATA_DIR) / "memory",
    Path(settings.DATA_DIR) / "evolution",
]

# 绝对禁止操作的目录
FORBIDDEN_DIRS = [
    Path(settings.DATA_DIR) / "identity",
]


def _validate_path(path_str: str) -> tuple[bool, Path | str]:
    """路径安全验证。防止目录遍历攻击。

    Returns:
        (True, resolved_path) 或 (False, error_message)
    """
    try:
        resolved = Path(path_str).resolve()
    except Exception:
        return False, f"无效路径: {path_str}"

    # 检查是否在禁止目录中
    for forbidden in FORBIDDEN_DIRS:
        try:
            resolved.relative_to(forbidden.resolve())
            return False, f"不能操作身份文件。这受宪法保护。"
        except ValueError:
            pass  # 不在禁止目录中，继续

    # 检查是否在允许目录中
    for allowed in ALLOWED_DIRS:
        try:
            resolved.relative_to(allowed.resolve())
            return True, resolved
        except ValueError:
            pass

    return False, f"只能操作 data/memory/ 或 data/evolution/ 中的文件。"


class MemoryListTool(BaseTool):
    name = "memory_list"
    description = (
        "列出记忆目录中的所有文件。"
        "用于了解自己记住了什么、记忆是怎么组织的。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "要列出的子目录，默认为整个记忆目录。例如: 'kevin_fact' 或 'knowledge'",
                "default": "",
            },
        },
    }
    permission_level = PermissionLevel.SAFE
    is_read_only = True
    tags = ["memory", "list", "记忆", "查看"]

    async def execute(self, directory: str = "") -> ToolResult:
        base = Path(settings.DATA_DIR) / "memory"
        target = base / directory if directory else base

        ok, result = _validate_path(str(target))
        if not ok:
            return ToolResult(success=False, output=result)

        if not target.exists():
            return ToolResult(success=False, output=f"目录不存在: {directory}")

        entries = []
        for item in sorted(target.rglob("*")):
            if item.is_file():
                rel = item.relative_to(base)
                size = item.stat().st_size
                size_str = f"{size}B" if size < 1024 else f"{size/1024:.1f}KB"
                entries.append(f"  {size_str}\t{rel}")

        if not entries:
            return ToolResult(success=True, output="(记忆目录为空)")

        header = f"记忆文件 ({len(entries)} 个):\n"
        return ToolResult(success=True, output=header + "\n".join(entries))


class MemoryReadTool(BaseTool):
    name = "memory_read"
    description = (
        "读取一个记忆文件的内容。"
        "用于回顾之前记录的信息、检查记忆是否准确。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于 data/memory/）。例如: 'kevin_fact/preferences.md'",
            },
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.SAFE
    is_read_only = True
    tags = ["memory", "read", "记忆", "读取"]

    async def execute(self, path: str) -> ToolResult:
        full_path = Path(settings.DATA_DIR) / "memory" / path

        ok, result = _validate_path(str(full_path))
        if not ok:
            return ToolResult(success=False, output=result)

        if not full_path.exists():
            return ToolResult(success=False, output=f"文件不存在: {path}")

        content = full_path.read_text(encoding="utf-8")

        # 添加行号（Claude Code Memory Tool 的做法）
        lines = content.split("\n")
        numbered = "\n".join(
            f"{i+1:>4}\t{line}" for i, line in enumerate(lines)
        )
        return ToolResult(
            success=True,
            output=f"=== {path} ===\n{numbered}"
        )


class MemoryEditTool(BaseTool):
    name = "memory_edit"
    description = (
        "编辑一个记忆文件中的内容（查找并替换）。"
        "用于更新过时的信息、纠正错误。"
        "old_text 必须精确匹配文件中的内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于 data/memory/）",
            },
            "old_text": {
                "type": "string",
                "description": "要替换的原文（必须精确匹配）",
            },
            "new_text": {
                "type": "string",
                "description": "替换后的新文本",
            },
        },
        "required": ["path", "old_text", "new_text"],
    }
    permission_level = PermissionLevel.MODERATE
    is_read_only = False
    tags = ["memory", "edit", "记忆", "编辑", "修改"]

    async def execute(
        self, path: str, old_text: str, new_text: str
    ) -> ToolResult:
        full_path = Path(settings.DATA_DIR) / "memory" / path

        ok, result = _validate_path(str(full_path))
        if not ok:
            return ToolResult(success=False, output=result)

        if not full_path.exists():
            return ToolResult(success=False, output=f"文件不存在: {path}")

        content = full_path.read_text(encoding="utf-8")
        count = content.count(old_text)

        if count == 0:
            return ToolResult(
                success=False,
                output=f"未找到要替换的文本。请确认 old_text 精确匹配文件内容。",
            )
        if count > 1:
            return ToolResult(
                success=False,
                output=f"old_text 在文件中出现了 {count} 次，请提供更精确的匹配文本。",
            )

        new_content = content.replace(old_text, new_text, 1)
        full_path.write_text(new_content, encoding="utf-8")

        return ToolResult(success=True, output=f"已更新 {path}")


class MemoryDeleteTool(BaseTool):
    name = "memory_delete"
    description = (
        "删除一个记忆文件或文件中的特定内容。"
        "用于清理过时的、错误的、或不再需要的记忆。"
        "如果提供了 text_to_remove，只删除文件中的那段文本而不删除整个文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于 data/memory/）",
            },
            "text_to_remove": {
                "type": "string",
                "description": "要从文件中删除的特定文本。不提供则删除整个文件。",
            },
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.MODERATE
    is_read_only = False
    tags = ["memory", "delete", "记忆", "删除"]

    async def execute(
        self, path: str, text_to_remove: str | None = None
    ) -> ToolResult:
        full_path = Path(settings.DATA_DIR) / "memory" / path

        ok, result = _validate_path(str(full_path))
        if not ok:
            return ToolResult(success=False, output=result)

        if not full_path.exists():
            return ToolResult(success=False, output=f"文件不存在: {path}")

        if text_to_remove:
            content = full_path.read_text(encoding="utf-8")
            if text_to_remove not in content:
                return ToolResult(success=False, output="未找到要删除的文本。")
            new_content = content.replace(text_to_remove, "", 1).strip()
            if new_content:
                full_path.write_text(new_content + "\n", encoding="utf-8")
            else:
                full_path.unlink()  # 文件变空了就删掉
            return ToolResult(success=True, output=f"已从 {path} 中删除指定内容。")

        full_path.unlink()
        return ToolResult(success=True, output=f"已删除文件 {path}")


class MemorySearchTool(BaseTool):
    name = "memory_search"
    description = (
        "在所有记忆文件中搜索包含关键词的内容。"
        "用于查找之前记录的特定信息。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词",
            },
        },
        "required": ["keyword"],
    }
    permission_level = PermissionLevel.SAFE
    is_read_only = True
    tags = ["memory", "search", "记忆", "搜索"]

    async def execute(self, keyword: str) -> ToolResult:
        base = Path(settings.DATA_DIR) / "memory"
        if not base.exists():
            return ToolResult(success=True, output="(记忆目录为空)")

        matches = []
        for file_path in base.rglob("*.md"):
            content = file_path.read_text(encoding="utf-8")
            if keyword.lower() in content.lower():
                rel = file_path.relative_to(base)
                # 提取包含关键词的行
                for i, line in enumerate(content.split("\n"), 1):
                    if keyword.lower() in line.lower():
                        matches.append(f"  {rel}:{i}  {line.strip()}")

        if not matches:
            return ToolResult(
                success=True,
                output=f"未找到包含 '{keyword}' 的记忆。",
            )

        return ToolResult(
            success=True,
            output=f"找到 {len(matches)} 条匹配:\n" + "\n".join(matches[:20]),
        )
```

---

## 四、Part C：自动记忆提取

### 设计来源

Claude Code 的 `extractMemories` 服务 — 对话结束后自动提取值得记忆的信息。

### 新增文件：`src/memory/auto_extractor.py`

```python
"""自动记忆提取器。

在对话 session 变为 dormant 或一段时间无活动后，自动扫描近期对话，
提取值得长期记忆的信息并写入 data/memory/。

触发时机：
- Session 从 active → dormant 时（如果已实现 session 系统）
- Heartbeat 检测到 15 分钟无活动时（降级方案）
- 手动调用（测试用）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
你是 Lapwing 的记忆管理模块。回顾以下对话，提取值得长期记忆的信息。

## 提取规则

只提取重要度 >= 3 的信息（1-5 分）。不要提取：
- 临时性的（"今天下午三点开会" — 过了就没用了）
- 已经记过的信息
- 太笼统的（"Kevin 今天比较忙" — 没有具体信息）

## 分类

- kevin_fact: 关于 Kevin 的个人信息（偏好、习惯、背景、人际关系）
- decision: 对话中做出的决定（技术方案选择、计划变更）
- knowledge: Lapwing 学到的知识（技术概念、世界事实）
- interest: Kevin 或 Lapwing 表现出兴趣的话题
- correction: Kevin 纠正 Lapwing 的地方（说话方式、事实错误）

## 输出格式

只返回 JSON 数组，不要返回其他内容。如果没有值得提取的信息，返回空数组 []。

```json
[
    {
        "category": "kevin_fact",
        "content": "Kevin 更喜欢用中文讨论技术问题",
        "importance": 4
    }
]
```

## 对话内容

{conversation}
"""

# 已记忆的内容指纹，避免重复提取
# 简单方案：检查 content 是否已存在于对应文件中
# 未来升级：embedding 相似度去重


class AutoMemoryExtractor:
    """自动记忆提取器。"""

    def __init__(self, llm_router):
        """
        Args:
            llm_router: LLMRouter 实例，用于调用轻量 LLM 做提取。
        """
        self._llm = llm_router
        self._memory_dir = Path(settings.DATA_DIR) / "memory"

    async def extract_from_messages(
        self, messages: list[dict]
    ) -> list[dict]:
        """从消息列表中提取记忆。

        Args:
            messages: 对话消息列表 [{"role": ..., "content": ...}, ...]

        Returns:
            成功提取的记忆列表 [{"category": ..., "content": ...}, ...]
        """
        if len(messages) < 4:
            # 太短的对话不值得提取
            return []

        # 格式化对话给 LLM
        formatted = self._format_conversation(messages)

        try:
            raw = await self._llm.query_lightweight(
                system="你是一个记忆提取模块。严格按照要求输出 JSON。",
                user=EXTRACTION_PROMPT.replace("{conversation}", formatted),
            )
            items = self._parse_response(raw)
        except Exception as e:
            logger.error(f"Memory extraction LLM call failed: {e}")
            return []

        stored = []
        for item in items:
            if await self._store(item):
                stored.append(item)

        if stored:
            logger.info(
                f"Auto-extracted {len(stored)} memories from "
                f"{len(messages)} messages"
            )

        return stored

    def _format_conversation(self, messages: list[dict]) -> str:
        """把消息列表格式化为可读文本。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # 处理 multi-part content (tool calls etc)
                content = " ".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if not content or role == "system":
                continue

            speaker = "Kevin" if role == "user" else "Lapwing"
            # 截断过长的单条消息
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{speaker}: {content}")

        return "\n".join(lines)

    def _parse_response(self, raw: str) -> list[dict]:
        """解析 LLM 返回的 JSON。容错处理各种格式问题。"""
        # 去掉 markdown 代码块包裹
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse extraction response: {text[:200]}")
            return []

        if not isinstance(items, list):
            return []

        valid = []
        for item in items:
            if (
                isinstance(item, dict)
                and "category" in item
                and "content" in item
            ):
                valid.append(item)

        return valid

    async def _store(self, item: dict) -> bool:
        """把一条提取的记忆存入文件。

        去重：如果 content 已经出现在对应文件中，跳过。
        """
        category = item["category"]
        content = item["content"]

        # 确保目录存在
        cat_dir = self._memory_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        # 按月组织文件
        month_key = datetime.now().strftime("%Y-%m")
        file_path = cat_dir / f"{month_key}.md"

        # 去重检查
        if file_path.exists():
            existing = file_path.read_text(encoding="utf-8")
            if content in existing:
                logger.debug(f"Duplicate memory skipped: {content[:50]}")
                return False

        # 追加
        with open(file_path, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%m-%d %H:%M")
            f.write(f"- [{timestamp}] {content}\n")

        return True
```

### 集成到 heartbeat

在 `src/heartbeat/actions/` 下新增 `auto_memory.py`：

```python
"""Heartbeat action: 自动记忆提取。

触发条件：检测到 15 分钟无对话活动时，对最近对话执行一次提取。
每次 heartbeat cycle 最多触发一次。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 上次提取时间，避免频繁触发
_last_extraction: datetime | None = None
# 最小间隔：30 分钟
_MIN_INTERVAL = timedelta(minutes=30)


async def should_run(context: dict) -> bool:
    """判断是否应该运行自动记忆提取。"""
    global _last_extraction

    # 条件 1：最近 15 分钟没有新消息
    last_message_time = context.get("last_message_time")
    if last_message_time is None:
        return False

    idle_minutes = (datetime.now() - last_message_time).total_seconds() / 60
    if idle_minutes < 15:
        return False

    # 条件 2：距上次提取至少 30 分钟
    if _last_extraction and (datetime.now() - _last_extraction) < _MIN_INTERVAL:
        return False

    return True


async def run(context: dict) -> str | None:
    """执行自动记忆提取。

    Returns:
        提取结果描述，或 None 表示无提取。
    """
    global _last_extraction

    from src.memory.auto_extractor import AutoMemoryExtractor

    extractor: AutoMemoryExtractor = context["auto_extractor"]
    recent_messages: list[dict] = context["recent_messages"]

    if len(recent_messages) < 4:
        return None

    results = await extractor.extract_from_messages(recent_messages)
    _last_extraction = datetime.now()

    if results:
        categories = set(r["category"] for r in results)
        return f"自动提取了 {len(results)} 条记忆 ({', '.join(categories)})"

    return None
```

---

## 五、Part D：自主调度

### 设计来源

Claude Code 的 `CronCreateTool`（AGENT_TRIGGERS flag）— Agent 自己可以创建定时任务。

### 新增文件：`src/tools/schedule_task.py`

```python
"""自主调度工具 — 让 Lapwing 自己安排定时任务。

Lapwing 可以决定：
- "我每天晚上 11 点回顾今天的对话"
- "每隔 3 小时看一下科技新闻"
- "明天早上 8 点提醒 Kevin 交文档"

任务持久化到 data/scheduled_tasks.json，由 heartbeat 驱动执行。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from config import settings
from src.tools.base import BaseTool, PermissionLevel, ToolResult

logger = logging.getLogger(__name__)

TASKS_FILE = Path(settings.DATA_DIR) / "scheduled_tasks.json"


class ScheduleTaskTool(BaseTool):
    name = "schedule_task"
    description = (
        "安排一个在未来执行的定时任务。"
        "用于：定期自省、定时提醒 Kevin、自主浏览新闻、安排兴趣探索等。"
        "schedule 用自然语言描述即可（如'每天晚上11点'、'每隔2小时'、'明天早上8点'）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "schedule": {
                "type": "string",
                "description": (
                    "时间安排描述。"
                    "支持: '每天HH:MM'、'每隔N小时'、'每隔N分钟'、'YYYY-MM-DD HH:MM'（单次）"
                ),
            },
            "task_description": {
                "type": "string",
                "description": "要执行的任务描述。这会成为你收到的 prompt。",
            },
            "repeat": {
                "type": "boolean",
                "description": "是否重复执行。默认 true。",
                "default": True,
            },
        },
        "required": ["schedule", "task_description"],
    }
    permission_level = PermissionLevel.MODERATE
    is_read_only = False
    tags = ["schedule", "cron", "定时", "安排", "提醒"]

    async def execute(
        self,
        schedule: str,
        task_description: str,
        repeat: bool = True,
    ) -> ToolResult:
        parsed = _parse_schedule(schedule)
        if parsed is None:
            return ToolResult(
                success=False,
                output=f"无法解析时间安排: '{schedule}'。"
                f"请用以下格式：'每天HH:MM'、'每隔N小时'、'YYYY-MM-DD HH:MM'",
            )

        task = {
            "id": f"sched_{uuid4().hex[:8]}",
            "schedule_raw": schedule,
            "schedule_parsed": parsed,
            "task": task_description,
            "repeat": repeat,
            "created_at": datetime.now().isoformat(),
            "created_by": "lapwing",
            "last_run": None,
            "enabled": True,
        }

        tasks = _load_tasks()
        tasks.append(task)
        _save_tasks(tasks)

        logger.info(f"Scheduled task: {task['id']} — {schedule} — {task_description[:50]}")

        return ToolResult(
            success=True,
            output=f"已安排: {task_description}\n时间: {schedule} ({'重复' if repeat else '单次'})\nID: {task['id']}",
        )


class ListScheduledTasksTool(BaseTool):
    name = "list_scheduled_tasks"
    description = "查看所有已安排的定时任务。"
    parameters = {"type": "object", "properties": {}}
    permission_level = PermissionLevel.SAFE
    is_read_only = True
    tags = ["schedule", "list", "定时", "查看"]

    async def execute(self) -> ToolResult:
        tasks = _load_tasks()
        if not tasks:
            return ToolResult(success=True, output="当前没有定时任务。")

        lines = []
        for t in tasks:
            status = "✓" if t["enabled"] else "✗"
            repeat = "重复" if t["repeat"] else "单次"
            last = t.get("last_run", "从未执行")
            lines.append(
                f"  {status} [{t['id']}] {t['schedule_raw']} ({repeat})\n"
                f"    任务: {t['task'][:60]}\n"
                f"    上次: {last}"
            )

        return ToolResult(
            success=True,
            output=f"定时任务 ({len(tasks)} 个):\n\n" + "\n\n".join(lines),
        )


class CancelScheduledTaskTool(BaseTool):
    name = "cancel_scheduled_task"
    description = "取消一个定时任务。"
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要取消的任务 ID",
            },
        },
        "required": ["task_id"],
    }
    permission_level = PermissionLevel.MODERATE
    is_read_only = False
    tags = ["schedule", "cancel", "定时", "取消"]

    async def execute(self, task_id: str) -> ToolResult:
        tasks = _load_tasks()
        for i, t in enumerate(tasks):
            if t["id"] == task_id:
                tasks.pop(i)
                _save_tasks(tasks)
                return ToolResult(
                    success=True, output=f"已取消任务: {t['task'][:50]}"
                )
        return ToolResult(success=False, output=f"未找到任务: {task_id}")


# --- 内部函数 ---

def _load_tasks() -> list[dict]:
    if not TASKS_FILE.exists():
        return []
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return []


def _save_tasks(tasks: list[dict]) -> None:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_schedule(raw: str) -> dict | None:
    """解析自然语言时间安排为结构化格式。

    支持的格式:
    - "每天HH:MM"  → {"type": "daily", "time": "HH:MM"}
    - "每隔N小时"   → {"type": "interval", "hours": N}
    - "每隔N分钟"   → {"type": "interval", "minutes": N}
    - "YYYY-MM-DD HH:MM" → {"type": "once", "datetime": "..."}

    Returns:
        解析后的 dict，或 None 表示无法解析。
    """
    import re

    raw = raw.strip()

    # 每天 HH:MM
    m = re.match(r"每天\s*(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        return {
            "type": "daily",
            "time": f"{int(m.group(1)):02d}:{m.group(2)}",
        }

    # 每隔 N 小时
    m = re.match(r"每隔\s*(\d+)\s*小时", raw)
    if m:
        return {"type": "interval", "hours": int(m.group(1))}

    # 每隔 N 分钟
    m = re.match(r"每隔\s*(\d+)\s*分钟", raw)
    if m:
        return {"type": "interval", "minutes": int(m.group(1))}

    # 日期时间（单次）
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        return {
            "type": "once",
            "datetime": f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}",
        }

    # 明天/后天 HH:MM
    m = re.match(r"(明天|后天)\s*(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        from datetime import timedelta
        days = 1 if m.group(1) == "明天" else 2
        target = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return {
            "type": "once",
            "datetime": f"{target} {int(m.group(2)):02d}:{m.group(3)}",
        }

    return None
```

### 新增文件：`src/heartbeat/actions/scheduled_tasks.py`

```python
"""Heartbeat action: 检查并执行到期的定时任务。

每个 heartbeat cycle 都检查一次。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

TASKS_FILE = Path(settings.DATA_DIR) / "scheduled_tasks.json"


async def check_and_run(context: dict) -> list[str]:
    """检查到期任务，返回需要执行的任务 prompt 列表。

    Brain 收到这些 prompt 后，像处理 heartbeat proactive message 一样处理。
    """
    if not TASKS_FILE.exists():
        return []

    try:
        tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return []

    now = datetime.now()
    to_execute = []
    modified = False

    for task in tasks:
        if not task.get("enabled", True):
            continue

        if _should_run(task, now):
            to_execute.append(task["task"])
            task["last_run"] = now.isoformat()
            modified = True

            if not task.get("repeat", True):
                task["enabled"] = False

            logger.info(f"Scheduled task triggered: {task['id']}")

    if modified:
        # 清理已禁用的单次任务
        tasks = [t for t in tasks if t.get("enabled", True)]
        TASKS_FILE.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return to_execute


def _should_run(task: dict, now: datetime) -> bool:
    """判断任务是否到了执行时间。"""
    parsed = task.get("schedule_parsed", {})
    last_run_str = task.get("last_run")
    last_run = (
        datetime.fromisoformat(last_run_str) if last_run_str else None
    )

    stype = parsed.get("type")

    if stype == "daily":
        # 每天 HH:MM
        target_time = parsed["time"]  # "23:00"
        h, m = map(int, target_time.split(":"))
        # 当前时间在目标时间的 5 分钟窗口内
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((now - target).total_seconds()) > 300:
            return False
        # 今天没执行过
        if last_run and last_run.date() == now.date():
            return False
        return True

    if stype == "interval":
        hours = parsed.get("hours", 0)
        minutes = parsed.get("minutes", 0)
        interval = timedelta(hours=hours, minutes=minutes)
        if interval.total_seconds() < 60:
            return False  # 安全：最少 1 分钟
        if last_run is None:
            return True
        return (now - last_run) >= interval

    if stype == "once":
        target = datetime.strptime(parsed["datetime"], "%Y-%m-%d %H:%M")
        if abs((now - target).total_seconds()) > 300:
            return False
        if last_run:
            return False
        return True

    return False
```

---

## 六、Part E：Feature Flags

### 新增文件：`config/feature_flags.py`

```python
"""Feature flags — 控制 Lapwing 各功能模块的开关。

优先从环境变量读取覆盖（LAPWING_FLAG_名称=true/false）。
默认值反映当前推荐状态。
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


class FeatureFlags:
    """访问 FLAGS.XXX 获取布尔值。"""

    # === Wave 1 新功能 ===
    UNIFIED_TOOLS: bool = True       # 统一 Tool 接口（本次改造核心）
    MEMORY_CRUD: bool = True         # Memory 编辑/删除/搜索
    AUTO_MEMORY_EXTRACT: bool = True # 自动记忆提取
    SELF_SCHEDULE: bool = True       # 自主调度 (CronTool)

    # === 已有功能 ===
    TELEGRAM: bool = True
    QQ_CHANNEL: bool = True
    HEARTBEAT: bool = True
    SELF_REFLECTION: bool = True
    SESSIONS: bool = False           # Session 系统（前一个蓝图）

    # === 未来 ===
    DESKTOP_APP: bool = False
    AUDIO_CAPTURE: bool = False
    DYNAMIC_AGENTS: bool = False
    TOOL_SEARCH: bool = False
    PROGRESSIVE_COMPRESS: bool = False

    def __init__(self):
        """从环境变量加载覆盖。"""
        for attr in dir(self):
            if attr.startswith("_") or not attr.isupper():
                continue
            env_key = f"LAPWING_FLAG_{attr}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                val = env_val.lower() in ("true", "1", "yes")
                setattr(self, attr, val)
                logger.info(f"Feature flag override: {attr} = {val}")

    def __repr__(self) -> str:
        flags = {
            attr: getattr(self, attr)
            for attr in dir(self)
            if attr.isupper() and not attr.startswith("_")
        }
        enabled = [k for k, v in flags.items() if v]
        return f"FeatureFlags(enabled={enabled})"


# 全局单例
FLAGS = FeatureFlags()
```

---

## 七、集成改动：brain.py

### 改动 1：用 ToolRegistry 替换硬编码的 tool schemas

**找到** brain.py 中定义 tools 列表的地方（大概在 `_prepare_think` 或 `__init__` 中，有一个 `self.tools = [...]` 或类似的 hardcoded tool definitions）。

**替换为**：

```python
# 在 Brain.__init__ 中
from config.feature_flags import FLAGS
from src.tools import create_default_registry

if FLAGS.UNIFIED_TOOLS:
    self.tool_registry = create_default_registry(
        memory_manager=self.memory,
        search_engine=self.search_engine,
        # ... 传入其他依赖
    )
else:
    self.tool_registry = None  # fallback 到旧的硬编码方式
```

### 改动 2：tool schema 生成

**找到** 构建 API 请求中 `tools=` 参数的地方。

**替换为**：

```python
# 在 _prepare_think 或 _complete_chat 中
if self.tool_registry:
    tools_param = self.tool_registry.to_openai_schemas()
else:
    tools_param = self._legacy_tools()  # 保留旧的硬编码列表作为 fallback
```

### 改动 3：tool call 处理

**找到** 处理 LLM 返回的 tool_call 的循环（通常在 `think` 方法中，检查 `response.choices[0].message.tool_calls`）。

**替换 tool dispatch 逻辑为**：

```python
# 旧代码大概长这样:
# if tool_call.function.name == "memory_note":
#     result = await self._handle_memory_note(args)
# elif tool_call.function.name == "web_search":
#     result = await self._handle_web_search(args)
# ...

# 新代码：
if self.tool_registry:
    tool_result = await self.tool_registry.execute(
        tool_name=tool_call.function.name,
        arguments=json.loads(tool_call.function.arguments),
    )
    result_str = tool_result.to_str()
else:
    # fallback 到旧的 dispatch
    result_str = await self._legacy_dispatch(tool_call)
```

### 改动 4：heartbeat 集成自动记忆提取

**找到** heartbeat 的 action 执行循环（`src/core/heartbeat.py` 或 `src/heartbeat/actions/` 的注册位置）。

**新增**：

```python
from config.feature_flags import FLAGS

if FLAGS.AUTO_MEMORY_EXTRACT:
    from src.heartbeat.actions import auto_memory
    # 在 heartbeat cycle 中:
    if await auto_memory.should_run(context):
        result = await auto_memory.run(context)
        if result:
            logger.info(f"Auto memory: {result}")
```

### 改动 5：heartbeat 集成定时任务

```python
if FLAGS.SELF_SCHEDULE:
    from src.heartbeat.actions import scheduled_tasks
    # 在 heartbeat cycle 中:
    task_prompts = await scheduled_tasks.check_and_run(context)
    for prompt in task_prompts:
        # 像处理 proactive message 一样处理
        await self._handle_scheduled_prompt(prompt)
```

**`_handle_scheduled_prompt`** 的实现：

```python
async def _handle_scheduled_prompt(self, prompt: str):
    """执行一个定时任务 prompt。

    Lapwing 自己安排的任务，用她自己的方式执行。
    结果可能需要发消息给 Kevin（如果是提醒类任务），
    也可能是内部操作（如自省、浏览新闻）。
    """
    # 用 heartbeat 的 channel 发送，因为这是 Lapwing 主动行为
    response = await self.think(
        user_message=f"[定时任务触发] {prompt}",
        chat_id=self.kevin_chat_id,
        is_scheduled=True,  # 标记来源，让 Lapwing 知道这是她自己安排的
    )

    # 判断是否需要发送给 Kevin
    # 如果任务是"自省"类的，不需要发送
    # 如果任务是"提醒 Kevin"类的，需要发送
    # 这个判断可以靠 response 内容或 task metadata
    if response and not self._is_internal_task(prompt):
        await self._send_proactive(response)
```

---

## 八、部署顺序

### Phase 1：骨架（不影响现有行为）

1. 创建 `src/tools/base.py`、`src/tools/registry.py`
2. 创建 `config/feature_flags.py`，所有新 flag 默认 **False**
3. 迁移 `memory_note` 到 `src/tools/memory_note.py`（保留旧代码作为 fallback）
4. 在 brain.py 中加入 `if FLAGS.UNIFIED_TOOLS:` 分支，但不启用
5. **部署，观察，确认无回归**

### Phase 2：启用统一 Tool 接口

1. 迁移所有现有工具到 `src/tools/` 下的独立文件
2. 设置 `LAPWING_FLAG_UNIFIED_TOOLS=true`
3. **部署，观察几小时，确认所有工具正常工作**

### Phase 3：Memory CRUD

1. 创建 `src/tools/memory_crud.py`
2. 在 `create_default_registry()` 中注册
3. 设置 `LAPWING_FLAG_MEMORY_CRUD=true`
4. **部署，测试 Lapwing 能否列出/读取/编辑/删除记忆**

### Phase 4：自动记忆提取

1. 创建 `src/memory/auto_extractor.py`
2. 创建 `src/heartbeat/actions/auto_memory.py`
3. 在 heartbeat 中集成
4. 设置 `LAPWING_FLAG_AUTO_MEMORY_EXTRACT=true`
5. **部署，观察 data/memory/ 目录是否有新文件自动生成**

### Phase 5：自主调度

1. 创建 `src/tools/schedule_task.py`
2. 创建 `src/heartbeat/actions/scheduled_tasks.py`
3. 在 heartbeat 中集成
4. 设置 `LAPWING_FLAG_SELF_SCHEDULE=true`
5. **部署，手动让 Lapwing 安排一个测试任务，观察是否按时触发**

---

## 九、测试清单

### BaseTool / ToolRegistry

> ⚠️ Part A 未按蓝图实现（BaseTool ABC）。项目已有成熟的 ToolSpec + executor 模式，决定不重写，改用现有架构实现新工具。以下测试对应的是蓝图设计，非实际实现，保留作参考。

- [ ] `test_tool_schema_generation` — to_openai_schema() 输出格式正确（已有 tests/tools/test_registry.py 覆盖）
- [ ] `test_registry_register_and_get` — 注册后能按名称获取（已有 tests/tools/test_registry.py 覆盖）
- [ ] `test_registry_execute_success` — 正常执行返回 ToolResult（已有 tests/tools/test_registry.py 覆盖）
- [ ] `test_registry_execute_unknown_tool` — 未知工具返回错误（已有 tests/tools/test_registry.py 覆盖）
- [ ] `test_registry_permission_forbidden` — FORBIDDEN 权限被拒绝（未实现，PermissionLevel 未引入）
- [ ] `test_registry_execute_exception` — 工具抛异常被捕获（已有 tests/tools/test_registry.py 覆盖）

### Memory CRUD

- [x] `test_memory_list` — 列出记忆文件（tests/tools/test_memory_crud.py::TestMemoryList）
- [x] `test_memory_read` — 读取文件内容（带行号）（tests/tools/test_memory_crud.py::TestMemoryRead）
- [x] `test_memory_edit` — 查找替换成功（tests/tools/test_memory_crud.py::TestMemoryEdit）
- [x] `test_memory_edit_not_found` — 未找到文本返回错误（tests/tools/test_memory_crud.py::TestMemoryEdit）
- [x] `test_memory_edit_multiple_matches` — 多次匹配返回错误（tests/tools/test_memory_crud.py::TestMemoryEdit）
- [x] `test_memory_delete_file` — 删除整个文件（tests/tools/test_memory_crud.py::TestMemoryDelete）
- [x] `test_memory_delete_text` — 删除文件中的特定文本（tests/tools/test_memory_crud.py::TestMemoryDelete）
- [x] `test_memory_search` — 关键词搜索（tests/tools/test_memory_crud.py::TestMemorySearch）
- [x] `test_path_traversal_blocked` — 目录遍历攻击被阻止（tests/tools/test_memory_crud.py::TestValidatePath）
- [x] `test_identity_protected` — 不能操作 data/identity/（tests/tools/test_memory_crud.py::TestValidatePath）

### Auto Memory Extraction

- [x] `test_extract_from_short_conversation` — 少于 4 条消息不提取（tests/memory/test_auto_extractor.py::TestShortConversation）
- [x] `test_extract_parses_json` — 正确解析 LLM 返回的 JSON（tests/memory/test_auto_extractor.py::TestParseResponse）
- [x] `test_extract_handles_malformed_json` — 格式错误不崩溃（tests/memory/test_auto_extractor.py::TestParseResponse）
- [x] `test_extract_dedup` — 重复内容不重复存储（tests/memory/test_auto_extractor.py::TestDeduplication）
- [x] `test_extract_stores_to_correct_category` — 分类目录正确（tests/memory/test_auto_extractor.py::TestStorePath）
- [x] `test_heartbeat_trigger_condition` — 15 分钟无活动才触发（tests/heartbeat/actions/test_auto_memory.py::TestAutoMemoryAction）

### Scheduled Tasks

- [x] `test_parse_daily` — "每天23:00" 解析正确（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_parse_interval_hours` — "每隔2小时" 解析正确（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_parse_interval_minutes` — "每隔30分钟" 解析正确（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_parse_once` — "2026-04-01 08:00" 解析正确（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_parse_tomorrow` — "明天08:00" 解析正确（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_parse_invalid` — 无效格式返回 None（tests/tools/test_schedule_task.py::TestParseSchedule）
- [x] `test_should_run_daily` — 到时间触发，同一天不重复（tests/heartbeat/actions/test_scheduled_tasks.py::TestShouldRun）
- [x] `test_should_run_interval` — 间隔到了触发（tests/heartbeat/actions/test_scheduled_tasks.py::TestShouldRun）
- [x] `test_should_run_once` — 单次任务执行后禁用（tests/heartbeat/actions/test_scheduled_tasks.py::TestScheduledTasksAction）
- [x] `test_cancel_task` — 取消任务（tests/tools/test_schedule_task.py::TestCancelScheduledTaskExecutor）

### Feature Flags

> ℹ️ 未实现独立的 FeatureFlags 类，改用项目现有 env var 模式（settings.py）。测试以 env var 验证为主。

- [x] `test_default_values` — 默认值正确（MEMORY_CRUD_ENABLED=true, AUTO_MEMORY_EXTRACT_ENABLED=true, SELF_SCHEDULE_ENABLED=true）
- [ ] `test_env_override` — 环境变量覆盖生效（待补充）
- [ ] `test_fallback_when_disabled` — flag 关闭时走旧路径（待补充）

---

## 十、文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `src/tools/__init__.py` | 工具包初始化 + create_default_registry |
| **新增** | `src/tools/base.py` | BaseTool + ToolResult + PermissionLevel |
| **新增** | `src/tools/registry.py` | ToolRegistry |
| **新增** | `src/tools/memory_note.py` | 迁移现有 memory_note |
| **新增** | `src/tools/web_search.py` | 迁移现有 web_search |
| **新增** | `src/tools/memory_crud.py` | Memory 列出/读取/编辑/删除/搜索 |
| **新增** | `src/tools/schedule_task.py` | 自主调度工具 (3个 Tool) |
| **新增** | `src/memory/auto_extractor.py` | 自动记忆提取器 |
| **新增** | `src/heartbeat/actions/auto_memory.py` | heartbeat 集成 |
| **新增** | `src/heartbeat/actions/scheduled_tasks.py` | 定时任务 heartbeat 集成 |
| **新增** | `config/feature_flags.py` | Feature flag 系统 |
| **修改** | `src/core/brain.py` | ToolRegistry 集成 (4 处改动) |
| **修改** | `src/core/heartbeat.py` | 自动记忆 + 定时任务集成 |
| **修改** | `config/settings.py` | 新增 DATA_DIR 等路径配置（如果不存在） |

### 不修改（保留作为 fallback）

| 文件 | 说明 |
|------|------|
| 现有 brain.py 中的 tool schemas | FLAGS.UNIFIED_TOOLS=false 时仍使用 |
| 现有 tool call dispatch 逻辑 | FLAGS.UNIFIED_TOOLS=false 时仍使用 |

---

## 附录：LLMRouter.query_lightweight

自动记忆提取需要一个轻量级 LLM 调用方法。如果 `llm_router.py` 中没有，需要新增：

```python
async def query_lightweight(self, system: str, user: str) -> str:
    """用轻量模型做简单任务（分类、提取、判断）。

    使用较低的 max_tokens，不需要 tool calling。
    """
    response = await self._client.chat.completions.create(
        model=self.lightweight_model,  # settings 中配置
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=1000,
        temperature=0.3,
    )
    return response.choices[0].message.content
```