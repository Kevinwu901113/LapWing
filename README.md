# Lapwing 🐦

> 个人 AI 伴侣 + 可执行助手（Telegram 主入口）

Lapwing（凤头麦鸡）是一个双面一体的 AI 系统：
- **伴侣面**：温柔知性的长期聊天伙伴
- **执行面**：可调度 Agent、可调用工具、可进行主动心跳行为的助手

## 当前阶段

**Phase 6 - 功能整合与体验打磨（进行中）**

截至 `2026-03-25`：
- 全量测试：`266 passed`
- 已具备 Agent 分发、Tool Loop、本地 Shell 执行、兴趣图谱、心跳引擎、本地 API、桌面端 MVP

## 已实现能力

- Telegram 文本/语音对话（Whisper 转写）
- 多模型路由（`chat` / `tool` / `heartbeat`，兼容 OpenAI 与 Anthropic）
- Agent 体系：`researcher` / `coder` / `browser` / `file` / `weather` / `todo`
- Tool Loop：`execute_shell`、`read_file`、`write_file`
- 记忆系统：对话历史、用户画像、discoveries、兴趣图谱、待办、向量记忆（Chroma）
- 心跳动作：主动消息、兴趣驱动分享、记忆整理、自省、人格进化
- 本地观测 API + Desktop 前端（React + Vite + Tauri）

## 环境要求

- Python 3.11+
- Ubuntu 22.04（PVE VM）
- Telegram Bot Token（@BotFather）
- LLM API Key（OpenAI 兼容或 Anthropic 兼容）

## 快速开始

```bash
# 1) 创建并激活虚拟环境
python3.11 -m venv venv
source venv/bin/activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 配置环境变量
cp config/.env.example config/.env

# 4) 启动
python main.py
```

## 目录结构

```text
lapwing/
├── main.py
├── config/                 # 环境变量与运行配置
├── prompts/                # 所有 prompt（Markdown）
├── src/
│   ├── agents/             # Agent 实现
│   ├── api/                # 本地 FastAPI + SSE
│   ├── core/               # brain/dispatcher/heartbeat/router
│   ├── heartbeat/actions/  # 心跳动作
│   ├── memory/             # SQLite + facts + interests + vector
│   └── tools/              # shell/search/fetch/runner/transcriber
├── tests/
├── desktop/                # 桌面端（React + Tauri）
├── data/
└── logs/
```

## 开发进度

- [x] Phase 1：Telegram 对话基础
- [x] Phase 1.5：持久化记忆 + 多模型路由 + 心跳基础
- [x] Phase 2：Agent 框架 + Researcher/Coder
- [x] Phase 3：Browser + 兴趣图谱 + 主动分享
- [x] Phase 4：体验修复（消息合并/搜索增强/人格替换）
- [x] Phase 5：Shell 执行 + 文件能力 + 自省与 prompt 进化（任务 17 仍可继续增强）
- [ ] Phase 6：整合与发布完善（进行中）

## 文档说明

- Prompt 规范：`prompts/README.md`
- 接手与现状快照：`HANDOVER_CLAUDE_TO_CODEX_2026-03-25.md`
- Claude 协作说明：`CLAUDE.md`
