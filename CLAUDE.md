# CLAUDE.md

## 项目概述

Lapwing 是一个个人 AI 伴侣 & 智能助手系统，运行在 PVE 虚拟机（6核8G，Ubuntu 22.04）上。
她通过 Telegram 与用户交互，有温柔知性的固定人格，未来会扩展 Agent 团队和自主浏览能力。

## 技术栈

- **语言**: Python 3.11+
- **聊天平台**: Telegram（python-telegram-bot）
- **LLM**: OpenAI 兼容格式的第三方 provider（GLM、MiniMax 等），通过 base_url 切换
- **数据库**: SQLite（结构化数据）+ ChromaDB（向量检索，后续引入）
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

以下是按顺序排列的开发任务。每个任务都为下一个任务打基础。
**请按顺序逐个完成，每完成一个任务打勾。**

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

### ✅ Phase 4 - 体验优化与问题修复（已完成）
- [x] 任务 11：消息合并机制（防连发）
- [x] 任务 12：修复搜索功能
- [x] 任务 13：人格 prompt 替换

### ✅ Phase 5 - 自我进化能力（已完成）
- [x] 任务 14：FileAgent（读写项目文件，带安全白名单）
- [x] 任务 15：自省与学习日志（data/learnings/，实时纠正检测）
- [x] 任务 16：Prompt 自我优化（/evolve 命令，每周自动进化）
- [x] 任务 17：知识积累（data/knowledge/，浏览后保存笔记，对话中自动引用）

---

### 🔨 Phase 4 - 体验优化与问题修复（已完成，详细记录如下）

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

### 📋 Phase 5 - 自我进化能力（已完成，详细记录如下）

> Lapwing 能在对话和自主探索过程中修改自己的文件、优化自己的 prompt，实现持续自我进化。
> 灵感来源：OpenClaw 的 self-improving-agent 机制。

#### 任务 14：文件系统 Agent（FileAgent）

**目标**：让 Lapwing 能读写自己项目目录下的文件。

**实现方案**：
- 新建 `src/agents/file_agent.py`，继承 BaseAgent
- 提供以下能力：
  - `read_file(path)` — 读取文件内容
  - `write_file(path, content)` — 写入/覆盖文件
  - `append_file(path, content)` — 追加内容
  - `list_files(directory)` — 列出目录内容
- **安全边界**（非常重要）：
  - 白名单目录：只允许操作 `prompts/`、`data/`、`logs/`、`config/` 下的文件
  - 黑名单文件：禁止修改 `main.py`、`src/` 下的 Python 代码文件
  - 所有写入操作记录到日志
  - 修改 prompt 文件前自动备份到 `data/backups/prompts/`
- 在 `prompts/agent_file.md` 中定义 FileAgent 的行为准则

**修改文件**：
- 新建 `src/agents/file_agent.py`
- 新建 `prompts/agent_file.md`
- 注册到 AgentRegistry

**验证方式**：对 Lapwing 说"帮我看看你的人格 prompt 是怎么写的" → 她能读取并展示 prompts/lapwing.md 的内容。

#### 任务 15：自省与学习日志

**目标**：Lapwing 能回顾自己的对话表现，记录学到的东西。

**实现方案**：
- 新建 `src/core/self_reflection.py`
- 在 `data/learnings/` 目录下以日期为文件名记录学习日志（Markdown 格式），如 `data/learnings/2026-03-24.md`
- 触发时机：
  1. 每天定时（如凌晨 2 点），回顾当天的对话，提取经验
  2. 用户明确纠正 Lapwing 时（如"你不应该这样说"、"记住以后别这么做"）
- 学习日志格式：
  ```markdown
  ## 2026-03-24
  
  ### 对话反思
  - 用户不喜欢我在回复结尾总是提问，以后应该更自然地回应
  - 用户提到华南理工大学论文格式时我搜索失败了，搜索关键词应该更具体
  
  ### 用户偏好更新
  - 用户在华南理工大学读书，正在写 RAG 方向的论文
  ```
- 新建 `prompts/self_reflection.md` 作为自省用的 prompt

**修改文件**：
- 新建 `src/core/self_reflection.py`
- 新建 `prompts/self_reflection.md`
- 在主动消息调度器中添加每日自省任务

