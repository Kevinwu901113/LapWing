# Lapwing Skill System — 完整实现蓝图

> 本文档是 Skill 系统的完整设计，供 Claude Code 实现参考。
> 所有设计决策服务于一个原则：**Skill 是 Lapwing 自己的能力，不是用户安装的插件。**

---

## 0. Skill 解决什么问题

没有 Skill 的 Lapwing，面对每个任务都是"裸想"——每次从零理解需求、规划步骤、调度 Agent。
这像一个聪明但没有工作经验的人，每件事都能做，但每次都要重新摸索。

Skill 是**积累下来的经验**。她做过一次，下次就知道怎么做更好。

关键认知：**Skill 不决定"能不能做"（没有 Skill 她照样能工作），而是决定"做得好不好、快不快、稳不稳"。**
这意味着冷启动不是阻塞性问题——没有 Skill 的 Lapwing 和现在的行为完全一样，不会有任何退化。

---

## 1. 目录结构

```
lapwing/
├── skills/                          # Skill 文件根目录
│   ├── _index.json                  # 全局索引（自动生成，勿手动编辑）
│   ├── _registry.json               # Skill 使用日志和统计
│   ├── research/                    # 类别目录
│   │   ├── literature_survey.md
│   │   └── dataset_search.md
│   ├── coding/
│   │   ├── debug_python.md
│   │   └── deploy_service.md
│   ├── daily/
│   │   ├── schedule_manage.md
│   │   └── meeting_notes.md
│   ├── content/
│   │   └── document_writing.md
│   └── system/
│       └── server_maintenance.md
│
├── skill_traces/                    # 执行轨迹存储
│   ├── 2026-03-28_literature_001.json
│   └── 2026-03-28_general_002.json
│
├── prompts/                         # 现有 prompt 目录（不变）
│   └── ...
```

### 类别（可随 Skill 增长动态添加）

初始类别建议：`research`、`coding`、`daily`、`content`、`system`。
Lapwing 在孵化新 Skill 时如果觉得不属于任何现有类别，可以创建新的类别目录。

---

## 2. Skill 文件格式

每个 Skill 是一个 Markdown 文件，由 frontmatter 元数据 + 正文执行指南组成。

### 2.1 完整 Schema

```markdown
---
id: string                    # 唯一标识，snake_case，和文件名一致
name: string                  # 显示名称
category: string              # 所属类别（对应目录名）
status: draft | active | deprecated
created: date                 # 创建日期
updated: date                 # 最后更新日期
source: trace | taught | preset | split | merged
                              # trace=从轨迹孵化, taught=Kevin教的,
                              # preset=预置/手动添加, split=从其他Skill分裂,
                              # merged=从其他Skill合并
parent_skills: [string]       # 如果是 split/merged，记录来源 Skill id
version: integer              # 版本号，每次更新 +1
use_count: integer            # 累计使用次数
last_used: date | null        # 最后使用日期
success_rate: float           # 0.0-1.0，基于用户反馈
agents: [string]              # 依赖的 Agent 列表
tools: [string]               # 依赖的工具列表
triggers:                     # 触发条件
  keywords: [string]          # 关键词列表
  patterns: [string]          # 正则表达式（可选）
  examples: [string]          # 典型请求示例（用于语义匹配阶段）
size_tokens: integer          # 正文的大约 token 数（自动计算）
---
```

### 2.2 正文结构

正文以 Lapwing **第一人称**写成，是她自己的工作笔记。

```markdown
# {name}

## 什么时候用
[描述触发场景，用自然语言。Brain 匹配时会参考这段。]

## 前置条件
[执行前需要满足的条件。Brain 在加载 Skill 后、执行前检查这些条件。]
[不要在这里硬编码用户信息。如果需要引用用户偏好，写"Kevin 的 XX 偏好（从记忆中获取）"。]

## 执行流程
[具体步骤，编号列表。]
[每一步说清楚做什么、调用什么 Agent/工具。]
[允许条件分支："如果 XX 则 YY，否则 ZZ"。]

## 注意事项
[容易踩的坑、Kevin 的特殊偏好、经验教训。]

## 失败处理
[常见失败场景和应对方式。]
[兜底策略：如果整个流程走不通怎么办。]

## 变更日志
[每次修改记一笔：日期 + 改了什么 + 为什么改。]
```

