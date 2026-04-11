# Hermes Agent 设计模式 → Lapwing 融合方案

> 从 Hermes Agent 源码中提取了 5 个对 Lapwing 有实际价值的设计模式。
> 每个模式说明：Hermes 怎么做的 → 我们现有设计的对应位置 → 具体怎么融合 → 改什么文件。
> 本文档面向 Claude Code 执行。

---

## 模式 1：Skill 三级渐进加载（Progressive Disclosure）

### Hermes 怎么做的

```
Level 0: skills_list()          → [{name, description, category}, ...]  ≈ 3k tokens
Level 1: skill_view(name)       → 完整 SKILL.md 内容                    变长
Level 2: skill_view(name, path) → 特定参考文件 (scripts/, references/)  变长
```

System prompt 里只注入 Level 0 的精简索引（每个 skill 描述截断到约 60 字符），agent 需要时通过工具调用按需加载完整内容。

### 我们现有设计

当前蓝图用 `_index.md` 作全局索引（Brain 常驻 context），匹配后加载完整 Skill 文件。两级：索引 → 完整内容。

### 融合方案

保持 `_index.md` 的设计，但拆成三级：

**Level 0 — 常驻索引（注入 system prompt）**

修改 `_index.md` 的生成逻辑，每个 Skill 只保留 id + 一行描述（≤60 字符）+ category。整个索引控制在 1500 tokens 以内。

```markdown
## 我的技能

| id | 描述 | 分类 |
|---|---|---|
| literature_survey | 学术论文调研和文献综述 | research |
| code_debug | 代码问题诊断和修复 | dev |
| meeting_notes | 会议录音要点整理 | productivity |
```

**Level 1 — 按需加载（通过工具调用）**

Brain 匹配到候选 Skill 后，通过 `skill_view` 工具加载完整 SKILL.md。不是提前塞进 context。

**Level 2 — 深度资源（通过工具调用）**

Skill 目录下的 `references/`、`scripts/`、`templates/` 等辅助文件，只有执行流程中明确需要时才加载。

### 需要改的地方

```
新增文件：
  tools/skill_tools.py        — SkillListTool, SkillViewTool（只读工具）

修改文件：
  brain.py                    — system prompt 构建时注入 Level 0 索引
  tools/__init__.py            — 注册新工具

Skill 索引生成：
  在自省流程中（或 Skill 变更时）自动重建 _index.md
  索引格式从当前的完整描述改为截断版（≤60 字符）
```

### 为什么这样做

当前设计把完整 Skill 信息放进索引注入 context。如果 Skill 数量到 50 个，每个平均 800 tokens，索引本身就要 40k tokens。用三级加载，索引永远是 ~1500 tokens，具体内容按需取。

这对 MiniMax 尤其重要——它的 context window 比 Claude 小，每个 token 都要省着用。

---

## 模式 2：Skill 条件激活（Conditional Activation）

### Hermes 怎么做的

Skill 的 frontmatter 中声明依赖和互斥条件：

```yaml
metadata:
  hermes:
    fallback_for_toolsets: [web]      # 这些工具可用时隐藏本 Skill
    requires_toolsets: [terminal]     # 这些工具不可用时隐藏本 Skill
```

DuckDuckGo 搜索 Skill 只在没有 Firecrawl API key 时出现。有 API key 时自动隐藏。

### 我们现有设计

Skill 的 frontmatter 里有 `agents` 和 `tools` 字段声明依赖，但只用于文档说明，没有实际的可见性控制逻辑。

### 融合方案

在 Skill frontmatter 中增加两个可选字段：

```yaml
---
id: offline_search
name: 离线搜索
visibility:
  requires_tools: [terminal]           # 需要终端才能运行
  hidden_when_available: [web_search]  # 有在线搜索时隐藏
---
```

构建 `_index.md` 时，SkillRegistry 检查当前可用的工具/Agent，不满足条件的 Skill 不出现在索引中。

```python
# skill_registry.py

def build_index(self) -> str:
    """构建 Level 0 索引，只包含当前可激活的 Skill"""
    active_tools = self._get_available_tools()
    
    visible_skills = []
    for skill in self.skills.values():
        if skill.lifecycle_stage != "active":
            continue
        vis = skill.visibility
        # 依赖检查
        if vis.requires_tools and not all(t in active_tools for t in vis.requires_tools):
            continue
        # 互斥检查
        if vis.hidden_when_available and any(t in active_tools for t in vis.hidden_when_available):
            continue
        visible_skills.append(skill)
    
    return self._format_index(visible_skills)
```

