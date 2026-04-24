# Agent 架构调研报告：OpenClaw / Hermes / Claude Code / Codex

**目标**: 研究四个主流 agent 系统如何解决（或规避）Lapwing 面临的 8 个系统层根因，为下一步改造提供参照。

---

## 一、四个系统的架构概览

### 1. OpenClaw

**定位**: 本地优先的个人 AI 助手平台。"Config-first"——写一个 SOUL.md，跑一条命令，agent 就上线了。

**核心架构**: 三层分离
- **Channel 层**: 消息适配器（WhatsApp, Telegram, Discord, QQ, WeChat 等 24+ 平台），负责协议归一化
- **Gateway（控制面）**: 单进程长驻服务，处理路由、会话管理、认证、事件分发。所有客户端通过 WebSocket 连接（默认 ws://127.0.0.1:18789）
- **Agent Runtime**: 推理 + 执行层，运行 agentic loop

**Agentic Loop（6 阶段）**:
1. 消息归一化（Channel Adapter）
2. 路由（Multi-agent routing，不同 channel/account 可以路由到不同 agent）
3. 上下文组装（System Prompt Builder 合并：base prompt + skills list + bootstrap context + memory）
4. 模型推理
5. 工具执行（ReAct 循环）
6. 持久化（JSONL transcript）

**System Prompt 构建**: 4 个来源动态合并
- Base prompt（核心指令）
- Skills prompt（可用技能的名称 + 描述列表）
- Bootstrap context files（工作区级别的上下文文件）
- Memory（记忆文件）

**关键组件**:
- **SOUL.md**: 纯 Markdown 文件，定义 agent 的人格、能力、行为规则。改 agent 行为 = 改文本文件
- **Context Window Guard**: 监控 token 数量，在窗口"爆炸"之前触发摘要或终止循环
- **Model Resolver**: 多 provider 管理，主模型失败自动切换备用
- **JSONL Transcripts**: 逐行审计记录（用户消息、工具调用、执行结果）
- **Skills**: 目录结构，每个技能一个 SKILL.md，支持 workspace/global/bundled 三级优先级

**主动行为系统**: Heartbeat + Cron
- **Heartbeat**: 周期性（默认 30m）在主会话中触发 agent turn。agent 读 HEARTBEAT.md checklist，决定是否需要行动。无事则回复 `HEARTBEAT_OK`（系统自动吞掉，不发给用户）
- **Cron**: 标准 cron 语法，精确时间触发，可在独立会话中运行
- **关键设计**: activeHours 限制（如 09:00-22:00 才触发），target 控制投递目标，`HEARTBEAT_OK` 自动静默

**记忆**: 三层
- Layer 1: 每日日志（`~/.openclaw/workspace/memory/YYYY-MM-DD.md`）
- Layer 2: 长期记忆（`MEMORY.md`），定期从日志中提炼
- Layer 3: 灵魂记忆（`SOUL.md` + `USER.md`），稳定核心

**规模**: 247K+ GitHub stars，MIT 协议，TypeScript/Node.js

---

### 2. Hermes Agent（NousResearch）

**定位**: "会成长的 agent"——自改进循环为核心架构关切，不只是附加功能。

**核心架构**: 以 AIAgent loop 为中心
- **AIAgent**（`run_agent.py`）：核心编排引擎，约 10,700 行，处理从 prompt 组装到工具分发到 provider failover 的一切
- **Gateway**: 后台持久服务，连接 CLI / Telegram / Discord / Slack / WhatsApp / Signal / Email 等 15+ 平台
- **Terminal Backends**: 6 种——local, Docker, SSH, Daytona, Singularity, Modal（后两种支持 serverless 休眠）

**Agentic Loop**:
1. 用户消息进入
2. Prompt 组装（prompt_builder.py）
3. 上下文压缩检查（context_compressor.py）——超阈值自动摘要
4. Provider 解析——支持 3 种 API 模式（chat_completion, messages, native）
5. 模型推理
6. 工具调用分发（model_tools.py）——支持顺序和并行执行
7. 结果追加，回到步骤 2，直到模型输出纯文本

**关键差异化: 闭环学习循环（Learning Loop）**

四个环节在不同时点触发：
1. **Periodic Nudge**: 会话中定期内部提示，agent 回看近期活动，判断是否值得持久化到记忆
2. **Skill Creation**: 任务完成后，agent 评估步骤，如果方法非平凡则提取为 SKILL.md
3. **Skill Refinement**: 下次类似任务，使用已有 skill 并根据结果改进
4. **Session Search**: FTS5 全文索引，可搜索所有历史会话

