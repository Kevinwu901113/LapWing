# Lapwing 🐦

> 个人 AI 伴侣 & 智能助手系统

Lapwing（凤头麦鸡）是一个双面一体的 AI 系统：
- **伴侣面**：温柔知性的伙伴，有自己的个性和好奇心
- **秘书面**：能干的团队领导者，理解需求并调度 Agent 团队执行

## 当前阶段：Phase 1 - 基础搭建

让 Lapwing 能在 Telegram 上以她独特的性格和你聊天。

## 环境要求

- Python 3.11+
- PVE VM（6核8G，Ubuntu 22.04）
- Telegram Bot Token（从 @BotFather 获取）
- LLM API Key（支持 OpenAI 兼容格式的 provider，如 GLM、MiniMax）

## 快速开始

```bash
# 1. 创建虚拟环境
python3.11 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp config/.env.example config/.env
# 编辑 config/.env 填入你的密钥

# 4. 启动
python main.py
```

## 项目结构

```
lapwing/
├── main.py                 # 入口文件
├── requirements.txt
├── config/
│   ├── .env.example        # 环境变量模板
│   └── settings.py         # 项目配置
├── prompts/                # 所有 Prompt 用 Markdown 管理
│   ├── lapwing.md          # Lapwing 主人格
│   └── README.md           # Prompt 管理说明
├── src/
│   ├── core/
│   │   ├── brain.py        # LLM 调用 & 对话管理
│   │   └── prompt_loader.py # Markdown prompt 加载器
│   ├── memory/
│   │   └── conversation.py # 对话记忆（Phase 1: 内存）
│   ├── agents/             # Phase 2: Agent 团队
│   └── tools/              # Phase 2: MCP 工具
└── logs/
```

## Prompt 管理

所有 Prompt 存放在 `prompts/` 目录下，使用 Markdown 格式。详见 [prompts/README.md](prompts/README.md)。

## 开发路线

- [x] Phase 1: Telegram Bot + 人格对话 + 基础记忆
- [ ] Phase 2: Agent 团队（Researcher + Coder）+ MCP 工具
- [ ] Phase 3: 自主浏览 + 兴趣图谱 + 主动分享
- [ ] Phase 4: 桌面应用

## CLAUDE.md

使用 Claude Code 开发时，请参考项目根目录的 `CLAUDE.md`。
