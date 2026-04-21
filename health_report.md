# Lapwing 项目健康报告

**生成日期**：2026-04-21  
**审计范围**：`src/`、`tests/`、`config/`、`desktop-v2/`、`docs/`、`scripts/`  
**审计方法**：8 个并行审计代理覆盖 10 个维度，静态分析 + 代码交叉验证

---

## Executive Summary

**整体健康状况：🟡 良好（需关注安全配置）**

Lapwing 代码库经过 2026-04-19 MVP 清理后处于良好状态。31,560 行 Python 生产代码 + 27,175 行测试代码，143 个源文件、146 个测试文件。所有退役模块已正确移除，导入健康，API 接口一致，异步架构无阻塞问题。主要关注点集中在**安全配置**（生产环境 sudo 和桌面默认 OWNER 权限）、**时区处理不一致**、以及**外部 API 缺少重试逻辑**。

| 维度 | 状态 | 关键发现 |
|------|------|----------|
| 项目结构 | 🟢 | 干净，无孤立文件或死代码 |
| 导入与依赖 | 🟢 | 无断裂导入，循环依赖已用 TYPE_CHECKING 规避 |
| API 与接口一致性 | 🟢 | 49 个端点无冲突，函数签名 100% 匹配 |
| 配置与环境 | 🟡 | .env.example 仅覆盖 18/111 个变量；6 处时区不一致 |
| 异步架构 | 🟢 | 无阻塞调用，无未 await 的协程 |
| 类型与数据流 | 🟢 | SQLite schema 与代码一致，ChromaDB 使用规范 |
| 错误处理 | 🟡 | 无裸 except，但外部 API 缺重试逻辑 |
| 近期改动 | 🟡 | 委派工具缺 auth_level 校验，MiniMax 无温度下限保护 |
| 安全性 | 🟡 | 生产配置需加固（sudo、桌面默认 OWNER、BrowserGuard） |
| 文档与注释 | 🟡 | README.md 有过时引用，docstring 覆盖率 95% |

---

## 1. 项目结构总览

### 1.1 顶层目录结构

| 目录 | 职责 | Python 行数 |
|------|------|-------------|
| `src/core/` | 核心运行时（Brain、TaskRuntime、LLMRouter、安全守卫等） | 14,974 |
| `src/tools/` | 工具实现（tell_user、memory、browser、skills、agent） | 5,406 |
| `src/api/` | FastAPI 路由（桌面 WebSocket、identity、notes 等） | 2,174 |
| `src/memory/` | RAPTOR 双层记忆 + 向量存储 + 对话缓存 | 2,146 |
| `src/auth/` | OAuth（OpenAI/Codex）、凭证管理 | 1,631 |
| `src/app/` | AppContainer（DI 根）、启动引导 | 1,105 |
| `src/adapters/` | 消息通道适配器（QQ/NapCat、Desktop） | 878 |
| `src/research/` | 研究引擎（Tavily/Bocha 后端） | 791 |
| `src/config/` | 环境变量配置 | 617 |
| `src/agents/` | Phase-6 代理框架（coder、researcher、team_lead） | 583 |
| `src/skills/` | Skill Growth Model（store、executor、Docker 沙箱） | 544 |
| `src/logging/` | StateMutationLog（追加式审计日志） | 421 |
| `src/guards/` | memory_guard.py | 133 |
| `src/models/` | RichMessage 共享数据模型 | 88 |
| `src/utils/` | 小型辅助函数 | 69 |
| **src/ 合计** | | **31,560** |
| `tests/` | 测试（1,364 个测试函数） | 27,175 |
| `desktop-v2/src/` | Tauri v2 + React 19 前端 | 4,831 (TS/TSX) |

**测试代码比**：0.86:1（良好）

### 1.2 孤立文件与死代码检查

**状态：🟢 通过**

所有在 CLAUDE.md 中标记为退役的模块均已确认不存在：