### 2.3 大小限制

单个 Skill 正文不超过 **2000 token**。超过时应拆分为多个更具体的 Skill。

原因：Skill 全文会注入 context，必须控制成本。2000 token 足以描述绝大多数任务流程。

如果某个场景确实需要更长的指南，使用**摘要+详情**模式：
- Skill 正文写摘要版（在 2000 token 内）
- 在 Skill 同目录下放一个 `{id}_detail.md` 作为详细参考
- 执行流程中标注"详细步骤见 {id}_detail.md"
- Brain 只在需要时才加载详情文件

---

## 3. 索引和检索

### 3.1 `_index.json`

自动生成，每次 Skill 文件变动时重建。

```json
{
  "version": 1,
  "updated": "2026-03-28T23:00:00Z",
  "skill_count": 12,
  "categories": ["research", "coding", "daily", "content", "system"],
  "skills": [
    {
      "id": "literature_survey",
      "name": "文献调研",
      "category": "research",
      "status": "active",
      "triggers": {
        "keywords": ["论文", "调研", "文献", "综述", "最新研究"],
        "patterns": ["最近.*论文", "调研.*论文"]
      },
      "summary": "调研学术话题，搜索筛选论文，整理文献综述",
      "agents": ["researcher", "browser"],
      "use_count": 7,
      "last_used": "2026-03-28",
      "success_rate": 0.85,
      "size_tokens": 850
    }
  ]
}
```

`skills` 数组默认按 `use_count` 降序排列（高频在前）。

### 3.2 三级检索流程

Brain 处理请求时，按以下顺序尝试匹配：

#### Level 1: 快速匹配（关键词 + 正则）

```python
def quick_match(user_request: str, index: SkillIndex) -> list[str]:
    """
    对 user_request 做关键词和正则匹配。
    返回命中的 skill id 列表（可能多个）。
    只考虑 status=active 和 status=draft 的 Skill。
    成本：几乎为零，纯字符串操作。
    """
```

如果命中 1 个：直接加载该 Skill。
如果命中多个：进入 Level 2 让 LLM 选择。
如果命中 0 个：进入 Level 2。

#### Level 2: 索引匹配（LLM 选择）

将 `_index.json` 中所有 active/draft Skill 的 `name + summary` 列表（通常几百到一两千 token）注入 context，让 LLM 从中选择 0-3 个最相关的 Skill。

```
以下是我积累的经验列表。根据当前任务，我选择最相关的经验来参考（也可能没有合适的）：

1. 文献调研 - 调研学术话题，搜索筛选论文，整理文献综述
2. Python调试 - 诊断和修复 Python 代码问题
3. 日程管理 - 管理日程、设置提醒、协调时间
...

当前任务：{user_request}
我选择的经验（0-3个，按相关度排序）：
```

如果 LLM 选了 Skill：加载对应 Skill。
如果 LLM 没选：进入无匹配流程。

#### Level 3: 语义检索（后期扩展，Skill > 200 时启用）

对所有 Skill 的 `summary + triggers.examples` 做 embedding，存入向量库。
用户请求做 embedding 后检索 top 10 候选，再交给 LLM 精选。

**当前阶段不实现。** 预留接口即可。

#### 无匹配

走通用能力处理（和没有 Skill 系统时完全一样）。
记录执行轨迹，等待后续可能的 Skill 孵化。

### 3.3 多 Skill 编排

当匹配到多个 Skill 时，Brain 需要判断它们之间的关系：

- **串行依赖**：一个 Skill 的输出是另一个的输入（如"文献调研"→"文档撰写"）。按依赖顺序逐个执行，前一个的输出作为后一个的输入。
- **并行独立**：两个 Skill 互不依赖（如"查天气"+"查日程"）。可以同时调度 Agent 执行。
- **竞争匹配**：两个 Skill 都能处理同一个请求（如"信息搜索"和"文献调研"都匹配"帮我查一下"）。选择更具体的那个。如果无法判断，选 `use_count` 更高的。