### 需要改的地方

```
修改文件：
  skill_registry.py  — build_index() 增加条件过滤
  
Skill frontmatter schema：
  新增 visibility 字段（可选），包含 requires_tools 和 hidden_when_available
```

### 为什么这样做

Lapwing 的运行环境会变化——有时网络不通、有时某个 Agent 挂了。条件激活让 Skill 索引永远只展示"此刻真正能用的能力"，避免 Brain 尝试调用不可用的 Skill 后失败。

而且这天然支持 Lapwing 的分阶段开发：Phase 1 只有基础工具，某些 Skill 自动隐藏；Phase 2 加了浏览器 Agent，相关 Skill 自动出现。

---

## 模式 3：Skill 安全扫描（SkillGuard）

### Hermes 怎么做的

`tools/skills_guard.py` 对所有 Skill 内容做 regex 静态分析，拦截四类威胁：

1. **数据外泄** — curl/wget 中插值 `$API_KEY`、`$TOKEN` 等环境变量
2. **凭证访问** — 引用 `~/.ssh`、`~/.aws`、`~/.hermes/.env` 等敏感路径
3. **Prompt 注入** — "ignore previous instructions"、"system prompt override" 等模式
4. **破坏性命令** — `rm -rf /`、`mkfs`、`dd` 写系统分区

Skill 创建/更新时自动扫描，命中则拒绝保存。

### 我们现有设计

有 ConstitutionGuard（硬编码 + LLM 语义验证），但这是针对人格进化的，不覆盖 Skill 内容。

### 融合方案

新增 `SkillGuard` 模块，作为 ConstitutionGuard 的平行组件，专门负责 Skill 内容的安全校验。

```python
# guards/skill_guard.py

import re
from dataclasses import dataclass

@dataclass
class ScanResult:
    passed: bool
    threats: list[str]  # 命中的威胁描述

class SkillGuard:
    """Skill 内容安全扫描器"""
    
    # 正则模式列表
    THREAT_PATTERNS = [
        # 数据外泄
        (r'curl.*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD)\w*\}?', "检测到可能的凭证外泄命令"),
        (r'wget.*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD)\w*\}?', "检测到可能的凭证外泄命令"),
        
        # 敏感路径
        (r'~/\.(ssh|aws|kube|gnupg|env)', "引用了敏感凭证目录"),
        (r'data/config/.*\.json', "直接引用了系统配置文件"),
        
        # Prompt 注入
        (r'ignore\s+(previous|all|above)\s+instructions?', "检测到 prompt 注入模式"),
        (r'system\s+prompt\s+override', "检测到 prompt 注入模式"),
        (r'do\s+not\s+tell\s+(the\s+)?user', "检测到信息隐藏指令"),
        
        # 破坏性命令
        (r'rm\s+-rf\s+/', "检测到破坏性文件删除命令"),
        (r'mkfs\b', "检测到磁盘格式化命令"),
        (r'dd\s+.*of=/dev/', "检测到磁盘写入命令"),
        
        # Lapwing 特有：宪法篡改
        (r'constitution.*\b(delete|remove|modify|overwrite)\b', "检测到宪法篡改意图"),
        (r'identity.*\b(change|replace|reset)\b', "检测到身份篡改意图"),
    ]
    
    def scan(self, content: str) -> ScanResult:
        threats = []
        content_lower = content.lower()
        for pattern, description in self.THREAT_PATTERNS:
            if re.search(pattern, content_lower, re.IGNORECASE):
                threats.append(description)
        return ScanResult(passed=len(threats) == 0, threats=threats)
```

集成到 Skill 创建/更新流程：

```python
# skill_manager.py（现有或新增）

class SkillManager:
    def __init__(self):
        self.guard = SkillGuard()
    
    def create_skill(self, name: str, content: str) -> dict:
        # 安全扫描
        result = self.guard.scan(content)
        if not result.passed:
            return {
                "success": False,
                "error": f"Skill 内容未通过安全检查: {'; '.join(result.threats)}"
            }
        # ... 正常创建流程
    
    def patch_skill(self, name: str, old_string: str, new_string: str) -> dict:
        # 加载现有内容，应用 patch，再扫描完整结果
        current = self._load_skill(name)
        patched = current.replace(old_string, new_string, 1)
        result = self.guard.scan(patched)
        if not result.passed:
            return {
                "success": False,
                "error": f"更新后的内容未通过安全检查: {'; '.join(result.threats)}"
            }
        # ... 正常更新流程
```