- ✓ `src/core/evolution.py`、`delegation.py`、`session.py`、`heartbeat.py`、`prompt_builder.py`
- ✓ `src/heartbeat/`、`src/guards/skill_guard.py`、`data/evolution/`、`desktop/`（v1）
- ✓ 旧 SQLite 表 `user_facts`、`interest_topics`、`discoveries`、`todos`、`reminders`
- ✓ 退役类 `TacticalRules`、`QualityChecker`、`EvolutionEngine` 在代码中无引用
- ✓ 无 `.pyc`、`__pycache__`（src/tests 内）、`.DS_Store` 等构建产物

**遗留参数**（可接受的技术债）：`browser_guard` 参数在 5 个文件中存在但设为 `None`，有明确注释标记为 Phase 1 减法。

---

## 2. 导入与依赖健康度

### 2.1 断裂导入

**状态：🟢 通过**

143 个源文件、146 个测试文件全部导入有效，无引用不存在模块的 import。

### 2.2 循环导入

**状态：🟢 通过**

所有潜在循环导入已用 `TYPE_CHECKING` 守卫正确规避：
- `src/core/maintenance_timer.py:27` — `LapwingBrain` 仅在 TYPE_CHECKING 下导入
- `src/core/main_loop.py:36-39` — 同上

### 2.3 未使用导入

**状态：🟢 通过**

关键文件抽样检查（brain.py、task_runtime.py、llm_router.py、container.py、registry.py）均无未使用导入。

### 2.4 requirements.txt 对齐

**状态：🟢 通过**

所有声明的依赖均在代码中使用：
- `psutil` → `src/core/vitals.py:106`（延迟导入）
- `playwright` → `src/core/browser_manager.py:461`（延迟导入）
- `anthropic` → `src/core/llm_router.py:543`（延迟导入，有降级处理）
- `openai` → `src/core/llm_router.py:557`（延迟导入，有降级处理）
- `chromadb` → `src/memory/vector_store.py`（延迟导入，有运行时错误处理）

---

## 3. API 与接口一致性

### 3.1 FastAPI 路由

**状态：🟢 通过**

14 个路由模块，49 个端点，全部正确注册于 `src/api/server.py:48-140`：

| 路由文件 | 前缀 | 端点数 |
|----------|------|--------|
| chat_ws.py | `/ws/chat` | 1 (WebSocket) |
| auth.py | `/api/auth` | 5 |
| browser.py | `/api/browser` | 5 |
| agents.py | `/api/agents` | 3 |
| events_v2.py | `/api/v2/events` | 1 |
| identity.py | `/api/v2/identity` | 5 |
| life_v2.py | `/api/v2/life` | 6 |
| models_v2.py | `/api/v2/models` | 3 |
| notes_v2.py | `/api/v2/notes` | 4 |
| permissions_v2.py | `/api/v2/permissions` | 4 |
| skills_v2.py | `/api/v2/skills` | 2 |
| status_v2.py | `/api/v2/status` | 1 |
| system_v2.py | `/api/v2/system` | 2 |
| tasks_v2.py | `/api/v2/tasks` | 3 |

无重复或冲突路径。所有 `init()` 函数的依赖注入参数与 server.py 中的调用匹配。

### 3.2 函数签名一致性

**状态：🟢 通过**

- `Brain.think_conversational()`（9 参数）：所有调用点匹配（main_loop.py:219、durable_scheduler.py:472）
- `Brain.think_inner()`（2 参数）：调用点匹配（main_loop.py:274）
- `TaskRuntime.complete_chat()`（11 个仅关键字参数）：调用点匹配（brain.py:283）
- `LLMRouter.complete()`、`complete_with_tools()`、`complete_structured()`：所有调用点匹配
- `AuthorityGate.identify()` / `authorize()`：调用点匹配

### 3.3 工具注册与执行器

**状态：🟢 通过**

`registry.py` 中注册的 12 个基础工具 + `container.py` 中注册的扩展工具，全部有对应的执行器函数且签名符合 `ToolExecutionRequest/ToolExecutionContext` 模式。

---

## 4. 配置与环境

### 4.1 环境变量覆盖

**状态：🟡 需改进**

`src/config/settings.py` 定义了 **111 个环境变量**，但 `config/.env.example` 仅文档化了 **18 个关键密钥**。