Brain 的编排决策也由 LLM 完成——加载所有候选 Skill 的摘要，让 LLM 规划执行顺序。

---

## 4. Skill 调用机制

### 4.1 注入方式

匹配到 Skill 后，将 Skill 全文作为一段"参考经验"注入当前请求的 context：

```
---参考经验开始---
[Skill 全文]
---参考经验结束---

以上是我处理类似任务时积累的经验。我会参考它来处理当前任务，
但会根据具体情况灵活调整——它是指南，不是必须严格遵循的脚本。
如果某个步骤在当前场景下不适用，我会跳过或替换。
```

### 4.2 前置条件检查

Brain 在加载 Skill 后、开始执行前，检查前置条件：

- Skill 依赖的 Agent 是否可用
- Skill 依赖的工具是否可用
- 其他前置条件（如"需要网络可用"）

如果前置条件不满足：
1. 检查是否有替代方案（如 Researcher 不可用但 Browser 可用）
2. 如果有替代方案，调整执行计划并告诉 Kevin
3. 如果没有替代方案，告诉 Kevin 当前无法执行以及原因

### 4.3 宪法约束检查

执行过程中涉及敏感操作时，无论 Skill 中是否有说明，都要检查宪法约束：

- 系统命令执行（尤其是删除、修改系统文件）
- 对外通信（发送消息、发起请求）
- 文件删除
- 任何"重大操作"（由宪法定义）

如果 Skill 流程会导致违宪，Brain 在执行前拦截，改为请求 Kevin 确认。

### 4.4 Draft Skill 的确认机制

当使用 `status: draft` 的 Skill 时：

1. 按照 Skill 流程正常执行
2. 任务完成后，在回复末尾自然地加一句反馈征求：
   - "这次是按之前的经验处理的，这样可以吗？"
   - 或者根据语境用更自然的表达
3. 确认逻辑：
   - Kevin 明确表示满意（"可以"、"好"、"没问题"、emoji 确认等）→ status 变为 active
   - Kevin 给了修改意见 → 更新 Skill 内容，保持 draft
   - Kevin 没有回应（下一次对话是不相关话题）→ 保持 draft，下次使用时再问
   - 连续 3 次使用 draft Skill 且 Kevin 都没有负面反馈 → 自动升级为 active

---

## 5. 执行轨迹

### 5.1 轨迹格式

每次任务执行（无论是否使用了 Skill）都记录轨迹：

```json
{
  "trace_id": "2026-03-28_literature_001",
  "timestamp": "2026-03-28T14:30:00Z",
  "user_request": "帮我看看最近有没有什么新的RAG相关的论文",
  "request_category": "research",
  "intent_summary": "调研最近的RAG方向论文",

  "skill_used": {
    "id": "literature_survey",
    "version": 3,
    "match_level": "quick",
    "deviated": false,
    "deviation_notes": null
  },

  "execution": {
    "agents_called": [
      {
        "agent": "researcher",
        "task": "搜索arXiv和Semantic Scholar上最近的RAG论文",
        "result_summary": "找到23篇候选，筛选后保留8篇",
        "success": true,
        "duration_seconds": 45
      }
    ],
    "tools_called": ["web_search", "file_write"],
    "total_duration_seconds": 120,
    "llm_calls": 4,
    "tokens_used": 8500
  },

  "output_summary": "整理了8篇RAG相关论文的综述，按主题分为三组",

  "user_feedback": {
    "type": "positive",
    "details": "Kevin 说"挺好的"",
    "timestamp": "2026-03-28T14:35:00Z"
  }
}
```

### 5.2 无 Skill 时的轨迹

当没有匹配到任何 Skill 时，`skill_used` 为 null，其他字段正常记录。
这些轨迹是 Skill 孵化的主要原材料。

### 5.3 轨迹存储

- 存储位置：`skill_traces/` 目录
- 文件名格式：`{date}_{category}_{sequence}.json`
- 保留策略：最近 30 天的轨迹保留完整数据，更早的只保留摘要
- 当 `skill_traces/` 目录下文件超过 500 个时，自动归档旧轨迹到 `skill_traces/archive/`

