# Lapwing 🐦

> 个人 AI 伴侣 + 可执行助手

Lapwing（凤头麦鸡）是一个双面一体的 AI 系统：
- **伴侣面**：温柔知性的长期聊天伙伴
- **执行面**：可调度 Agent、可调用工具、可进行主动心跳行为的助手

## 当前阶段

**Phase 7 - 权限与存活系统（进行中）**

截至 `2026-03-31`：
- 全量测试：`702 collected`
- 已具备 Agent 分发、Tool Loop、本地 Shell 执行、兴趣图谱、心跳引擎、本地 API、桌面端 MVP
- 新增 VitalGuard（存活保护）、AuthorityGate（权限认证）、QQ 适配器、Skills 系统、Watchdog 哨兵

## 已实现能力

- Telegram 文本/语音对话（Whisper 转写）
- QQ 群聊适配器（OneBot v11 协议）
- 多模型路由（`chat` / `tool` / `heartbeat`，兼容 OpenAI 与 Anthropic）
- Agent 体系：`researcher` / `coder` / `browser` / `file` / `weather` / `todo`
- Tool Loop：`execute_shell`、`read_file`、`write_file`
- 记忆系统：对话历史、用户画像、discoveries、兴趣图谱、待办、向量记忆（Chroma）
- 心跳动作：主动消息、兴趣驱动分享、记忆整理、自省、人格进化
- Skills 系统：可调度的结构化技能注册表
- 本地观测 API + Desktop 前端（React + Vite + Tauri）
- **VitalGuard**：存活保护系统，防止删除/破坏核心文件
- **AuthorityGate**：三级权限认证（Owner / Trusted / Guest）
- **Watchdog Sentinel**：独立哨兵进程，文件完整性检查与自动恢复

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
├── skills/                 # Skills 注册表（coding/content/daily/research/system）
├── watchdog/               # Sentinel 哨兵进程 + systemd unit
├── src/
│   ├── agents/             # Agent 实现
│   ├── api/                # 本地 FastAPI + SSE
│   ├── app/                # 适配器（Telegram、QQ）
│   ├── core/               # brain/dispatcher/heartbeat/router/vital_guard/authority_gate
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
- [x] Phase 5：Shell 执行 + 文件能力 + 自省与 prompt 进化
- [x] Phase 6：整合与发布完善（QQ 适配器、Skills 系统、桌面端 MVP）
- [ ] Phase 7：权限与存活系统（VitalGuard + AuthorityGate + Watchdog，进行中）

## 文档说明

- Prompt 规范：`prompts/README.md`
- 权限与存活系统蓝图：`CLAUDE.md`