**缺失的分类**：
- 所有 `LLM_CHAT_*`、`LLM_TOOL_*`、`NIM_*` 模型路由变量
- 所有 `SHELL_*` 配置变量
- 所有 `QQ_*` 非凭证配置变量
- 所有 `BROWSER_*` 变量（70+）
- 所有 `CONSCIOUSNESS_*`、`TASK_*`、`SLO_*`、`LOOP_DETECTION_*` 调优变量
- 所有 `SANDBOX_*` 沙箱变量

### 4.2 硬编码值

**状态：🟢 可接受**

发现的硬编码 URL 均为可配置的默认值：
- `src/config/settings.py:270` — QQ 默认 `ws://127.0.0.1:3001`
- `src/config/settings.py:303` — NVIDIA NIM 默认 `https://integrate.api.nvidia.com/v1`
- `src/config/settings.py:411` — MiniMax 默认 `https://api.minimaxi.com`
- `src/research/backends/tavily.py:14` — Tavily API `https://api.tavily.com/search`
- `src/research/backends/bocha.py:20` — Bocha API `https://api.bochaai.com/v1/web-search`

无明文密钥或敏感信息硬编码在源代码中。`.env` 文件已正确列入 `.gitignore`（第 17 行），且从未被提交到 git 历史。

### 4.3 时区处理

**状态：🔴 不一致**

6 处使用 `datetime.now()` 缺少时区感知（应统一为 `Asia/Shanghai`）：

| 文件 | 位置 | 用途 |
|------|------|------|
| `src/core/maintenance_timer.py` | :76 | 3 AM 每日触发判断 |
| `src/core/browser_manager.py` | :195+ | 截图时间戳 |
| `src/core/task_runtime.py` | :1540 | 工具结果文件名 |
| `src/core/inner_tick_scheduler.py` | :72 | 提示注入当前时间 |
| `src/core/vital_guard.py` | | 备份时间戳 |
| `src/tools/file_editor.py` | | 备份时间戳 |

**正确使用**（参考实现）：
- `src/core/time_utils.py:35` — `datetime.now(timezone.utc).isoformat()` ✓
- `src/tools/shell_executor.py:188` — `datetime.now(timezone.utc).isoformat()` ✓

---

## 5. 异步架构审查

### 5.1 同步阻塞调用

**状态：🟢 通过**

- 无 `requests.get/post` 在 async 上下文中使用（全部使用 httpx）
- 无 `time.sleep` 在 async 上下文中使用（全部使用 `asyncio.sleep`）
- 文件 I/O 正确包装：`asyncio.to_thread()` 用于 `shell_executor.py:192` 和 `state_mutation_log.py:337`

### 5.2 未 await 的协程

**状态：🟢 通过**

所有关键异步函数调用均正确 await：
- `main_loop.py:219` — `await self._brain.think_conversational(...)` ✓
- `main_loop.py:274` — `await self._brain.think_inner(...)` ✓
- `durable_scheduler.py:472` — `await self._brain.think_conversational(...)` ✓

### 5.3 资源释放

**状态：🟢 通过**

数据库连接、HTTP 客户端、WebSocket 连接、浏览器上下文均使用 `async with` 正确管理。`AppContainer.shutdown()` 实现逆序资源释放。

---

## 6. 类型与数据流

### 6.1 Pydantic 模型

**状态：🟢 通过**

API 模型（`ProviderCreate`、`ProviderUpdate`、`SlotAssign`）字段名与端点处理函数中的实际使用一致。

### 6.2 SQLite Schema 与代码匹配

**状态：🟢 通过**

四张核心表全部验证通过：

| 表 | 文件 | INSERT 列数 | 匹配 |
|-----|------|------------|------|
| trajectory | trajectory_store.py | 8 列 | ✓ |
| commitments | commitments.py | 11 列 | ✓（含 Step 5 新增列的迁移） |
| mutations | state_mutation_log.py | 6 列 | ✓ |
| reminders_v2 | durable_scheduler.py | 9 列 | ✓ |

### 6.3 ChromaDB 使用

**状态：🟢 通过**