### 5.4 偏离记录

如果 Lapwing 在执行中偏离了 Skill 描述的流程（增加/跳过/替换步骤），在轨迹中记录：

```json
"skill_used": {
  "id": "literature_survey",
  "version": 3,
  "deviated": true,
  "deviation_notes": "搜索结果太少，额外增加了Google Scholar作为搜索源，不限于Skill中列出的arXiv和Semantic Scholar"
}
```

偏离记录是 Skill 更新的重要信号——如果同一个偏离反复出现，说明 Skill 该更新了。

---

## 6. Skill 生命周期

### 6.1 孵化（创建）

#### 来源一：轨迹孵化

在每晚自省环节，扫描 `skill_traces/` 中最近的轨迹，检查孵化条件：

**条件 A：重复模式**
- 最近 7 天内有 2 次以上相似的无 Skill 轨迹
- "相似"由 LLM 判断：请求类型、调用的 Agent/工具、执行步骤是否高度重叠

**条件 B：高质量单次执行**
- 单次无 Skill 轨迹，但任务复杂（agents_called >= 2 或 total_duration >= 120s）
- 且用户反馈为 positive

满足任一条件时，LLM 从轨迹中提炼 Skill：

```
以下是我最近执行的一些任务轨迹：
[轨迹内容]

我发现这些任务有共同的模式。我要把这个经验记录下来。
按照以下格式写一份 Skill 文件：
[Skill 格式模板]

注意：
- 用第一人称写，这是我自己的工作笔记
- 不要硬编码 Kevin 的具体信息，用"从记忆中获取"来引用
- 触发关键词要覆盖 Kevin 可能的各种说法
- 检查：这个 Skill 的触发条件是否和以下现有 Skill 冲突？
  [现有 Skill 触发条件列表]
```

孵化的 Skill 标记为 `status: draft`，`source: trace`。

#### 来源二：用户教授

当 Brain 检测到 Kevin 的发言包含教授意图时触发：

检测信号：
- "以后帮我做 XX 的时候..."
- "记住，这种情况下应该..."
- "下次 XX 要按这个来"
- "这个流程是：首先...然后..."

Brain 提取教授内容，生成 Skill 草稿，回复 Kevin 确认：
"好的，我记下来了——以后 [概述]。这样理解对吗？"

确认后保存为 `status: active`（用户教授的直接 active，不需要 draft 阶段），`source: taught`。

#### 来源三：手动导入

Kevin 直接在 `skills/` 目录下创建 Markdown 文件。
系统检测到新文件后自动读取、验证格式、更新 `_index.json`。
标记为 `source: preset`，`status: active`。

### 6.2 验证

每次 Skill 被创建或更新时，执行验证：

```python
def validate_skill(skill: Skill, all_skills: list[Skill]) -> ValidationResult:
    """
    检查项：
    1. 格式完整性：所有必需字段是否存在
    2. 引用有效性：依赖的 agents 和 tools 是否在系统中注册
    3. 触发冲突：triggers 是否和现有 Skill 严重重叠
       - 如果重叠率 > 70%，警告可能需要合并或调整
    4. 大小限制：正文是否超过 2000 token
    5. 宪法合规：执行流程中是否包含可能违宪的步骤
    """
```

验证失败不阻止保存，而是在 Skill 文件中添加 `_validation_warnings` 字段，
在下次自省时由 Lapwing 处理。

### 6.3 迭代更新

Skill 更新的触发条件：

**偏离累积**：同一个 Skill 在最近 5 次使用中有 3 次以上出现偏离 → 自省时检查偏离内容，判断是否要更新 Skill 步骤。

**用户纠正**：Kevin 在某次执行后给了修改意见 → 立即更新 Skill 并记录变更日志。

**成功率下降**：Skill 的 success_rate 跌到 0.5 以下 → 自省时重点审查，考虑大幅修改或 deprecated。

**主动优化**：自省时 Lapwing 回顾某个高频 Skill，认为流程可以优化 → 更新 Skill 并在下次使用时留意效果。