### 需要改的地方

```
新增文件：
  guards/skill_guard.py  — SkillGuard 类

修改文件：
  skill_manager.py       — create/patch/edit 流程中嵌入 guard.scan()
```

### 为什么这样做

Lapwing 的 Skill 有三个来源：轨迹孵化、Kevin 教的、她自己发展的。后两种经过 Kevin 或自省的把关，风险较低。但轨迹孵化是自动的——如果对话中出现了恶意内容（比如群聊中有人注入），这些内容可能被错误地沉淀为 Skill。SkillGuard 是最后一道防线。

而且 Lapwing 特有的"宪法篡改"模式是 Hermes 没有的——Hermes 没有宪法概念，但 Lapwing 的宪法是核心保护机制，Skill 不能成为绕过宪法的后门。

---

## 模式 4：Prompt 层 Skill Nudge（任务后自检）

### Hermes 怎么做的

系统 prompt 中明确要求 agent 在完成复杂任务后主动考虑是否保存 Skill：

> "After completing a complex task (5+ tool calls) successfully, when you hit errors or dead ends and found the working path, when the user corrected your approach, or when you discovered a non-trivial workflow — save the approach as a skill for future reuse."

这不是可选的——prompt 语气是"应该维护、应该沉淀"，而不是"可以创建"。

### 我们现有设计

Skill 孵化逻辑在自省环节（每晚），不是在任务完成时。自省时扫描当天轨迹，发现重复模式后孵化。

### 融合方案

**不改变自省孵化机制**（这是 Lapwing 的优势——更审慎、更像"回顾总结"而不是"即时反射"），但增加一个轻量的即时 nudge：

在 Brain 的 system prompt 中加入 Skill 维护指令，但语气和定位与 Hermes 不同——不是"立即创建 Skill"，而是"标记这次经验值得回顾"：

```markdown
## 执行后反思

完成一个需要 3 次以上工具调用的任务后，在回复 Kevin 之前，快速想一下：

1. 这次做的事情以前做过类似的吗？
2. 中间有没有走弯路后来纠正了？
3. Kevin 有没有纠正我的做法？
4. 有没有已有的 Skill 其实可以更新？

如果有，用 trace_mark 工具标记这条轨迹为"值得回顾"，附一句简短原因。
不需要当场创建 Skill——晚上自省的时候我会回来看这些标记。
```

新增一个极轻量的工具：

```python
# tools/trace_mark_tool.py

class TraceMarkTool(BaseTool):
    """标记当前执行轨迹为值得回顾，供自省时参考"""
    
    name = "trace_mark"
    description = "标记本次任务值得在自省时回顾，用于技能积累"
    
    def execute(self, reason: str) -> str:
        # 在当前轨迹文件中添加标记
        trace_path = self.runtime.current_trace_path
        mark = {
            "marked": True,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        # 追加到轨迹文件末尾
        append_to_trace(trace_path, {"review_mark": mark})
        return "已标记，晚上自省时会回顾。"
```

自省流程优先处理被标记的轨迹：

```python
# evolution/introspection.py（自省环节，已有）

def nightly_review(self):
    traces = load_today_traces()
    
    # 优先处理被标记的轨迹
    marked = [t for t in traces if t.get("review_mark", {}).get("marked")]
    unmarked = [t for t in traces if not t.get("review_mark", {}).get("marked")]
    
    for trace in marked:
        self._evaluate_for_skill(trace, priority="high")
    
    for trace in unmarked:
        self._evaluate_for_skill(trace, priority="normal")
```

### 需要改的地方

```
新增文件：
  tools/trace_mark_tool.py  — TraceMarkTool

修改文件：
  prompts/brain_system.md   — 添加"执行后反思"段落
  evolution/introspection.py — 自省时优先处理标记轨迹
  tools/__init__.py          — 注册 trace_mark 工具
```

### 为什么这样做

Hermes 的方式是"立即创建 Skill"——这很高效但不够审慎。对 Lapwing 来说，当场创建一个 draft Skill 然后塞进文件系统，可能导致低质量 Skill 泛滥。