**记忆架构**: 四层
- Persistent notes（`memory.md`）：agent 主动写入的事实和偏好
- User model（`user.md`）：Honcho 辩证用户建模——不只记住你说了什么，还推断你是谁
- Skills（`skills/` 目录）：程序性记忆——不只记事实，记方法
- Session DB（SQLite + FTS5）：可搜索的完整会话历史

**迭代预算系统**:
- 每个 agent 有独立的迭代预算
- Subagents 预算独立，上限 `delegation.max_iterations`（默认 50）
- 到 100% 时强制停止并返回已完成工作的摘要
- Fallback model：主模型 429/5xx/auth 失败时自动切换

**纠正处理**: 用户纠正偏好时，Hermes **程序性地更新 memory.md 文件**，而不只是对话中"知道了"。下次会话启动时已加载更新后的记忆。

**规模**: 95K+ stars（7 周），MIT 协议，Python，支持 MiniMax M2.7

---

### 3. Claude Code

**定位**: 终端 agentic coding 系统。"Harness 是真正的产品，不只是模型。"

**核心架构**: 7 层组件
- **用户接口**: CLI / VS Code / Desktop App / Web / Slack / CI/CD
- **Agent Loop**: 所有入口汇聚到同一个循环
- **权限系统**: 5 级权限（ReadOnly → DangerFullAccess）
- **工具系统**: ~50 个内置工具 + MCP 扩展
- **状态和持久化**: JSONL transcript，session resume/fork/rewind
- **执行环境**: 本地终端

**Agentic Loop 核心设计**: 
- **单线程主循环**：一个 while 循环，模型有 tool call 就继续，输出纯文本就终止
- **说话 = 输出纯文本**: 模型直接输出 text 就是跟用户说话，不需要特殊工具调用。这是和 Lapwing 的 tell_user 机制最关键的区别
- **实时引导**: 双缓冲异步队列，用户可在 agent 工作时随时插入新指令
- **Subagent**: Task tool 产生子 agent，独立上下文和工具集，不能再产生子 agent（防递归）

**System Prompt 构建**（动态组装）:
- 系统指令（编码哲学、通讯风格、工具规则）
- Git 状态（分支、最近提交、working tree）
- CLAUDE.md（项目级持久指令）
- Auto Memory（MEMORY.md，首 200 行或 25KB）
- 工具定义（~50 个，条件加载）
- 会话历史
- 附件（plan mode 状态、TODO 列表、@-mentioned 文件）
- Skills（按需加载，不用时只加载描述）

**上下文管理（4 层 Compaction）**:
1. **Microcompact**: 清理旧工具结果（保留最近 5 个），配合 cache edit block pinning 避免缓存失效
2. **Auto-compact**: token 达 ~98% 时自动触发，摘要对话历史
3. **Manual /compact**: 用户触发，可指定关注点（"focus on API changes"）
4. **Image/PDF stripping**: 压缩时移除所有图片和 PDF 块

**关键设计模式**:
- **TodoWrite 工具**: 结构化任务列表（JSON），在工具调用后注入 TODO 状态作为 system message，防止模型在长对话中丢失目标
- **maxResultSizeChars**: 每个工具有最大输出大小，超过则存文件、返回预览 + 路径
- **Checkpoint/Revert**: 编辑文件前快照，可撤销

**规模**: Anthropic 产品，TypeScript（现有 Rust 重写），开源 CLI

---

### 4. Codex（OpenAI）

**定位**: AI 编码伙伴，从 CLI 到桌面到云的全栈 agent 平台。

**核心架构**: App Server 统一协议
- **App Server**: 双向 JSON-RPC 协议，解耦 agent 核心逻辑和客户端界面
- **三个会话原语**: Item（原子输入/输出单元）→ Turn（单次 agent 工作序列）→ Thread（持久会话容器）
- **客户端**: CLI, VS Code 扩展, Desktop App, Web App, JetBrains, Xcode

**Agentic Loop**:
1. 用户消息进入
2. Prompt 构建（包含对话历史、环境上下文、权限指令、工具定义）
3. 模型推理（Responses API）
4. Tool call → 执行 → 结果追加 → 回到步骤 2
5. 纯文本输出 → 终止
- **说话 = 纯文本输出**: 和 Claude Code 一样，模型直接输出 text 就是说话