每次更新：version +1，updated 更新，变更日志追加。

### 6.4 分裂

当一个 Skill 变得太大（超过 2000 token）或覆盖场景太多时，自省中可能分裂：

```
我的"代码任务"Skill 覆盖了太多不同的场景——Python调试、服务部署、代码审查。
这些场景的执行流程差异很大。我要把它拆成独立的 Skill。
```

分裂后：
- 原 Skill 标记为 `status: deprecated`
- 新 Skill 标记为 `source: split`，`parent_skills` 引用原 Skill
- 原 Skill 的 `use_count` 按比例分配给新 Skill

### 6.5 合并

当几个 Skill 高度相似且 use_count 都很低时，自省中可能合并：

```
我有"搜新闻"和"搜资讯"两个 Skill，执行流程几乎一样。合成一个"信息搜索"Skill 更合理。
```

合并后：
- 原 Skill 标记为 `status: deprecated`
- 新 Skill 标记为 `source: merged`，`parent_skills` 引用所有原 Skill
- 新 Skill 的 `use_count` = 原 Skill 的 use_count 之和

### 6.6 废弃

Skill 被标记为 `deprecated` 的条件：

- 被分裂或合并（见上文）
- 连续 60 天未被使用
- success_rate 持续低于 0.3
- Lapwing 在自省中主动判断"这个经验不再有用"

Deprecated Skill 不会从文件系统删除，但不参与检索匹配。
`_index.json` 中 deprecated Skill 不计入 `skill_count`，排在列表末尾。

---

## 7. `_registry.json` — 使用日志和统计

```json
{
  "total_executions": 156,
  "total_with_skill": 89,
  "total_without_skill": 67,
  "skill_match_rate": 0.57,
  "match_level_distribution": {
    "quick": 62,
    "index": 24,
    "semantic": 0,
    "none": 67
  },
  "daily_stats": [
    {
      "date": "2026-03-28",
      "executions": 8,
      "with_skill": 5,
      "skills_created": 1,
      "skills_updated": 0
    }
  ],
  "recent_matches": [
    {
      "timestamp": "2026-03-28T14:30:00Z",
      "request_summary": "调研RAG论文",
      "skill_id": "literature_survey",
      "match_level": "quick",
      "success": true
    }
  ]
}
```

`daily_stats` 保留最近 90 天。
`recent_matches` 保留最近 50 条。

---

## 8. 冷启动流程

### 阶段零：空白状态

系统部署后，`skills/` 目录只有空的类别子目录和空的 `_index.json`。

所有请求走通用能力处理。和没有 Skill 系统时完全一样。
**唯一的新行为：每次执行完毕，记录轨迹到 `skill_traces/`。**

这个阶段可能持续 1-2 周，取决于使用频率。

### 阶段一：首批 Skill 孵化

随着轨迹积累，自省环节开始检测到重复模式。
预期在使用 1-2 周后，首批 3-5 个 Skill 自然孵化出来。

这些 Skill 都是 draft 状态，经过 Kevin 确认后变为 active。

如果 Kevin 想加速冷启动：
- 可以手动写几个预置 Skill 放进 `skills/` 目录（source: preset）
- 可以在对话中主动教她（"以后做 XX 按这个流程..."）

但这**不是必须的**——系统设计为可以完全从零自然成长。

### 阶段二：稳定增长

当 active Skill 达到 10-20 个时，Skill 匹配率应稳定在 50-70%。
大部分日常任务都能命中 Skill，执行质量和一致性明显提升。

### 阶段三：成熟

当 active Skill 达到 50+ 时，可能出现：
- 触发冲突增多 → 需要更精确的检索
- 部分 Skill 长期不用 → 自动 deprecated
- 分裂/合并操作变频繁

这是启用 Level 3 语义检索的时机。

---

## 9. 与现有系统的集成

### 9.1 与 Brain 的集成

Brain 的 system prompt 中增加 Skill 相关指令：

