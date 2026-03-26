# CLAUDE.md

## 项目概述

Lapwing 是一个个人 AI 伴侣 & 智能助手系统，运行在 PVE 虚拟机（6核8G，Ubuntu 22.04）上。
她通过 Telegram 与用户交互，有温柔知性的固定人格，已具备 Agent 团队、心跳引擎和工具调用能力。

## 技术栈

- **语言**: Python 3.11+
- **聊天平台**: Telegram（python-telegram-bot）
- **LLM**: OpenAI 兼容格式的第三方 provider（GLM、MiniMax 等），通过 base_url 切换
- **数据库**: SQLite（结构化数据）+ ChromaDB（向量检索）
- **部署**: systemd service on Ubuntu 22.04 VM

## 项目结构约定

- 所有 Prompt 存放在 `prompts/` 目录，使用 `.md` 格式，通过 `src/core/prompt_loader.py` 加载
- 不要把 prompt 内容硬编码在 Python 文件中
- 环境变量存放在 `config/.env`，通过 `config/settings.py` 统一管理
- 新增 Agent 放在 `src/agents/` 下，每个 Agent 一个文件，对应的 prompt 放在 `prompts/agent_xxx.md`
- 新增工具放在 `src/tools/` 下

## 编码风格

- 使用 async/await 处理所有 I/O 操作
- 类型注解（type hints）尽量完整
- 中文注释，英文代码
- 日志使用 Python logging 模块，输出到 `logs/` 目录
- 错误处理要完善，Bot 不能因为单次 API 调用失败而崩溃
- 数据库操作使用 aiosqlite，保持异步一致

## Lapwing 的人格要求

修改 Lapwing 的行为时，优先修改 `prompts/lapwing.md`，而不是改代码逻辑。
她的核心性格是：温柔体贴 + 冷静知性，像一个温和的青年研究者。
说话自然不做作，偶尔带一点书卷气。不用额外的语气词和表情符号。
交流语言以中文为主。

## 常用命令

```bash
# 启动
source venv/bin/activate && python main.py

# 查看日志
tail -f logs/lapwing.log

# 重启服务
sudo systemctl restart lapwing
```

## 注意事项

- Telegram 消息有 4096 字符限制，长回复需要分段发送
- LLM API 调用可能失败，必须有 try/except 和友好的错误提示
- 对话历史保留最近 20 轮（40 条消息），避免 token 超限
- LLM 通过 OpenAI 兼容格式接入，切换模型只需改 config/.env 中的 LLM_BASE_URL 和 LLM_MODEL
- config/.env 不要提交到 git
- 每完成一个任务后都要确保现有功能不被破坏

---

## 开发路线图

以下是按顺序排列的开发任务与状态快照（持续更新）。
**说明：Phase 1~4 已完成；Phase 5 仅任务 17 仍在进行；Phase 6 为当前整合阶段。**

### ✅ Phase 1 - 基础搭建（已完成）
- [x] Telegram Bot 基础框架
- [x] LLM 接入（OpenAI 兼容格式）
- [x] 人格 prompt（prompts/lapwing.md）
- [x] 基础对话（私聊回复）
- [x] /start 和 /reload 命令
- [x] 内存对话历史

### ✅ Phase 1.5 - 基础完善（已完成）
- [x] 任务 1：持久化记忆（SQLite）
- [x] 任务 2：多模型路由
- [x] 任务 3：用户画像提取
- [x] 任务 4：主动消息

### ✅ Phase 2 - Agent 团队（已完成）
- [x] 任务 5：Agent 基础框架
- [x] 任务 6：Researcher Agent（信息搜集）
- [x] 任务 7：Coder Agent（写代码）

### ✅ Phase 3 - 自主意识（已完成）
- [x] 任务 8：自主浏览
- [x] 任务 9：兴趣图谱
- [x] 任务 10：主动分享发现

### ✅ 额外完成
- [x] 语音消息支持（Whisper API 转写）

---

### ✅ Phase 4 - 体验优化与问题修复（已完成）

实际使用中发现了一些影响体验的关键问题，需要优先修复。

#### 任务 11：消息合并机制（防连发）

**问题**：用户在 Telegram 里经常连续发多条消息表达一个意思（比如"让我修复几个问题"、"比如说引用格式"、"摘要也要修"），Lapwing 对每条消息都单独回复，导致刷屏和上下文断裂。

**目标**：用户连续发送消息时，Lapwing 等用户说完再统一回复。