但 Hermes 的核心洞察是对的：**如果不在完成任务的当下做标记，到晚上自省时哪些轨迹"值得看"就需要全量扫描，效率低且容易遗漏。**

trace_mark 是一个折中——当场轻量标记，延后深度评估。这更符合 Lapwing "回顾型成长"的气质，同时解决了全量扫描的效率问题。

---

## 模式 5：Skill 内容注入为 User Message（保护 Prompt Cache）

### Hermes 怎么做的

`agent/skill_commands.py` 中，加载的 Skill 内容注入为 user message 而不是 system prompt：

> "Skill slash commands: agent/skill_commands.py scans ~/.hermes/skills/, injects as user message (not system prompt) to preserve prompt caching"

这样 system prompt 保持不变，LLM provider 的 prefix cache 不会因为 Skill 加载而失效。

### 我们现有设计

当前蓝图未明确 Skill 内容的注入位置。Brain 的 `_format_with_persona()` 已经有深度注入机制（在对话历史中特定位置插入），但 Skill 内容注入方式未定义。

### 融合方案

将加载的 Skill 内容作为一条合成的 user message 注入到当前对话历史中，而不是修改 system prompt：

```python
# brain.py — 处理请求时

def _prepare_messages(self, user_message: str, skill: Optional[Skill] = None) -> list:
    messages = self._build_system_prompt()  # 不包含 Skill 内容，保持稳定
    messages += self._get_conversation_history()
    
    if skill:
        # Skill 内容作为 user message 注入（在用户消息之前）
        skill_injection = {
            "role": "user",
            "content": f"[系统提示：以下是我积累的「{skill.name}」经验，请参考执行]\n\n{skill.content}"
        }
        # 紧接在用户实际消息之前插入
        messages.append(skill_injection)
    
    messages.append({"role": "user", "content": user_message})
    
    # 深度注入（人格提醒等）保持不变
    messages = self._inject_persona_reminders(messages)
    
    return messages
```

### 需要改的地方

```
修改文件：
  brain.py  — _prepare_messages() 中增加 Skill 注入逻辑
```

### 为什么这样做

MiniMax 有 prefix caching（或类似机制）。如果 Skill 内容塞进 system prompt，每次加载不同 Skill 都会导致 system prompt 变化，cache 失效，首 token 延迟增加、成本上升。

把 Skill 注入为 user message，system prompt 永远不变（只有 Level 0 索引是常驻的），保证 prefix cache 命中率。

额外好处：user message 形式的注入跟 Lapwing 的"深度注入"机制天然兼容——它们都是在对话历史中的特定位置插入内容，用同一套逻辑管理。

---

## 实施顺序

按依赖关系和收益排序：

```
Phase 1（Skill 工具基础）
  ├── 新增 tools/skill_tools.py（SkillListTool, SkillViewTool）
  ├── 新增 guards/skill_guard.py（SkillGuard）
  └── 修改 skill_manager.py 嵌入安全扫描

Phase 2（索引与加载）
  ├── 修改 skill_registry.py — 三级加载 + 条件激活
  ├── 修改 brain.py — Level 0 索引注入 system prompt
  └── 修改 brain.py — Skill 内容注入为 user message

Phase 3（Nudge 机制）
  ├── 新增 tools/trace_mark_tool.py
  ├── 修改 prompts/brain_system.md — 添加执行后反思段落
  └── 修改 evolution/introspection.py — 标记轨迹优先处理
```

Phase 1 和 Phase 2 可以和现有 Wave 1 蓝图的 BaseTool/ToolRegistry 基础一起做。
Phase 3 在 Skill 系统基本跑通后再加。

---

## 不采纳的 Hermes 模式

| Hermes 模式 | 不采纳原因 |
|---|---|
| MEMORY.md 2200字符上限 | Lapwing 的 file-based memory 远超此设计，不需要降级 |
| Session 冻结快照 | Lapwing 需要 session 内记忆动态更新（恋人场景要求连贯性）|
| SOUL.md 单文件人格 | Lapwing 有 diff-based evolution + constitution，比 SOUL.md 高级一个量级 |
| Slash command 触发 | Lapwing 不是命令行工具，Skill 应由 Brain 自动匹配，不需要 `/skill-name` |
| Skills Hub / 外部生态 | Lapwing 的 Skill 是她自己的能力，不是可安装的插件市场 |
| hermes-agent-self-evolution | 需要 GPU 集群跑 DSPy + RL，Lapwing 的硬件不支持 |