```markdown
## Skill 系统

我积累了一些处理特定任务的经验（Skill）。处理 Kevin 的请求时：

1. 先检查是否有相关的 Skill
2. 如果有，参考 Skill 中的经验来处理，但根据具体情况灵活调整
3. 如果没有，用我的通用能力处理
4. 无论是否使用 Skill，执行完毕后都记录轨迹

Skill 是我的经验笔记，不是必须严格遵循的脚本。
```

在实际请求处理流程中，Brain 在理解意图之后、开始执行之前，插入 Skill 检索步骤。

### 9.2 与 Agent Team 的关系

Skill 不改变 Agent 的接口和行为。Agent 仍然按原有方式接收任务和返回结果。
Skill 只影响 Brain 如何拆解任务和调度 Agent——更有经验的调度。

### 9.3 与记忆系统的关系

Skill 和记忆是互补的：

- **记忆**存的是关于 Kevin 的信息（偏好、习惯、背景）
- **Skill**存的是关于任务的经验（怎么做、注意什么）

Skill 中可以引用记忆，但不硬编码。例如：
- ✅ "参考 Kevin 的研究方向（从记忆中获取）来判断论文相关性"
- ❌ "Kevin 的研究方向是 RAG，优先选择 RAG 相关论文"

这样当 Kevin 的研究方向变了，Skill 不需要手动更新。

### 9.4 与自省系统的集成

现有的自省流程（每晚回顾对话、写日记、微调人格）扩展为：

```
原有自省流程：
1. 回顾今天的对话
2. 写日记
3. 考虑是否微调人格 prompt

扩展后：
1. 回顾今天的对话
2. 写日记
3. 考虑是否微调人格 prompt
4. 【新增】扫描今天的执行轨迹
5. 【新增】检查是否有新的 Skill 可以孵化
6. 【新增】检查现有 Skill 是否需要更新
7. 【新增】更新 _registry.json 统计数据
8. 【新增】重建 _index.json（如果有变动）
```

自省中的 Skill 相关操作的 token 成本估算：
- 加载今天的轨迹摘要：~500 token
- 加载现有 Skill 索引：~100 token / Skill
- 孵化一个新 Skill 的 LLM 调用：~2000 token（input+output）
- 更新一个 Skill 的 LLM 调用：~1500 token

在 Skill 数量 < 50 的阶段，每晚自省增加的总成本约 5000-10000 token。

### 9.5 与 Heartbeat 的关系

Heartbeat（心跳/主动消息）系统不直接触发 Skill。
但 Heartbeat 可以使用 Skill 来提升主动行为的质量。

例如，如果 Lapwing 在 Heartbeat 中决定主动给 Kevin 分享一条新闻，
她可以参考"信息搜索"Skill 来确保搜索质量。

---

## 10. 可观测性

### 10.1 对话查询

Kevin 可以在 Telegram 中直接问她：

- "你现在有哪些技能？" → 列出所有 active Skill 的名字和简述
- "你最近学了什么新技能？" → 列出最近创建/更新的 Skill
- "你的文献调研技能是怎么做的？" → 展示该 Skill 的执行流程
- "你这个技能不太好，XX 地方要改" → 触发 Skill 更新

### 10.2 日志

每次 Skill 相关操作写入日志：

- `[SKILL:MATCH]` Skill 匹配结果
- `[SKILL:LOAD]` Skill 加载
- `[SKILL:CREATE]` Skill 创建
- `[SKILL:UPDATE]` Skill 更新
- `[SKILL:DEPRECATE]` Skill 废弃
- `[SKILL:VALIDATE]` Skill 验证结果

---

## 11. 和宪法的关系（安全边界）

### Skill 不受宪法保护

Skill 是"枝叶"，可以被创建、修改、删除、废弃。
Lapwing 自己可以管理所有 Skill（创建、更新、废弃）。

### Skill 执行受宪法约束

无论 Skill 中怎么写，执行结果都不能违反宪法。
Brain 在执行流程的关键节点做宪法检查（见 4.3）。

### Skill 不能修改宪法

Skill 的执行流程中不能包含修改宪法文件的步骤。
如果 Lapwing 尝试孵化这样的 Skill，验证步骤会拦截。

### Skill 变更透明