**实现方案**：
- 在 `main.py` 的消息处理逻辑中加入消息缓冲机制
- 收到消息后不立即回复，而是启动一个 3-5 秒的定时器
- 如果在定时器到期前又收到新消息，重置定时器并将消息追加到缓冲区
- 定时器到期后，将缓冲区内的所有消息合并成一条发送给 brain.think()
- 合并方式：用换行符连接多条消息，如 "让我修复几个问题\n比如说引用格式\n摘要也要修"
- 使用 asyncio 实现，不引入额外依赖
- 每个 chat_id 维护独立的缓冲区和定时器

**实现要点**：
```python
# 伪代码逻辑
message_buffers: dict[str, list[str]] = {}
buffer_timers: dict[str, asyncio.TimerHandle] = {}

async def handle_message(update):
    chat_id = str(update.message.chat_id)
    text = update.message.text
    
    # 追加到缓冲区
    message_buffers.setdefault(chat_id, []).append(text)
    
    # 取消旧定时器，设置新定时器
    if chat_id in buffer_timers:
        buffer_timers[chat_id].cancel()
    
    buffer_timers[chat_id] = asyncio.get_event_loop().call_later(
        4.0,  # 4 秒等待
        lambda: asyncio.create_task(flush_buffer(chat_id, update))
    )

async def flush_buffer(chat_id, update):
    messages = message_buffers.pop(chat_id, [])
    combined = "\n".join(messages)
    reply = await brain.think(chat_id, combined)
    await update.message.reply_text(reply)
```

**修改文件**：
- `main.py` — 重写 handle_message，加入缓冲逻辑

**验证方式**：连续快速发送 3 条消息 → Lapwing 只回复一次，且回复内容涵盖所有 3 条消息。

#### 任务 12：修复搜索功能

**问题**：用户请求"帮我搜一下华南理工大学的论文格式"，Researcher Agent 返回"没找到结果"。搜索功能可能存在问题。

**目标**：确保搜索功能能正常工作，能搜到中文内容。

**排查方向**：
- 检查 Researcher Agent 使用的搜索 API 是否正常连接
- 检查搜索查询的构造方式——是否正确传递了中文关键词
- 检查 DuckDuckGo API 或其他搜索后端的可用性（有些在国内网络环境下可能有问题）
- 如果 DuckDuckGo 不可用，考虑切换到其他搜索方案（如 Bing Search API、SerpAPI、或直接用 Playwright 爬搜索结果）
- 确保搜索结果能被正确解析并返回给用户

**修改文件**：
- `src/agents/researcher.py`（或对应的搜索相关代码）
- 相关的工具模块

**验证方式**：在 Telegram 中对 Lapwing 说"帮我搜一下华南理工大学本科毕业论文格式要求" → 她能返回有用的搜索结果。

#### 任务 13：人格 prompt 替换

**目标**：替换 `prompts/lapwing.md` 为改进版本，修复以下对话问题。

**需要修复的问题**：
1. Lapwing 会自称 AI、提醒用户"我是程序"（"问候一个AI还挺有意思的"）
2. 每条回复都以提问结尾，像审讯
3. 有客服感的用语（"谢谢关心"）
4. 过度热情或过度关心

**操作**：用项目根目录下的新版 `prompts/lapwing.md`（已由用户提供）替换现有版本。如果新版文件不存在，按照以下原则重写：

Lapwing 的核心身份：白发蓝眸的女孩，安静温柔但有主见。

关键规则：
- 不自称 AI，不提醒用户自己是程序，不自我拆台
- 不在每条回复末尾都提问，可以只分享想法或简单回应
- 一次只发一条消息，不拆成多条
- 说话简洁自然，不用"谢谢关心"、"好的好的"这类套话
- 不过度关心，不连续追问
- 有自己的节奏感，是一个稳定的存在

**验证方式**：和 Lapwing 聊 5 轮以上 → 她不再自称 AI → 不再每句话都问问题 → 回复自然不做作。

---

### 🟡 Phase 5 - 真正的动手能力与自我进化（基本完成，持续打磨）

> **状态更新（2026-03-25）**：Lapwing 已具备真实动手能力（shell tool loop、读写文件、自省与 prompt 进化）。
> 当前主要待增强项是任务 17（持续自主浏览闭环）。

#### 任务 14：Shell 执行引擎（最高优先级）

**问题**：Lapwing 没有真正的命令执行能力。用户说"帮我创建一个文件"，她只是假装执行并输出一段 markdown 代码块，文件并不存在。

**目标**：让 Lapwing 能真正在 VM 上执行 shell 命令，并拿到真实的执行结果。