- 集合名称一致：每聊天动态集合 + 固定 `"lapwing_memory"` 集合
- 元数据 schema 一致：`note_type`、`trust`、`created_at` 等字段在 upsert/query 间匹配
- 过滤条件 `where={"note_type": _NOTE_TYPE}` 在 episodic/semantic 查询中正确使用

---

## 7. 错误处理

### 7.1 裸 except

**状态：🟢 通过**

全代码库无 `except:` 裸捕获。

### 7.2 过于宽泛的 except Exception

**状态：🟡 大部分可接受，少数需改进**

**可接受**（有日志记录或优雅降级）：container.py、refiner.py、working_set.py、fetcher.py、episodic_store.py、semantic_store.py 等约 15 处。

**需改进（静默吞异常）**：

| 文件 | 行号 | 问题 |
|------|------|------|
| `src/app/container.py` | :81-82 | git 版本检测 `except Exception: pass` 无日志 |
| `src/app/container.py` | :378-379 | VLM 客户端关闭静默捕获 |
| `src/memory/note_store.py` | :123, :177, :224, :245 | 笔记解析/搜索循环中静默吞异常（4 处） |
| `src/research/fetcher.py` | :63 | URL 解析静默异常 |

### 7.3 外部服务超时

**状态：🟢 大部分配置正确**

- httpx 请求：fetcher（10s）、Tavily（10s）、Bocha（10s）、MiniMax VLM（60s）、Codex OAuth（30s） ✓
- 浏览器操作：fetch（8s）、close（3s） ✓

### 7.4 外部 API 重试逻辑

**状态：🔴 缺失**

| 服务 | 文件 | 重试 | 备注 |
|------|------|------|------|
| LLM（MiniMax/OpenAI） | llm_router.py | ✓ | 有 `_RECOVERABLE_FAILURES` 重试机制 |
| Tavily Search | backends/tavily.py:26-58 | ✗ | 单次尝试，失败返回空列表 |
| Bocha Search | backends/bocha.py:31-75 | ✗ | 单次尝试，失败返回空列表 |
| MiniMax VLM | minimax_vlm.py:23-58 | ✗ | 单次尝试，失败直接抛出 |
| 情景提取 | episodic_extractor.py | ✗ | LLM 调用无显式重试 |
| 语义蒸馏 | semantic_distiller.py | ✗ | LLM 调用无显式重试 |

---

## 8. 近期改动专项检查

### 8.1 MiniMax M2.7 集成

**Anthropic 兼容检测**：`llm_protocols.py:14` 通过 `base_url` 中的 `/anthropic` 正确识别 ✓  
**tool_choice 处理**：`llm_router.py:1155-1158` 正确设置 `{"type": "auto", "disable_parallel_tool_use": True}` ✓  
**529 错误分类**：`llm_router.py:1543` 正确归类为 `rate_limit` ✓  
**thinking block 处理**：`llm_protocols.py:85-90` + `llm_router.py:1186-1206` 正确检测并处理 ✓

**问题**：

| 问题 | 严重度 | 详情 |
|------|--------|------|
| 无温度下限保护 | Warning | 代码未阻止向 MiniMax 发送 `temperature=0`（MiniMax 要求 `(0.0, 1.0]`）。`llm_router.py` 中仅在 mutation log 记录温度但无校验。 |
| thinking 兼容性未验证 | Info | MiniMax 可能不支持 extended thinking，但代码未检测此限制。若配置了 thinking 模式可能导致静默失败。 |

### 8.2 多模态管道

**完整链路验证**：

```
QQ Adapter._extract_image_urls() (qq_adapter.py:400-410)
  → _download_image_as_base64() (qq_adapter.py:412-439)
    → Brain._inject_images_into_last_user_message() (brain.py:434-478)
      → Anthropic content block 格式传递给 LLM ✓
```

参数传递一致，base64 和 URL 两种来源均支持。

**限制**：图片仅在 `think_conversational()` 中传递（brain.py:728-759），`think()` 方法不支持（功能限制而非 bug）。

### 8.3 委派权限传播

**状态：🟡 需关注**

