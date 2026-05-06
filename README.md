# Lapwing

24/7 自主 AI 伴侣 — 具有人格、记忆和自我进化能力的虚拟女友。

## 架构概览

```
main.py  →  AppContainer (DI 根)
              ├─ LapwingBrain        所有消息的唯一入口
              │   ├─ LLMRouter       多 slot 模型路由 (chat / tool / heartbeat / fallback)
              │   ├─ TaskRuntime     工具调用执行循环
              │   ├─ TrajectoryStore 对话真相源 (SQLite)
              │   └─ 可选: skill / browser / focus / identity / ...
              ├─ MainLoop + EventQueue  优先级消费 (OWNER > TRUSTED > SYSTEM > INNER)
              ├─ InnerTickScheduler     自主 inner tick (自适应退避)
              ├─ DurableScheduler       持久化提醒 + 承诺
              ├─ MaintenanceTimer       每日 3AM 语义蒸馏
              ├─ ChannelManager         QQ + Desktop 适配器
              └─ LocalApiServer         FastAPI (SSE / WebSocket / REST)
```

**核心原则:**
- 裸 assistant 文本 = 用户可见消息。伴随 tool_call 的文本 = 内部 scratch，不发送。
- 所有能力注册为 `ToolSpec`，LLM 自行决定调用，无 agent dispatch 层。
- 人格 (soul.md) 与行为约束 (voice.md) 分离于代码。
- TrajectoryStore 是对话唯一真相源，无独立缓存。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+, ~70k 行, ~190+ .py 文件 |
| 前端 | Tauri v2 + React 19 + TypeScript, ~5k 行 |
| LLM | MiniMax M2.7, GLM, NVIDIA NIM, OpenAI Codex |
| 存储 | SQLite (WAL), ChromaDB (向量) |
| 消息通道 | QQ (OneBot v11 WebSocket), Desktop (SSE/WebSocket) |
| 部署 | systemd, PVE 服务器 |

## 快速开始

### 前置要求

- Python 3.12+
- 推荐使用 venv

### 安装

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 配置

```bash
cp config.example.toml config.toml   # 编辑主配置
cp config/.env.example config/.env   # 填入 API keys
```

QQ 通道需要配置 NapCat (OneBot v11) WebSocket 连接 `ws://127.0.0.1:3001`。

### 运行

```bash
python main.py              # 启动 bot
bash scripts/deploy.sh      # systemd 部署
```

### 桌面前端

```bash
cd desktop-v2
npm install
npm run tauri dev            # 开发模式
npm run tauri build          # 生产构建
```

### 命令行工具

```bash
python main.py auth login openai-codex              # Codex OAuth 登录
python main.py auth set-api-key --provider ...      # 保存 API key
python main.py auth bind --purpose chat --profile . # 绑定模型
python main.py credential generate-key              # 生成凭据加密密钥
python main.py credential set <service> --username . --login-url .
```

## 测试

```bash
# 快速安全套件 (~1s, 无外部依赖)
PYTHONPATH=. python -m pytest \
  tests/core/test_runtime_profiles_exclusion.py \
  tests/core/test_brain_zero_tools_path.py \
  tests/core/test_tool_dispatcher.py \
  tests/core/test_tool_boundary.py \
  tests/core/test_intent_router.py \
  tests/agents/ -v

# 排除集成/e2e/LLM/浏览器测试
PYTHONPATH=. python -m pytest tests/ -x -q \
  -m "not integration and not e2e and not requires_llm and not requires_browser and not requires_network"

# 完整测试 (需要 LLM key, ChromaDB, Playwright)
PYTHONPATH=. python -m pytest tests/ -x -q
```

## 源码结构

```
src/
├── core/         核心: brain, task_runtime, llm_router, state_view, main_loop, 安全, 调度
├── tools/        工具系统: shell, browser, file editor, memory, agents, skills
├── memory/       4 层记忆: vector → episodic/semantic → notes → trajectory
├── identity/     身份基底: 事件溯源 claims, LLM 解析, ChromaDB 索引
├── capabilities/ 能力进化系统: Phase 0-8C + Maintenance A/B/C
├── api/          FastAPI: SSE/WebSocket/REST, 21 个路由模块
├── agents/       子智能体: Researcher, Coder, 动态 agent 工厂
├── adapters/     QQ (OneBot v11), Desktop (SSE)
├── auth/         多策略认证: API key, OAuth PKCE, desktop token
├── research/     Web 搜索: Tavily/DuckDuckGo
├── skills/       技能存储/捕获/沙箱执行
├── ambient/      时间/农历/节假日/环境感知
├── feedback/     纠错系统
├── guards/       VitalGuard (核心文件保护)
├── utils/        循环检测, 断路器, 重试
├── app/          DI 容器, TaskViewStore
└── config/       pydantic-settings: .env + config.toml
```

## 数据目录

```
data/
├── identity/        soul.md (人格), constitution.md (不可变规则)
├── lapwing.db       主库 (trajectory, reminders, commitments, focuses)
├── mutation_log.db  LLM/工具事件日志
├── chroma/          ChromaDB 向量索引
├── notes/           NoteStore Markdown 笔记
├── credentials/     加密凭据 (Fernet)
├── browser/         Playwright 持久化上下文 + 截图
└── config/          运行时模型路由
```

## 配置系统

加载优先级: `.env` > `config.toml` > 代码默认值。

Feature flags (在 `config.toml` 中配置):
`SKILLS_ENABLED`, `BROWSER_ENABLED`, `QQ_ENABLED`, `FOCUS_ENABLED`,
`CORRECTION_ENABLED`, `SEMANTIC_DISTILL_ENABLED`, `LOOP_DETECTION_ENABLED`,
`INTENT_ROUTER_ENABLED`, `CHAT_WEB_TOOLS_ENABLED`

## 开发约定

- 注释用中文; commits/PR/维护者文档用英文。Conventional Commits。
- 绝对导入: `from src.core.brain import LapwingBrain`
- 日志: `logging.getLogger("lapwing.<module>")`，不向 root 传播
- Prompt: `prompts/` 目录 Markdown 热加载，改 prompt 不需改代码
- 部署: 始终用 `bash scripts/deploy.sh`，不要直接 `nohup python main.py &`
- 测试: `PYTHONPATH=. python -m pytest tests/ -x -q`，`asyncio_mode = auto`