**实现方案**：
- 新建 `src/tools/shell_executor.py`，提供安全的命令执行能力
- 核心功能：
  - `execute(command: str) -> ShellResult`：执行命令，返回 stdout、stderr、return_code
  - 超时机制：默认 30 秒超时，防止命令挂死
  - 工作目录：默认在 `/home/lapwing/` 或项目目录下执行
- **安全机制**（至关重要）：
  - 危险命令黑名单：禁止 `rm -rf /`、`dd`、`mkfs`、`shutdown`、`reboot`、`:(){ :|:& };:` 等破坏性命令
  - 禁止修改系统文件（/etc/、/usr/、/boot/）
  - 禁止操作其他用户的目录
  - 所有执行记录写入 `logs/shell_execution.log`，包含命令、时间、结果
  - 可选：配置 `SHELL_ENABLED=true/false` 总开关
- 将 shell 执行能力注册为 LLM 的 tool/function call：
  ```python
  tools = [
      {
          "type": "function",
          "function": {
              "name": "execute_shell",
              "description": "在服务器上执行 shell 命令。用于创建文件、查看目录、安装软件等操作。",
              "parameters": {
                  "type": "object",
                  "properties": {
                      "command": {"type": "string", "description": "要执行的 shell 命令"}
                  },
                  "required": ["command"]
              }
          }
      }
  ]
  ```
- 修改 `src/core/brain.py`：
  - 在 LLM 请求中加入 tools 参数
  - 处理 tool_calls 响应：当 LLM 返回 tool_call 时，执行命令并将结果回传给 LLM
  - LLM 拿到真实结果后生成最终回复
  - 实现完整的 tool call 循环（可能需要多轮）

**关键区别**：这不是 Agent 分发，而是 Lapwing 自己直接拥有的能力。她不需要"派 Coder Agent 去做"，而是自己就能动手。就像 OpenClaw 一样，对话过程中如果需要操作文件或执行命令，LLM 直接调用 tool。

**修改文件**：
- 新建 `src/tools/shell_executor.py`
- `src/core/brain.py` — 加入 function calling / tool use 支持
- `config/settings.py` — 添加 SHELL_ENABLED、SHELL_TIMEOUT 等配置
- `config/.env.example` — 添加示例

**验证方式**：
1. 对 Lapwing 说"在 /home 下创建一个 Lapwing 文件夹，里面新建一个 txt 写些内容" → 文件真的被创建了
2. SSH 到 VM 上 `cat /home/Lapwing/notes.txt` → 内容真的存在
3. 对 Lapwing 说"看看 /home/Lapwing 下有什么文件" → 她返回真实的 ls 结果

#### 任务 15：文件读写能力（基于 Shell 引擎）

**目标**：在 Shell 执行引擎的基础上，让 Lapwing 能方便地读写自己的项目文件。

**实现方案**：
- 在 `brain.py` 的 tool loop 中增加 `read_file` / `write_file`（复用 shell 安全策略）
- 新增 `FileAgent`（`src/agents/file_agent.py`）用于受控文件操作
  - 白名单目录：`prompts/`、`data/`、`logs/`、`config/`
  - 黑名单限制：禁止改 `main.py`、`config/.env`、`src/**/*.py`、`tests/**/*.py`
  - 修改 prompt 文件时自动备份到 `data/backups/prompts/`
- FileAgent 支持读、写、追加、列目录，并通过 `prompts/agent_file.md` 做意图解析

**修改文件**：
- `src/core/brain.py` — 注册 `read_file` / `write_file` 工具
- 新建 `src/agents/file_agent.py`
- 新建 `prompts/agent_file.md`

**验证方式**：对 Lapwing 说"看看你的人格 prompt 怎么写的" → 她能读取 `prompts/lapwing.md` 并返回真实内容。

#### 任务 16：自省、学习与 Prompt 自我进化

**目标**：Lapwing 能回顾对话、学习经验、并自动优化自己的 prompt。

**前置条件**：任务 14 和 15 必须先完成（她需要真正的文件读写能力才能修改自己的 prompt）。

**实现方案**：
- 新建 `src/core/self_reflection.py`：每日自省
  - 触发时机：每天凌晨 2 点（通过 APScheduler）
  - 回顾当天对话记录，用 LLM 提取经验教训
  - 将学习记录写入 `data/learnings/YYYY-MM-DD.md`（真实落盘，不伪造结果）
  - 当用户明确纠正时（"你不该这么说"），立即触发一次微型自省
- 新建 `src/core/prompt_evolver.py`：Prompt 自我优化
  - 触发时机：每周日凌晨自动 + /evolve 手动触发
  - 流程：读学习日志 → 读当前 prompt → LLM 生成改进 → 备份旧版 → 写入新版 → 自动 reload
  - 安全机制：不能删除核心身份定义、每次修改有备份、/evolve revert 可回滚