**沙箱设计**:
- 每个任务在隔离容器中运行
- 默认禁止网络访问，只能操作指定目录
- 系统级沙箱（macOS/Linux/Windows 各有原生方案）
- 权限审批流：server 暂停 turn → 发 approval request → 客户端 allow/deny → 继续

**上下文管理**:
- **Prompt Caching 优先**: 静态内容放前面、动态内容放后面，确保 prefix cache hit
- **Auto-compaction**: token 超阈值自动摘要，替换对话历史
- **Configuration 变更处理**: 追加新消息而不是修改旧消息，保护 cache prefix
- **Stateless for ZDR**: 支持零数据保留客户，encrypted_content 处理

**多 agent 并行**:
- ThreadManager 维护活跃 thread 映射
- 每个 thread 是独立的 CodexThread
- Git worktree 支持：多 agent 同时在同一 repo 不同分支工作
- Review agent 作为特化子 agent

**关键设计**:
- **AGENTS.md**: 类似 CLAUDE.md 的项目级指令文件
- **Skills 系统**: 可复用工作流
- **Automations**: 调度后台工作
- **Codex Security**: 应用安全 agent，构建威胁模型 → 找漏洞 → 沙箱验证 → 提出修复

**规模**: 200 万+ 周活跃用户，GPT-5.4 模型，Rust 核心

---

## 二、Lapwing 8 个根因 × 4 个系统的交叉对比

### 根因 1: tell_user 把说话变成高摩擦操作

| 系统 | 解决方案 | 
|------|---------|
| **OpenClaw** | **直接输出 = 说话**。agent 输出的 text 就是发给用户的消息。没有中间机制。Gateway 负责把 text 路由到正确的 channel |
| **Hermes** | **直接输出 = 说话**。AIAgent loop 中 model 输出纯文本就是最终回复。工具调用是内部操作。Gateway 路由到 platform |
| **Claude Code** | **直接输出 = 说话**。"All text you output outside of tool use is displayed to the user." 输出 text 就是跟用户说话，零额外步骤 |
| **Codex** | **直接输出 = 说话**。Turn 以 assistant message 结束，就是用户看到的回复 |
| **Lapwing** | **tell_user 工具调用 = 说话**。所有 LLM 文本输出是内心独白。需要显式调用 tell_user 工具才能说话。6 步流程，每步可能失败 |

**结论**: **四个系统无一使用 tell_user 机制**。全部采用"直接输出 = 说话"模式。Lapwing 是唯一一个把说话变成工具调用的系统。这不是设计取舍，是设计失误。

---

### 根因 2: 意识系统（consciousness tick）空转

| 系统 | 主动行为机制 |
|------|------------|
| **OpenClaw** | **Heartbeat + Cron 分离**。Heartbeat 读 HEARTBEAT.md checklist（具体、简短），无事回 `HEARTBEAT_OK`（系统静默吞掉）。支持 activeHours、isolatedSession、lightContext。Cron 处理精确时间任务。关键：checklist 建议 <200 token |
| **Hermes** | **Cron 系统 + Periodic Nudge**。Cron 是 first-class agent task，在 gateway cron ticking 中触发，走完整 agent pipeline。Periodic Nudge 在会话内触发，不是独立 tick |
| **Claude Code** | **无主动 tick**。完全被动——用户发消息才运行。KAIROS（自主守护模式）在 feature flag 后面尚未发布 |
| **Codex** | **Automations（调度后台工作）**。类似 cron，按计划执行指定任务 |
| **Lapwing** | **consciousness tick**。每 10-30 分钟触发，给模型开放式 prompt，95%+ 输出"无事"。模型自己控制间隔但系统不遵守。无 `HEARTBEAT_OK` 静默机制。22 天约 3000-5000 次 API 调用，ROI ~0.1% |

**关键差异**:
- OpenClaw 的 Heartbeat 有**明确的 checklist**（模型知道检查什么）和**静默机制**（无事自动跳过），Lapwing 的 tick 给开放式 prompt 且没有静默机制
- OpenClaw 有 **activeHours**（深夜不触发），Lapwing 24/7 触发
- OpenClaw 有 **isolatedSession** 和 **lightContext** 选项降低 token 消耗，Lapwing 每次都加载完整上下文
- Hermes 不用独立 tick，而是在会话内做 periodic nudge——轻量得多

---