| 检查项 | 状态 | 位置 |
|--------|------|------|
| delegate 执行器有 auth 校验 | ✗ | agent_tools.py:28-75 未检查调用者 auth_level |
| delegate_to_agent 执行器有 auth 校验 | ✗ | agent_tools.py:77-130 未检查调用者 auth_level |
| 代理执行尊重权限级别 | ✗ | agents/base.py:72-112 无 auth_level 验证 |

**风险评估**：由于 `OPERATION_AUTH` 中 `delegate_task: OWNER`（注意：此处工具名不匹配，见 8.5 节）以及 `DEFAULT_AUTH = OWNER`，**实际上委派工具在 `authorize()` 层面仍然要求 OWNER 权限**。因此当前无实际权限绕过，但缺少纵深防御。

### 8.4 Desktop v2 感知管道

**状态：⚠️ 未实现**

- `desktop_adapter.py` 为简单 WebSocket 连接管理器，无事件感知功能
- `vitals.py` 提供系统快照（CPU/内存/磁盘）但不含桌面窗口事件
- 无 Tauri window focus/blur 事件的后端接收代码
- 架构已准备好通过 `mutation_log` 接收事件，但事件生成层缺失

### 8.5 OPERATION_AUTH 工具名不匹配

**状态：🟡 功能安全但有死代码**

`authority_gate.py` 中部分工具名与实际注册名不一致：

| OPERATION_AUTH 中的名称 | 实际工具名 | 状态 |
|------------------------|-----------|------|
| `delegate_task` (line 78) | `delegate` | 不匹配（由 DEFAULT_AUTH=OWNER 兜底） |
| `memory_note` (line 69) | `write_note` | 不匹配（由 DEFAULT_AUTH=OWNER 兜底） |
| `memory_list` (line 70) | `list_notes` | 不匹配 |
| `memory_read` (line 71) | `read_note` | 不匹配 |
| `memory_edit` (line 72) | `edit_note` | 不匹配 |
| `memory_delete` (line 73) | [需确认] | 不匹配 |
| `memory_search` (line 74) | `search_notes` | 不匹配 |

**安全影响**：由于 `DEFAULT_AUTH = AuthLevel.OWNER`，未匹配的工具名会回退到 OWNER 权限，**无安全漏洞**。但 OPERATION_AUTH 中的这些条目是死代码，永远不会被匹配到。

### 8.6 技能系统

**状态：🟢 安全**