**验证方式**：和 Lapwing 聊天时纠正她的某个行为 → 次日检查 data/learnings/ 下出现了记录。

#### 任务 16：Prompt 自我优化

**目标**：Lapwing 能基于学习日志自动优化自己的人格 prompt。

**实现方案**：
- 新建 `src/core/prompt_evolver.py`
- 工作流程：
  1. 读取 `data/learnings/` 中最近 7 天的学习日志
  2. 读取当前的 `prompts/lapwing.md`
  3. 用 LLM 分析学习日志，生成 prompt 的改进建议
  4. 自动应用改进（追加新规则或修改现有规则）
  5. 写入前自动备份旧版本到 `data/backups/prompts/lapwing_YYYYMMDD_HHMMSS.md`
  6. 写入后自动 reload prompt
- 触发时机：
  - 每周一次自动触发（如每周日凌晨）
  - 用户通过 /evolve 命令手动触发
- **安全机制**：
  - 不能删除核心人格定义（"你是 Lapwing"、"白发蓝眸"等身份信息）
  - 只能在"说话风格"和"行为准则"部分追加或修改规则
  - 每次修改后在日志中记录修改内容
  - 用户可以通过 /evolve revert 回滚到上一个版本
- 新建 `prompts/prompt_evolver.md` 定义优化的原则和约束

**修改文件**：
- 新建 `src/core/prompt_evolver.py`
- 新建 `prompts/prompt_evolver.md`
- `main.py` — 添加 /evolve 和 /evolve revert 命令
- 在调度器中添加每周自动优化任务

**验证方式**：
1. 连续几天和 Lapwing 聊天，纠正她一些说话方式
2. 手动执行 /evolve
3. 检查 prompts/lapwing.md 是否增加了新的规则
4. 检查 data/backups/prompts/ 下有旧版本备份
5. 后续对话中她的表现有所改善

#### 任务 17：自主探索中的知识积累

**目标**：Lapwing 在自主浏览网络的过程中，能把学到的知识融入自己的知识体系。

**实现方案**：
- 新建 `data/knowledge/` 目录，按主题存储知识笔记（Markdown）
- 自主浏览后，除了更新兴趣图谱，还将有价值的内容整理成笔记
- 笔记格式：
  ```markdown
  # 深海水母的视觉系统
  
  来源: https://example.com/article
  日期: 2026-03-24
  
  ## 关键发现
  - 深海水母使用极化光感知环境
  - 与哺乳动物视觉系统完全不同
  
  ## 我的思考
  - 这种机制是否能启发新的传感器设计？
  ```
- 在对话中，当用户聊到相关话题时，Lapwing 能引用自己的知识笔记
- 知识笔记可以通过 FileAgent 读写

**修改文件**：
- 修改自主浏览相关代码，浏览后生成知识笔记
- 修改 brain.py，在相关话题时检索知识笔记注入上下文

**验证方式**：Lapwing 自主浏览了一篇文章 → data/knowledge/ 下出现笔记 → 聊天中提到相关话题时她能引用。

---

### 🔨 Phase 6 - 功能扩展（当前阶段，Phase 5 已完成）

> 以下任务按顺序开发，任务 18 → 19 → 20 → 21。

#### 任务 18：记忆管理界面
- /memory 命令查看 Lapwing 记住了哪些关于你的信息（user_facts）
- /memory delete <编号> 删除某条记忆
- /interests 命令查看她的兴趣图谱（interest_topics）
- 格式化输出，清晰易读

#### 任务 19：RAG 长期记忆
- 引入 ChromaDB 向量数据库
- 对话历史向量化存储
- 当用户提到过去的事情时，Lapwing 能通过语义检索找到相关的历史对话
- 将检索到的历史上下文注入到 system prompt 中

#### 任务 20：更多工具 Agent
- 天气查询 Agent
- 日历/待办管理 Agent
- 文件处理 Agent（读取、总结文档）

#### 任务 21：桌面应用
- 使用 Electron 或 Tauri 构建桌面客户端
- 任务看板：查看 Agent 团队的任务进度
- 设置面板：管理模型配置、主动消息设置等
- 通知系统：桌面端推送 Lapwing 的主动消息