### 根因 3: 8 层 System Prompt 稀释模型注意力

| 系统 | Prompt 管理 |
|------|------------|
| **OpenClaw** | **4 源合并**: base prompt + skills list（只有名称和描述）+ bootstrap context + memory。SOUL.md 是纯 Markdown，核心身份文件。Skills 只加载描述，不加载全文 |
| **Hermes** | **prompt_builder.py 动态组装**。上下文压缩自动触发。辅助 LLM 处理侧任务（视觉、摘要），不占主 prompt 空间 |
| **Claude Code** | **条件加载**: ~50 工具有条件决定是否进入上下文。Skills 按需加载（不用时只加载描述）。Subagent 有独立上下文不占主窗口。Compaction 4 层管理。maxResultSizeChars 限制工具输出大小 |
| **Codex** | **Prefix caching 优先设计**: 静态内容固定在前，动态内容追加在后。Auto-compaction。AGENTS.md 项目级指令 |
| **Lapwing** | **8 层 pipeline**: constitutional constraints + persona + examples + depth injection + 记忆 + 工具声明 + 对话历史 + correction。全部无条件加载，不分优先级，不做压缩 |

**关键差异**:
- 所有系统都做**按需加载**——OpenClaw 的 skills 只加载名称、Claude Code 的工具条件加载、Skills 按需加载
- Claude Code 和 Codex 有**自动压缩**——上下文快满时自动摘要
- Lapwing **全部无条件加载、无压缩**，是四个系统中唯一一个

---

### 根因 4: 没有结构化工具，全靠裸推理

| 系统 | 工具策略 |
|------|---------|
| **OpenClaw** | **Skills 生态**: 13,000+ community skills，ClawHub 市场。Skills 目录结构 + SKILL.md。工具是 first-class 扩展点 |
| **Hermes** | **40+ 内置工具** + MCP 支持 + 自创建 skills。Session search、memory 管理、web 控制（search, extract, browse, vision, TTS）都是内置工具 |
| **Claude Code** | **~50 内置工具**: 文件读写、bash 执行、搜索、web fetch、TodoWrite（任务管理）、subagent 派遣。MCP 扩展 |
| **Codex** | **内置 shell/patch 工具** + MCP + ToolRouter 分发。Sandbox 内执行 |
| **Lapwing** | 有工具但**缺关键领域工具**: 无时区转换、无体育数据 API、无农历转换。依赖 web_search + 模型推理 |

**关键差异**: 这不是工具数量问题，是**高频使用场景缺少专用工具**。Kevin 最常问的时区、体育、日期问题都没有对应工具。

---

### 根因 5: Correction/Learning 不闭环

| 系统 | 纠正和学习 |
|------|-----------|
| **OpenClaw** | **SOUL.md 直接修改**: 规则变更 = 改文件。下次 heartbeat/对话自动加载。三层记忆系统确保长期记忆沉淀 |
| **Hermes** | **程序性更新 memory.md**: 用户纠正偏好时，agent **直接写入文件**。下次会话自动加载。Learning loop 从成功任务中提取 skill。Periodic nudge 定期让 agent 审视近期活动并持久化 |
| **Claude Code** | **CLAUDE.md + Auto Memory**: 持久规则放 CLAUDE.md（项目级），Claude 自动学习放 MEMORY.md。每次会话开始加载 |
| **Codex** | **AGENTS.md + Memories System**: 项目级指令 + 持久记忆 |
| **Lapwing** | **correction 文件**: 写入 `/data/memory/notes/correction/`，但加载依赖记忆检索系统判断"相关性"。不保证每次加载。无违反计数。无自动升级机制 |

**关键差异**: 
- OpenClaw/Hermes/Claude Code 都保证**每次会话启动时加载核心指令文件**（SOUL.md / memory.md / CLAUDE.md）
- Lapwing 的 correction 加载取决于检索系统，可能"认为不相关"而跳过
- Hermes 有**闭环**：纠正 → 写文件 → 下次加载 → 应用。Lapwing 的闭环在"下次加载"这步断了

---

### 根因 6: 输出无质量门禁