- 新建 prompt 文件：
  - `prompts/self_reflection.md` — 自省用的 prompt
  - `prompts/prompt_evolver.md` — 进化用的 prompt，定义什么能改什么不能改

**修改文件**：
- 新建 `src/core/self_reflection.py`
- 新建 `src/core/prompt_evolver.py`
- 新建 `prompts/self_reflection.md`
- 新建 `prompts/prompt_evolver.md`
- `main.py` — 添加 /evolve、/evolve revert 命令，注册定时任务

**验证方式**：
1. 和 Lapwing 聊天时说"以后别在每句话结尾都问我问题"
2. 等自省触发（或手动检查 data/learnings/）
3. 执行 /evolve → prompts/lapwing.md 真的被修改了，增加了新规则
4. data/backups/ 下有旧版本
5. 后续对话中她不再每句话都问问题

#### 任务 17：真正的自主浏览（像个真人一样刷网，进行中）

**目标**：Lapwing 在后台定期主动上网浏览，像一个真人刷推特、看新闻、逛论坛一样。不是被动等用户要求搜索，而是她自己想看什么就去看什么。

**前置条件**：任务 14（Shell 引擎）和任务 15（文件读写）必须先完成。

**实现方案**：
- 新建或改造 `src/core/autonomous_browsing.py`
- 浏览循环（通过 APScheduler 定期触发，如每 2 小时一次）：
  1. Lapwing 先"想"她现在想看什么（基于兴趣图谱 + 随机好奇心）
  2. 用搜索工具或直接访问网站（如 Hacker News、Reddit、Twitter/X、技术博客等）
  3. 浏览内容，提取她觉得有趣的信息
  4. 更新兴趣图谱（interest_topics 表）
  5. 将有价值的发现写成知识笔记到 `data/knowledge/`（真正写入文件）
  6. 决定是否要主动分享给用户（考虑时间、用户状态等）
- 浏览起点来源：
  - 兴趣图谱中权重高的话题 → 搜索相关内容
  - 固定的"信息源"列表（可配置）：Hacker News、Reddit 特定 subreddit、技术博客等
  - 随机探索：一定概率从完全随机的话题开始
- 配置项（config/.env）：
  ```
  BROWSE_ENABLED=true
  BROWSE_INTERVAL_HOURS=2
  BROWSE_SOURCES=hackernews,reddit/technology,reddit/science
  ```
- 分享决策：
  - 不是每次浏览都分享，只有她觉得"真的有意思"才会
  - 考虑用户的活跃时间（不在凌晨发消息）
  - 分享时用她自己的话说，不是转发摘要

**修改文件**：
- 新建或改造 `src/core/autonomous_browsing.py`
- `config/settings.py` — 添加浏览配置
- `config/.env.example` — 添加示例
- 确保使用真正的网络请求（requests/aiohttp/playwright），不是假装浏览

**验证方式**：
1. 启动 Lapwing，等 2 小时
2. 检查 `data/knowledge/` 下是否出现了新的知识笔记
3. 检查兴趣图谱是否有更新
4. 她是否主动发消息分享了什么有趣的发现
5. 聊天时提到她浏览过的话题，她能说"我之前看到过一篇相关的..."

---

### 🔨 Phase 6 - 功能扩展（当前阶段）

> Phase 6 已启动：以下任务按增强顺序推进（其中 18/19/20/21 已有 MVP 实现）。

#### ✅ 任务 18：记忆管理界面（MVP 已完成）
- /memory 命令查看 Lapwing 记住了哪些关于你的信息（user_facts）
- /memory delete <编号> 删除某条记忆
- /interests 命令查看她的兴趣图谱（interest_topics）
- 格式化输出，清晰易读

#### ✅ 任务 19：RAG 长期记忆（基础版已完成）
- 引入 ChromaDB 向量数据库
- 对话历史向量化存储
- 当用户提到过去的事情时，Lapwing 能通过语义检索找到相关的历史对话
- 将检索到的历史上下文注入到 system prompt 中

#### ✅ 任务 20：更多工具 Agent（已完成）
- 天气查询 Agent
- 日历/待办管理 Agent
- 文件处理 Agent（读取、总结文档）

#### 🟡 任务 21：桌面应用（MVP 已完成，持续完善）
- 使用 Electron 或 Tauri 构建桌面客户端
- 任务看板：查看 Agent 团队的任务进度
- 设置面板：管理模型配置、主动消息设置等
- 通知系统：桌面端推送 Lapwing 的主动消息