- 路径遍历防护：`skill_store.py:30-33` 检查 `/`、`\`、`..` ✓
- 代码安全扫描：`skill_security.py:5-37` 覆盖 `os.system`、`subprocess`、`eval`、`exec`、`__import__`、`pickle.loads`、`ctypes`、`socket` 等
- Docker 沙箱：三级（STRICT/STANDARD/PRIVILEGED），`--cap-drop=ALL`、`--user sandboxuser`、内存限制
- SSRF 防护：安装时使用 `_check_browse_safety()` 校验 URL ✓
- 下载大小限制：`_MAX_SKILL_DOWNLOAD_BYTES = 512KB` ✓
- 输出截断：`_MAX_OUTPUT = 4000` ✓
- 权限门控：所有技能工具要求 OWNER（`search_skill` 除外为 GUEST）✓

---

## 9. 安全性初筛

### 9.1 敏感信息暴露

**状态：🟢 源代码安全**

- `.env` 文件已正确 `.gitignore`（第 17 行），从未提交到 git 历史
- 源代码中无明文 API key、token 或密码
- 日志输出经 `credential_sanitizer.py` 脱敏处理 ✓
- `MemoryGuard` 扫描记忆写入中的凭证信息 ✓

### 9.2 权限模型

**状态：🟡 功能安全但需加固生产配置**

| 检查项 | 状态 | 详情 |
|--------|------|------|
| OPERATION_AUTH 覆盖率 | ⚠️ | 有工具名不匹配的死代码条目（见 8.5 节），但 DEFAULT_AUTH=OWNER 兜底安全 |
| DESKTOP_DEFAULT_OWNER | ⚠️ | 当前启用，localhost 连接直接获得 OWNER 权限。单用户本地桌面场景下可接受，但需意识到风险 |
| SHELL_ALLOW_SUDO | ⚠️ | 当前启用。结合 DESKTOP_DEFAULT_OWNER，桌面连接可执行 sudo 命令 |
| BrowserGuard | ⚠️ | Phase 1 中设为 None，浏览器导航无 URL 验证。`personal_tools.py` 的 `browse` 工具有 SSRF 防护，但 `browser_manager.py.navigate()` 无校验 |

### 9.3 外部输入校验

**状态：🟡 基本覆盖**

- QQ 消息长度限制：`MAX_QQ_MSG_LENGTH = 4000` ✓
- 文本提取正确处理 list/string 格式 ✓
- 文件编辑器路径遍历防护：`os.path.commonpath()` ✓
- SSRF 防护（browse/install_skill）：阻止 IPv4 私有地址、IPv6 本地链路、localhost ✓

**问题**：
- `_download_image_as_base64()`（qq_adapter.py:412-439）下载图片无大小限制，仅有 15s 超时。超大图片可能导致内存耗尽。

### 9.4 Shell 执行安全

**状态：🟢 良好**

- 危险模式拦截：fork bomb、`rm -rf /`、`mkfs`、`shutdown`、`dd` ✓
- 交互命令拦截：vim、nano、top、htop、less、`tail -f` ✓
- 系统路径保护：`/etc`、`/usr`、`/boot`、`/bin`、`/sbin`、`/lib` ✓
- VitalGuard 保护：constitution.md、.env、settings.py、src/ 目录 ✓

### 9.5 凭证保险库

**状态：🟢 良好**

`credential_vault.py` 使用 Fernet 对称加密，密钥通过环境变量加载（标准实践）。

---

## 10. 文档与注释

### 10.1 Docstring 覆盖率

**状态：🟢 95% 覆盖**

所有核心模块均有模块级 docstring，主要类和公开方法文档完善。

**缺少 class docstring 的类**（3 处）：
- `src/core/event_queue.py:25` — `EventQueue`
- `src/adapters/qq_adapter.py:45` — `QQAdapter`
- `src/tools/registry.py:60` — `ToolRegistry`

### 10.2 过时注释

**状态：🟡 README.md 需更新**

**README.md 中的过时引用**：
- Line 31：引用 `PromptBuilder`（已被 `StateViewBuilder` 替代）
- Line 61：引用 `SessionManager`（已退役）
- Line 63：引用 `TacticalRules`（已退役）
- Line 65：引用 `PromptBuilder`（已退役）
- Lines 471, 474：引用 `TacticalRules`、`SessionManager`
- Line 593：引用 `sessions/` 目录

**源代码注释**：干净，无引用已删除模块的过时注释。`src/memory/conversation.py:3-12` 中保留了退役表的历史说明（属于有意的迁移文档）。

### 10.3 TODO/FIXME 标记

**状态：🟢 无遗留标记**

全源码树中无正式的 TODO、FIXME、HACK、XXX 标记。`main_loop.py:9-14` 中的 TODO 引用是设计文档中的里程碑标记（已全部实现）。

### 10.4 测试文档

**状态：🟢 良好**

测试文件名遵循 `test_*.py` 约定，测试类使用 `Test*` 命名，测试方法使用 `test_*` 命名，函数名具有良好的描述性（如 `test_pass_safe_command`、`test_block_rm_rf_root`）。

---

## 问题清单

### Critical（需立即修复）

| # | 问题 | 文件 | 行号 |
|---|------|------|------|
| C-1 | datetime.now() 无时区感知，maintenance_timer 的 3AM 判断在非 Asia/Shanghai 时区服务器上会出错 | `src/core/maintenance_timer.py` | :76 |

### Warning（应尽快修复）

| # | 问题 | 文件 | 行号 |
|---|------|------|------|
| W-1 | Tavily/Bocha/MiniMax VLM 外部 API 调用无重试逻辑 | `src/research/backends/tavily.py` | :26-58 |
| W-2 | OPERATION_AUTH 工具名与实际注册名不匹配（6 条死代码） | `src/core/authority_gate.py` | :69-78 |
| W-3 | MiniMax 无 temperature 下限保护（不能发送 temp=0） | `src/core/llm_router.py` | [需确认具体行] |
| W-4 | note_store.py 中 4 处静默吞异常无日志记录 | `src/memory/note_store.py` | :123, :177, :224, :245 |
| W-5 | QQ 图片下载无大小限制，可能导致内存耗尽 | `src/adapters/qq_adapter.py` | :412-439 |
| W-6 | BrowserGuard 被禁用，browser_manager.navigate() 无 URL 校验 | `src/core/browser_manager.py` | :523 |
| W-7 | README.md 有 6 处引用已退役模块（PromptBuilder、TacticalRules、SessionManager） | `README.md` | :31, :61, :63, :65, :471, :474 |
| W-8 | .env.example 仅覆盖 18/111 个环境变量 | `config/.env.example` | — |
| W-9 | 生产配置 SHELL_ALLOW_SUDO=true，结合 DESKTOP_DEFAULT_OWNER 存在提权路径 | `config/` 相关配置 | — |

### Info（建议改进）

| # | 问题 | 文件 | 行号 |
|---|------|------|------|
| I-1 | 5 处 datetime.now() 缺少时区（非关键路径：截图、备份、文件名） | 见第 4.3 节 | — |
| I-2 | 3 个核心类缺少 class docstring | 见第 10.1 节 | — |
| I-3 | container.py git 版本检测 `except Exception: pass` 无日志 | `src/app/container.py` | :81-82 |
| I-4 | MiniMax extended thinking 兼容性未验证 | `src/core/llm_router.py` | — |
| I-5 | Desktop v2 感知管道（Tauri 窗口事件）未实现 | `src/adapters/desktop_adapter.py` | — |
| I-6 | 委派工具执行器内缺少 auth_level 纵深校验（当前由 authorize() 层兜底） | `src/tools/agent_tools.py` | :28-130 |
| I-7 | browser_guard 遗留参数在 5 个文件中存在 | 见第 1.2 节 | — |
| I-8 | VitalGuard 未覆盖符号链接创建和归档解压路径遍历 | `src/core/vital_guard.py` | — |

---

## 建议修复优先级

### 第一优先级（本周）

1. **C-1 + I-1**：统一时区处理 — 在 6 处 `datetime.now()` 调用中添加 `tz=ZoneInfo("Asia/Shanghai")` 或统一使用 `time_utils` 模块
2. **W-2**：修正 OPERATION_AUTH 工具名，使其与实际注册名一致（`delegate_task` → `delegate`，`memory_*` → `write_note`/`list_notes`/...）
3. **W-3**：在 LLMRouter 中添加 MiniMax 温度下限保护（`max(temperature, 0.01)`）
4. **W-5**：为 `_download_image_as_base64()` 添加 50MB 大小限制

### 第二优先级（本 Sprint）

5. **W-1**：为 Tavily/Bocha/MiniMax VLM 添加指数退避重试（max 3 次，初始延迟 1s）
6. **W-4**：为 note_store.py 的 4 处静默 except 添加日志记录
7. **W-7**：更新 README.md，移除 PromptBuilder/TacticalRules/SessionManager 引用
8. **W-8**：从 `_ENV_MAP` 生成完整的 `.env.example`，按模块分组并标注默认值
9. **W-9**：评估生产环境是否需要 SHELL_ALLOW_SUDO=true，如不需要则设为 false

### 第三优先级（下个 Sprint）

10. **W-6**：重新实现 BrowserGuard URL 验证（至少在 navigate() 层面阻止内网地址）
11. **I-2**：为 EventQueue、QQAdapter、ToolRegistry 添加 class docstring
12. **I-6**：在 delegate/delegate_to_agent 执行器内添加 auth_level 纵深校验
13. **I-8**：VitalGuard 增加符号链接和归档解压检测

---

*报告由 8 个并行审计代理生成，覆盖项目结构、导入、API、配置、异步、错误处理、安全、文档共 10 个维度。标注 [需确认] 的条目需人工二次验证。*