| 系统 | 质量控制 |
|------|---------|
| **OpenClaw** | HEARTBEAT_OK 静默机制（无意义输出不发送）。Skills 提供结构化工作流减少自由发挥空间 |
| **Hermes** | Iteration budget（到上限强制停止 + 返回摘要）。Fallback model（主模型出错自动切换）。Governance subsystem（审计 + 危险操作阻断） |
| **Claude Code** | **权限系统 5 级**（执行前检查）。maxResultSizeChars（工具输出限制）。TodoWrite（任务追踪防丢失）。Checkpoint（可撤销） |
| **Codex** | **Approval flow**（执行前审批）。沙箱（隔离执行环境）。Diff review（变更可审查） |
| **Lapwing** | 无。模型输出 → tell_user → 直接发送。无验证步骤、无事实检查、无一致性检查 |

---

### 根因 7: 无断路器，失败操作无限重试

| 系统 | 失败处理 |
|------|---------|
| **OpenClaw** | **Model Resolver**: 主模型失败自动 cool down + 切换备用。Heartbeat 如果主队列 busy 则跳过重试。超时配置（timeoutSeconds） |
| **Hermes** | **Iteration budget**: 硬上限防无限循环。Fallback model chain。Subagent 预算独立。429/5xx/auth 错误自动触发 fallback |
| **Claude Code** | **Tool timeout**: 长操作有可配置超时。Compaction 自动触发防上下文溢出。Loop 终止条件明确（纯文本 or 硬步数上限 or 错误传播） |
| **Codex** | **沙箱隔离**: 任务在容器中运行，默认禁网。Thread 级独立。Turn 有超时 |
| **Lapwing** | 无断路器。小红书 20+ 次重试、QQ 30+ 次重试、错误路径 50+ 次重试。无指数退避、无失败缓存 |

---

### 根因 8: 输出管道有漏洞

| 系统 | 输出清洗 |
|------|---------|
| **OpenClaw** | Gateway 控制输出路由，工具调用结果不直接暴露给用户。Heartbeat reasoning 默认不投递（需显式开启 includeReasoning） |
| **Hermes** | Agent loop 中工具调用结果是内部状态，最终回复是模型的纯文本输出。KawaiiSpinner 等 display 模块处理用户可见输出 |
| **Claude Code** | "All text you output outside of tool use is displayed to the user" —— 工具结果不泄露，只有模型主动输出的 text 可见。Compaction 保证旧工具结果被清理 |
| **Codex** | Event/Item 协议明确区分 agent message（可见）和 tool execution（内部）。客户端只渲染 agent message |
| **Lapwing** | 正则黑名单过滤，覆盖不全。[TOOL_CALL] JSON、源代码、<user_visible> 标签、乱码都曾泄露给用户 |

**关键差异**: 所有系统都是**架构层面区分可见/不可见**——模型输出 text = 可见，tool call/result = 不可见。Lapwing 用正则后处理来分离，本质上是在用创可贴解决架构问题。

---

## 三、提取的关键架构模式

### 模式 1: "说话零摩擦"原则

四个系统一致采用：**模型输出纯文本 = 说话，工具调用 = 内部操作**。这是最基本的架构决策，不需要任何额外机制就能让 agent "正常说话"。

Lapwing 的 tell_user 是一个需要被废除的反模式。

### 模式 2: "文件即真理"

- OpenClaw: SOUL.md, HEARTBEAT.md, MEMORY.md
- Hermes: memory.md, user.md, SKILL.md
- Claude Code: CLAUDE.md, MEMORY.md
- Codex: AGENTS.md, memories

所有系统的核心配置和学习都通过**文件**管理，且**每次会话启动时保证加载**。Lapwing 的文件记忆系统方向正确，但加载机制不可靠。

### 模式 3: "Checklist > 开放式 Prompt" 用于主动行为

OpenClaw 的 HEARTBEAT.md 是一个简短、具体的 checklist（<200 token）：
```markdown
- 检查是否有到期提醒
- 检查邮箱是否有紧急邮件  
- 如果都没有回复 HEARTBEAT_OK
```

Lapwing 的意识 tick prompt 是开放式的："你可以做任何事，或者什么都不做。"

前者给模型明确的行动框架，后者给模型逃避的许可。

### 模式 4: "静默机制"——无事则不输出

OpenClaw: `HEARTBEAT_OK` → 系统自动吞掉，用户看不到。
Hermes: Periodic nudge 在内部运行，只在发现值得持久化的内容时写入文件。

Lapwing: 每个 tick 都会产生 inner_thought（消耗 API），即使结论是"无事"。

### 模式 5: "上下文是稀缺资源"——按需加载 + 自动压缩