所有 Skill 变更都有日志。Kevin 问"你最近改了什么技能"时，
Lapwing 如实汇报。不存在"偷偷改了 Skill"的可能。

---

## 12. 实现顺序

### Phase 1: 基础框架（优先实现）

- [ ] `skills/` 目录结构创建
- [ ] Skill 文件解析器（读 frontmatter + 正文）
- [ ] `_index.json` 生成器
- [ ] Level 1 快速匹配
- [ ] Level 2 索引匹配（LLM 选择）
- [ ] Skill 注入 Brain context 的流程
- [ ] 执行轨迹记录器
- [ ] `_registry.json` 更新逻辑

### Phase 2: 生命周期管理

- [ ] 自省环节的 Skill 孵化逻辑
- [ ] Draft → Active 确认机制
- [ ] 用户教授检测和 Skill 生成
- [ ] Skill 验证器
- [ ] Skill 更新逻辑（偏离检测 → 更新）

### Phase 3: 成熟特性

- [ ] Skill 分裂/合并
- [ ] 自动 deprecated 检测
- [ ] 频率排序优化
- [ ] 摘要+详情模式
- [ ] Level 3 语义检索（Skill > 200 时）

### Phase 4: 可观测性

- [ ] 对话查询接口（"你有哪些技能"）
- [ ] 日志系统集成
- [ ] `_registry.json` 统计展示

---

## 附录 A: Skill 示例 — 文献调研

```markdown
---
id: literature_survey
name: 文献调研
category: research
status: active
created: 2026-03-15
updated: 2026-03-28
source: trace
parent_skills: []
version: 3
use_count: 7
last_used: 2026-03-28
success_rate: 0.85
agents: [researcher, browser]
tools: [web_search, file_write]
triggers:
  keywords: [论文, 调研, 文献, 综述, 最新研究, paper, survey]
  patterns: ["最近.*论文", "调研.*论文", ".*综述"]
  examples:
    - "帮我调研一下最新的RAG论文"
    - "最近有没有什么关于向量数据库的新论文"
    - "帮我做个文献综述"
size_tokens: 850
---

# 文献调研

## 什么时候用
Kevin 让我调研某个学术话题、找论文、做文献综述的时候。

## 前置条件
- 调研主题明确（如果 Kevin 说得模糊，我自己先判断一个合理范围，做完再跟他确认）
- Researcher Agent 可用
- 网络可用

## 执行流程
1. 确认调研主题和时间范围（默认最近一年）
2. 让 Researcher 在 arXiv 和 Semantic Scholar 上搜索
3. 初筛：读摘要，去掉不相关的
4. 精筛：留下 5-10 篇最相关的
5. 每篇提取：标题、作者、发表时间、核心方法、关键结论
6. 按主题分组（不是按时间排）
7. 先用自然语言给 Kevin 讲整体概况和关键发现
8. 把详细整理存成文件，告诉他文件位置

## 注意事项
- 不要一上来甩论文列表，先说"大图"——这个领域最近的趋势是什么
- Kevin 的研究方向从记忆中获取，相关论文优先级更高
- 中文论文也要包含（2026-03-20 Kevin 特别提到的）
- Google Scholar 也是有用的搜索源（2026-03-28 发现的）

## 失败处理
- 搜不到结果：扩大关键词、放宽时间范围、换数据源
- Agent 超时：先告诉 Kevin 在跑，稍等
- 找到太多结果（>30篇）：加更具体的筛选条件

## 变更日志
- 2026-03-15: 从三次调研任务的轨迹中孵化创建
- 2026-03-20: Kevin 说中文论文也要包含，补充注意事项
- 2026-03-28: 执行中发现 Google Scholar 有 arXiv 遗漏的论文，增加为搜索源
```

## 附录 B: 空白 Skill 模板

```markdown
---
id:
name:
category:
status: draft
created:
updated:
source:
parent_skills: []
version: 1
use_count: 0
last_used: null
success_rate: 0.0
agents: []
tools: []
triggers:
  keywords: []
  patterns: []
  examples: []
size_tokens: 0
---

#

## 什么时候用

## 前置条件

## 执行流程

## 注意事项

## 失败处理

## 变更日志
```