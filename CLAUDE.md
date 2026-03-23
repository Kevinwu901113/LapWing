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

---

### 🔨 Phase 1.5 - 基础完善（当前阶段）

完善 Phase 1 的基础设施，为后续所有功能提供支撑。

#### 任务 1：持久化记忆（SQLite）

**目标**：重启 Lapwing 后不会失忆，能记住之前的对话。

**实现方案**：
- 改造 `src/memory/conversation.py`，用 aiosqlite 替代内存字典
- 数据库文件存放在 `data/lapwing.db`
- 表结构：
  - `conversations` 表：chat_id, role, content, timestamp
  - `user_facts` 表：chat_id, fact_key, fact_value, updated_at（存储用户偏好、重要信息）
- 启动时从 SQLite 加载最近的对话历史
- 每次对话后自动存储
- 添加 /forget 命令：清除当前对话的记忆
- **不需要**做向量数据库，那是后面的事

**修改文件**：
- `src/memory/conversation.py` — 重写，改用 aiosqlite
- `config/settings.py` — 添加 DATA_DIR、DB_PATH 配置
- `main.py` — 启动时初始化数据库

**验证方式**：启动 Lapwing → 聊几句 → 重启 → 她还记得之前聊的内容。

#### 任务 2：多模型路由

**目标**：不同类型的任务自动使用不同的模型，平衡效果和成本。

**实现方案**：
- 在 `config/.env` 中支持配置多个模型：
  ```
  # 主对话模型（人格对话用，需要质量好的）
  LLM_CHAT_MODEL=glm-4-plus
  LLM_CHAT_BASE_URL=https://...
  LLM_CHAT_API_KEY=...

  # 工具模型（Agent 任务用，速度快成本低的）
  LLM_TOOL_MODEL=glm-4-flash
  LLM_TOOL_BASE_URL=https://...
  LLM_TOOL_API_KEY=...
  ```
- 新建 `src/core/llm_router.py`，提供统一的模型调用接口
- brain.py 改为通过 llm_router 调用，按用途选择模型
- 保持向后兼容：如果只配了一组 LLM_* 变量，所有任务用同一个模型

**修改文件**：
- 新建 `src/core/llm_router.py`
- `config/settings.py` — 添加多模型配置
- `config/.env.example` — 更新模板
- `src/core/brain.py` — 改用 llm_router

**验证方式**：配置两个不同模型 → 对话时用 chat 模型 → 日志中确认用了正确的模型。

#### ✅ 任务 3：用户画像提取

**目标**：Lapwing 能自动从对话中提取你的偏好和重要信息，记入长期记忆。

**实现方案**：
- 每次对话结束后（用户超过 5 分钟没回复，或对话轮次 >= 3），触发一次"记忆提取"
- 用 LLM（tool 模型）分析对话，提取关键信息，例如：
  - 用户偏好："不喜欢吃香菜"
  - 重要事件："下周三有面试"
  - 项目信息："正在做一个叫 Lapwing 的 AI 项目"
- 提取结果存入 `user_facts` 表
- 在 system prompt 中注入已知的用户信息，让 Lapwing 能自然地引用
- 新建 `prompts/memory_extract.md` 作为提取用的 prompt

**修改文件**：
- 新建 `src/memory/fact_extractor.py`
- 新建 `prompts/memory_extract.md`
- `src/core/brain.py` — system prompt 中注入用户画像
- `src/memory/conversation.py` — 添加 user_facts 相关方法

**验证方式**：聊天中提到"我不喜欢吃辣" → 过一会儿问"你记得我不吃什么吗" → 她能回答。

#### 任务 4：主动消息

**目标**：Lapwing 能主动找你聊天，而不是只被动回复。

**实现方案**：
- 新建 `src/core/proactive.py`，管理主动消息逻辑
- 使用 APScheduler 做定时任务调度（pip install apscheduler）
- 主动消息场景（先实现前两个）：
  1. **早安/晚安**：根据设定的时间段，发一条自然的问候
  2. **关心**：如果用户超过一段时间没说话（如 8 小时），发一条关心的消息
  3. **提醒**：如果 user_facts 中有待办事项或日程，到时间提醒（后续做）
- 在 `config/.env` 中添加开关和时间配置：
  ```
  PROACTIVE_ENABLED=true
  PROACTIVE_MORNING_HOUR=8
  PROACTIVE_NIGHT_HOUR=23
  ```
- 主动消息也要符合 Lapwing 的人格，通过 LLM 生成而不是硬编码
- 新建 `prompts/proactive.md` 作为主动消息的 prompt

**修改文件**：
- 新建 `src/core/proactive.py`
- 新建 `prompts/proactive.md`
- `config/settings.py` — 添加主动消息配置
- `config/.env.example` — 添加示例
- `main.py` — 启动时注册定时任务
- `requirements.txt` — 添加 apscheduler

**验证方式**：设置早安时间为当前时间 +2 分钟 → 等待 → Lapwing 主动发来问候消息。

---

### 📋 Phase 2 - Agent 团队（下一阶段，暂不开发）

> 以下任务在 Phase 1.5 全部完成后再开始。

#### 任务 5：Agent 基础框架
- 设计 Agent 基类（BaseAgent）
- Agent 注册和发现机制
- Lapwing Core 的任务分发逻辑
- 新建 `prompts/agent_dispatcher.md`（任务分发用的 prompt）

#### 任务 6：Researcher Agent（信息搜集）
- 联网搜索能力（通过搜索 API 或 Playwright）
- 信息整理和摘要
- 结果格式化后通过 Lapwing 回复用户

#### 任务 7：Coder Agent（写代码）
- 代码生成和调试
- 在 VM 上安全执行代码（沙箱）
- 代码结果返回

---

### 📋 Phase 3 - 自主意识（远期，暂不开发）

#### 任务 8：自主浏览
#### 任务 9：兴趣图谱
#### 任务 10：主动分享发现