- Claude Code: 4 层 compaction + 工具条件加载 + Skills 按需加载 + Subagent 独立上下文
- Codex: Prefix caching + auto-compaction + 配置变更追加而非修改
- Hermes: context_compressor.py 超阈值自动摘要
- OpenClaw: Context Window Guard + lightContext 模式

Lapwing: 8 层全量加载，无压缩，无按需机制。

### 模式 6: "预算 + 断路器"防止失控

- Hermes: iteration budget，到上限强制停止
- Claude Code: 硬步数上限 + tool timeout + maxResultSizeChars
- Codex: Turn 超时 + 沙箱隔离
- OpenClaw: heartbeat timeoutSeconds + queue busy 则跳过

Lapwing: 无预算、无超时、无断路器。

### 模式 7: "纠正即更新文件"——闭环学习

Hermes 的做法最值得参考：
1. 用户纠正 → agent 调用 memory 工具 → 写入 memory.md
2. 下次会话启动 → 自动加载 memory.md
3. Periodic nudge → 定期审视并持久化
4. 任务完成 → 提取 skill → 下次复用

闭环的关键是**步骤 2 不依赖检索系统的"相关性判断"**——直接加载。

---

## 四、对 Lapwing 改造的具体启示

### 启示 1: 废除 tell_user，采用"直接输出 = 说话"

所有四个系统验证了这个模式。对话场景中，模型输出纯文本 = 发送给用户。内部思考通过 thinking block（如 MiniMax 的原生 think block）或系统层分离，不需要工具调用。

### 启示 2: 用 Heartbeat 模式重写意识系统

参考 OpenClaw:
- 明确的 HEARTBEAT.md checklist（<200 token）
- `HEARTBEAT_OK` 静默机制
- activeHours 限制
- isolatedSession + lightContext 降低消耗
- 系统控制间隔，不让模型决定

### 启示 3: 大幅精简 System Prompt

从 8 层降到核心 3-4 层：
- **Layer 1**: Constitutional constraints（不可变的硬规则）
- **Layer 2**: Persona（SOUL.md 等价物，占比 ≥30%）
- **Layer 3**: 工具定义（按需加载）
- **Layer 4**: 动态上下文（记忆、对话历史，有压缩机制）

### 启示 4: 构建关键领域的结构化工具

高优先级：convert_timezone, get_mlb_data, lunar_solar_convert
中优先级：image_search_pipeline, qq_status_check

### 启示 5: 保证 correction 每次加载

参考 Hermes/Claude Code:
- 核心 correction 写入 constitutional constraints 文件（相当于 CLAUDE.md 的项目规则）
- 每次会话启动**强制加载**，不经过检索系统
- 高频违反的 correction 自动升级优先级

### 启示 6: 加系统级断路器

- 失败缓存：记录最近 N 小时失败的操作
- 指数退避：失败后 10min → 30min → 2h
- Iteration budget：每个对话 turn 最多 N 个工具调用
- Tick budget：每个 heartbeat 最多 M 个工具调用

### 启示 7: 输出可见性在架构层而非正则层解决

模型输出 text = 可见，tool call/result = 不可见。这是架构决策而非后处理过滤。废除 tell_user 后这个问题自动解决。

---

## 五、总结：Lapwing vs 行业实践的差距

| 维度 | 行业标准做法 | Lapwing 现状 | 差距 |
|------|------------|-------------|------|
| 说话机制 | 直接输出 = 说话 | tell_user 工具调用 | **根本性反模式** |
| 主动行为 | Checklist + 静默 + 时间窗 | 开放式 prompt + 24/7 + 无静默 | **设计理念错误** |
| Prompt 管理 | 按需加载 + 自动压缩 | 8 层全量 + 无压缩 | **严重** |
| 工具覆盖 | 高频场景有专用工具 | 高频场景靠裸推理 | **中等** |
| 纠正学习 | 写文件 + 保证加载 + 闭环 | 写文件 + 检索加载 + 断链 | **中等** |
| 失败处理 | 预算 + 超时 + 断路器 | 无限重试 | **严重** |
| 输出隔离 | 架构层 text/tool 分离 | 正则后处理过滤 | **方向性错误** |

**核心发现**: Lapwing 的问题不是个别功能缺失，而是**若干关键架构决策与行业实践相反**。最大的两个——tell_user 和意识系统——是自创的设计，没有任何主流 agent 采用类似方案，且被数据证明效果极差。修复这两个就能解决 50%+ 的体验问题。
