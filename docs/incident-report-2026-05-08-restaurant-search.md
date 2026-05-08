# task_b48b60871a03 取证报告

**日期**: 2026-05-08 23:34 (UTC 15:34)
**用户**: awwaw (chat_id: 919231551)
**父 turn**: c0b0d317bcf44afe839f1d2a87cdbea1
**分类**: 硬失败 — ToolDispatcher 在后台 AgentRuntime 的 services 中缺失

---

## A. 任务运行时事实

### A.1 `spec_id`

**`researcher`** — builtin agent，走 `AgentFactory._create_builtin()` 路径。

来源：`agent_tasks.spec_id` = `'researcher'`；mutation #13317 的 `tool.result` payload 中 `spec_id: "researcher"`。

### A.2 AgentRuntime 实际拿到的 services keys

无法从持久化数据直接还原（AgentTaskRecord 不存 services dict）。但构造链如下：

```
Brain._build_services()
  → dispatcher, tool_registry, llm_router, research_engine, ambient_store, ...
  → _start_background_delegate(services=svc.raw)
  → supervisor.start_agent_task(services=svc.raw)
  → _spawn_runtime(record, services)           ← child_services = dict(services)
    → child_services.update({                    ← 注入 4 个额外 key:
        "agent_event_bus",                        ←   self.event_bus
        "background_task_id",                     ←   record.task_id
        "background_chat_id",                     ←   record.chat_id
        "background_owner_user_id",               ←   record.owner_user_id
      })
    → AgentRuntime(services=child_services)
      → agent_registry.get_or_create_instance("researcher", services_override=child_services)
        → AgentFactory.create(spec, services_override)
          → Researcher.create(llm_router, tool_registry, mutation_log, services=services_override)
            → BaseAgent.__init__(..., services=services_override)
              → self._services = services_override or {}
```

**关键**: `_build_services()` 对 dispatcher 是条件注入（brain.py:295-297）：只有 `self._dispatcher_ref is not None` 时才会 `services["dispatcher"] = dispatcher`。其他 5 个 researcher 必需服务（tool_registry, llm_router, research_engine, ambient_store）同理。

### A.3 `tool_errors` 全文

4 次 tool call，全部返回同一错误，逐条：

| # | seq | occurred_at (UTC) | tool | error | reason |
|---|-----|-------------------|------|-------|--------|
| 1 | 1778254474283633302 | 15:34:34.283 | research | tool_forbidden | missing_dispatcher |
| 2 | 1778254478013112132 | 15:34:38.013 | research | tool_forbidden | missing_dispatcher |
| 3 | 1778254489378860659 | 15:34:49.378 | research | tool_forbidden | missing_dispatcher |
| 4 | 1778254492288885136 | 15:34:52.288 | research | tool_forbidden | missing_dispatcher |

来源：`agent_events` 表 `summary_for_lapwing` 和 `payload_json.content` 字段。

### A.4 Researcher 实际请求调用的 tool name 序列

全部 4 次调用都是 **`research`** 工具，无 `browse` 调用：

1. `research(question='华南理工大学大学城校区 贝岗村 GOGO新天地 2025年5月营业中 不同价位 校外餐厅推荐', scope='cn')`
2. `research(question='华南理工大学大学城校区 贝岗村 GOGO新天地 校外餐厅推荐 不同价位', scope='cn')`
3. `research(question='广州大学城贝岗村 平价餐厅 人均20以下 营业中', scope='cn')`
4. `research(question='华南理工大学大学城校区附近贝岗村、GOGO新天地营业的校外餐厅，分便宜、适中、稍好三个价位推荐', scope='cn')`

### A.5 每次 tool dispatch 的 ToolExecutionResult

全部 4 次都命中 `BaseAgent._execute_tool()` (base.py:452-478) 的 dispatcher 缺失 guard：

```python
dispatcher = services.get("dispatcher")        # 返回 None
if dispatcher is None or not hasattr(dispatcher, "dispatch"):
    # 记录 TOOL_DENIED mutation
    return json.dumps({
        "error": "tool_forbidden",
        "tool": tool_call.name,
        "reason": "missing_dispatcher",
    })
```

**没有经过 `ToolDispatcher.dispatch()`**，所以没有 `ToolExecutionResult.reason` / `error_code` 字段——工具调用在到达 dispatcher 之前就被拦截了。`_collected_tool_errors` 中记录的是 `payload.error`（即 `"tool_forbidden"`）。

### A.6 final result 完整 payload

来源：`agent_events` seq=1778254494406957623 (type=agent_completed)，`agent_tasks.result_summary`：

```
无法调用搜索工具获取相关信息，无法完成本次餐厅推荐任务。
```

`payload_json`:
```json
{
  "task_id": "task_b48b60871a03",
  "agent_name": "researcher",
  "actor": "researcher",
  "summary": "无法调用搜索工具获取相关信息，无法完成本次餐厅推荐任务。",
  "content": "无法调用搜索工具获取相关信息，无法完成本次餐厅推荐任务。",
  "duration_seconds": 25.387,
  "tool_calls_made": 4,
  "chat_id": "919231551"
}
```

AgentTaskRecord 中 `status = 'completed'`（非 'failed'），`error_summary = None`。这意味着 Researcher agent 的 LLM loop 正常结束（它识别出所有 tool call 都失败后自行生成了失败总结），而非 runtime 抛异常。

### A.7 精确时间轴 (UTC+8)

| 时间 | 事件 |
|------|------|
| 23:34:16 | 用户首条消息进入 (QQ) |
| 23:34:22 | coalesce 窗口关闭（最后一条消息 + 0.5s） |
| 23:34:28.858 | task 创建 (`agent_tasks.created_at`) |
| 23:34:28.859 | seq=1: agent_started (Runtime 启动) |
| 23:34:29.019 | seq=2: agent_started (EventBus 派生) |
| 23:34:34.283 | seq=3: **第 1 次 tool call 失败** (距创建 5.4s) |
| 23:34:37 | "在查，等一下" 用户可见 (send_fn) |
| 23:34:38.013 | seq=4: **第 2 次 tool call 失败** (距上次 3.7s) |
| 23:34:45 | 用户 status_probe |
| 23:34:45 | LapWing 回复 "卡住了..." |
| 23:34:49.378 | seq=5: **第 3 次 tool call 失败** (距上次 11.4s) |
| 23:34:52.288 | seq=6: **第 4 次 tool call 失败** (距上次 2.9s) |
| 23:34:54.406 | seq=7: agent_completed (距创建 25.5s) |
| 23:34:54 | 失败通知用户可见 |

### A.8 mutation log 所有相关条目

来源：`data/logs/mutations_2026-05-08.log`，共 6 条：

| mutation_id | timestamp (epoch) | event_type |
|-------------|-------------------|------------|
| 13317 | 1778254468.859 | tool.result (delegate_to_researcher → success, background accepted) |
| 13322 | 1778254469.056 | agent.task_started |
| 13337 | — | agent.tool_called (#1, missing_dispatcher) |
| 13351 | — | agent.tool_called (#2, missing_dispatcher) |
| 13362 | — | agent.tool_called (#3, missing_dispatcher) |
| 13366 | — | agent.tool_called (#4, missing_dispatcher) |
| 13369 | — | agent.task_done (failure summary) |

---

## B. 同步 vs 后台 delegation 的代码等价性

### B.1 同步路径 services 构造

**文件**: `src/tools/agent_tools.py:232-268`

```python
async def _run_agent(..., ctx: ToolExecutionContext) -> ToolExecutionResult:
    svc = ServiceContextView(ctx.services or {})       # line 236
    agent = await _resolve_agent(registry, agent_name,
        services_override=svc.raw)                      # line 244 — 传全量 raw
    missing_services = _missing_required_agent_services(
        agent_name, svc.raw)                            # line 260 — 校验
    if missing_services:
        return ToolExecutionResult(success=False, ...,
            reason=f"agent_services_unavailable: ...")  # line 264-266 — 硬拒绝
```

`_missing_required_agent_services` (agent_tools.py:111-115):
```python
required = list(_AGENT_BASE_REQUIRED_SERVICES)  # ("dispatcher", "tool_registry", "llm_router")
if agent_name == "researcher":
    required.extend(_RESEARCHER_REQUIRED_SERVICES)  # ("research_engine", "ambient_store")
return [key for key in required if services.get(key) is None]
```

**5 个必需 key 缺一不可**。任一缺失都会在 agent 执行前返回 `agent_services_unavailable`。

### B.2 后台路径 services 构造

**文件**: `src/tools/agent_tools.py:396-442`

```python
async def _start_background_delegate(*, agent_name, task, ctx, ...):
    svc = ServiceContextView(ctx.services or {})
    handle = await supervisor.start_agent_task(
        services=svc.raw,      # ← 直接传，无校验
        ...
    )
    return ToolExecutionResult(success=True, ...)  # 无条件返回 success
```

**差异**: 无 `_missing_required_agent_services` 调用。services 直接透传，不检查 `dispatcher`、`tool_registry`、`llm_router`、`research_engine`、`ambient_store` 是否非 None。

### B.3 snooker 修复覆盖范围

snooker 修复 commit `056b5a1`（已合入 master）包含：

| 修复点 | 文件 | 行号 | 覆盖同步 | 覆盖后台 |
|--------|------|------|---------|---------|
| `_missing_required_agent_services` 校验 | agent_tools.py | 111-115, 260-266 | **是** | **否** |
| `_hard_tool_error_reason` 传播 | agent_tools.py | 118-125, 164 | **是** | **否** |
| Legacy agent setattr services | registry.py | 128-129 | 是 | 是 |

**明确盲区**: `_missing_required_agent_services` 只在 `_run_agent`（同步路径）中调用，`_start_background_delegate`（后台路径）不调用。`_hard_tool_error_reason` 同理——它检查的是同步 `agent.execute()` 返回的 `AgentResult.tool_errors`，而后台路径通过 EventBus 异步回传，不经过该校验函数。

注意：registry.py:128-129 的 `setattr(agent, "_services", services_override)` 对本次 task 不适用——researcher 不是 legacy agent（`agent_registry.register()` 未被调用过），每次都走 `AgentFactory.create(spec, services_override)` 创建新实例。

### B.4 TaskSupervisor 的 services 传递

**文件**: `src/core/concurrent_bg_work/supervisor.py:314-341`

```python
def _spawn_runtime(self, record, services):
    child_services = dict(services)     # 浅拷贝
    child_services.update({
        "agent_event_bus": self.event_bus,
        "background_task_id": record.task_id,
        "background_chat_id": record.chat_id,
        "background_owner_user_id": record.owner_user_id,
    })
    runtime = AgentRuntime(..., services=child_services, ...)
```

**无过滤/裁剪/key rename**。只是追加 4 个 key。如果传入的 `services` 缺少 `dispatcher`，`child_services` 也会缺少。

### B.5 AgentFactory.create 的 services_override 处理

**文件**: `src/agents/factory.py:27-119`

```python
def create(self, spec, services_override=None):
    if spec.kind == "builtin":
        return self._create_builtin(spec, services_override=services_override)

def _create_builtin(self, spec, *, services_override=None):
    if spec.name == "researcher":
        return Researcher.create(self.llm_router, self.tool_registry,
            self.mutation_log, services=services_override)
```

**无完整性校验**。直接 set 到 `BaseAgent.__init__(..., services=services)` → `self._services = services or {}`。

---

## C. browser_vision 是否触发

### C.1 BrowserManager 是否被实例化或调用

**否**。4 次 tool call 全部是 `research`，没有 `browse`。`research` 工具走 `ResearchEngine.research()`（后端搜索 API），不走 `BrowserManager`。

### C.2 `slot="browser_vision"` 是否触发

**否**。未到达 `browser_manager.py:1395`。

### C.3 是否调了 `browse` 工具

**否**。

### C.4 browser 路径错误

**不适用**。本次故障在 dispatcher 层就拦截了，从未到达任何 tool executor。

**结论**: browser_vision 已知 bug 与本次故障无关。排除。

---

## D. 21 秒首响的时间分解

从 23:34:16 用户消息进入 → 23:34:37 "在查，等一下" 发出（~21 秒）：

| 阶段 | 耗时 | 说明 |
|------|------|------|
| coalesce 窗口 | ~0.5s | OWNER_COALESCE_SECONDS，等待追加消息 |
| LLM 第一次 planning | ~6.5s | 从 coalesce 结束 (23:34:22.5) → delegate_to_researcher 返回 (23:34:28.8) |
| "在查，等一下" 文本生成 | ~8s | LLM 在 tool_result 后继续生成 interim reply |

mutation #13323 确认 model 为 **minimax-m2.7**（火山方舟），slot 为 `main_conversation`。**未 fallback 到慢路径**。

**6.5s 的第一次 planning 延迟分析**: mutation #13323 的 `llm.request` payload 显示主对话 history 包含约 10+ 轮历史消息和 3 条 rapid-fire 用户消息的合并内容。prompt 很大（~4200 token system prompt + history + voice.md），这解释了 planning 耗时。

**8s 的文本生成延迟分析**: LLM 在 `tool.result` 返回后需要 (a) 理解 tool result payload、(b) 生成文字回复。这两步在 minimax-m2.7 非流式模式下可能延迟较高。

**结论**: 21 秒首响中，LLM planning (~6.5s) 和文本生成 (~8s) 各占约一半。这与 dispatcher 缺失故障是两个独立现象，不要合并分析。

---

## E. status_probe 的事实来源

### E.1 23:34:45 时 task store 中的 task 状态

23:34:45 (UTC 15:34:45) 时刻，task 状态为 **RUNNING**：
- 第 1、2 次 tool call 已失败（15:34:34、15:34:38）
- 第 3、4 次 tool call 尚未发生（15:34:49、15:34:52）
- task 的 `failed_at` / `completed_at` 尚未设置

### E.2 "卡住了，可能是工具调度或外部检索异常" 的来源

这是 **LLM 推测**，不是 task state 的直接翻译。当时 task 状态是 RUNNING，brain 的 status_probe handler 可能调用了 `agent_tasks` 的 status 字段或从 EventBus 的进度摘要推断。

LLM 看到的事实：
- 自己之前说 "在查，等一下"（8 秒前）
- 用户追问 "是不是还在查"
- tool call 记录可能显示 delegate_to_researcher 返回了 `background: true` 但还没收到结果

LLM 用 "卡住了" + "可能是工具调度或外部检索异常" 来解释延迟。但实际上 4 次 tool call 都是 `missing_dispatcher`，与 "工具调度" 猜测方向一致（巧合），但 LLM 并不真正知道 dispatcher 缺失。

### E.3 task 真实 failed_at vs status_probe 时间

```
status_probe 处理时间: 23:34:45 (UTC 15:34:45)
task 第 3 次 tool call: 23:34:49 (UTC 15:34:49)
task 第 4 次 tool call: 23:34:52 (UTC 15:34:52)
task completed_at:     23:34:54 (UTC 15:34:54)
```

**task 在 status_probe 时尚未结束**。`failed_at` 不存在（task 最终 status 为 `completed`，不是 `failed`）。

**结论**: "卡住了" 是 LLM 推测。从分析中应剔除这句话作为故障证据——它只是 LLM 对延迟的自然语言解释，不是状态机事实。

---

## F. 搜索 provider 配置

本次故障无需深入 provider 层，因为 tool call 在到达 `research` tool executor 之前就被 dispatcher guard 拦截了。但为完整性记录当前配置：

| 配置项 | 值 |
|--------|-----|
| 后端 1 | Tavily (`api.tavily.com`, weight 1.0) |
| 后端 2 | Bocha/博查 (`api.bochaai.com`, weight 0.7) |
| Scope router | `ScopeRouter.decide()` — 中文查询 → scope="cn" |
| ProxyRouter | `server=""` 时禁用 |
| fetcher | `SmartFetcher` (带 browser_manager + proxy_router) |
| engine timeout | 30s |

API key 在 `SearchConfig` (settings.py:470-474) 中配置，通过 `AppContainer` 注入 `ResearchEngine`。

---

## 7. 故障树结论

```
task_b48b60871a03 失败
  │
  ├─ 软失败（researcher 找到信息但返回 low confidence）── 排除
  │   └─ tool call 从未成功执行
  │
  └─ 硬失败（工具/服务不可用）
        │
        ├─ search API 不可用 ── 排除
        │   └─ tool call 被 dispatcher guard 拦截，未到达 API
        │
        ├─ browser_vision 已知 bug ── 排除
        │   └─ 从未调用 browse 工具
        │
        └─ ★ ToolDispatcher 缺失 ── 确认
              │
              ├─ 直接原因: BaseAgent._execute_tool() 中
              │   services.get("dispatcher") 返回 None，
              │   guard 返回 "tool_forbidden" / "missing_dispatcher"
              │
              └─ 根因假设（按可能性排序）:
                    │
                    ├─ [最可能] Brain._dispatcher_ref 在 _build_services()
                    │   调用时尚未设置，导致 dispatcher key 未注入 services dict。
                    │   后台 AgentRuntime 拿到一个不含 dispatcher 的 services。
                    │   Brain._build_services() line 295 对 dispatcher 是条件注入。
                    │
                    ├─ [可能] services dict 在 _build_services → _start_background_delegate
                    │   → supervisor.start_agent_task → _spawn_runtime 的某个环节被
                    │   意外替换/清空。需要更详细的日志来排除。
                    │
                    └─ [低可能] services_override 在 AgentFactory.create →
                        Researcher.create → BaseAgent.__init__ 链中丢失。
                        但代码审查显示这一链是直接透传，无过滤。

根本防护缺失: 后台 delegation 路径 (_start_background_delegate)
缺少同步路径 (_run_agent) 已有的 _missing_required_agent_services 预校验。
这是 snooker 修复 (056b5a1) 的明确盲区。
```

---

## 8. 建议修复

**P0 — 在后台 delegation 入口加 services 预校验:**

`_start_background_delegate()` (agent_tools.py:396) 中，在调用 `supervisor.start_agent_task()` 之前，添加与 `_run_agent` 相同的 `_missing_required_agent_services` 检查。如果必需服务缺失，返回 `ToolExecutionResult(success=False, reason="agent_services_unavailable: ...")`，让主对话 LLM 立即知道搜索不可用，而不是等待 25 秒后台任务超时后再通知用户。

**P1 — 加日志确认 dispatcher 注入状态:**

在 `_spawn_runtime()` 中加一条 debug log 打印 `child_services` 中以下 key 的存在性：`dispatcher, tool_registry, llm_router, research_engine, ambient_store`。如果任一缺失，log warning 级别。

**P2 — AgentFactory.create 加防御性校验:**

在 `_create_builtin` 中，对 researcher agent 检查 services_override 是否包含 5 个必需 key，缺失时抛明确异常（而非静默创建）。

---

## 附录: 数据来源

| 数据 | 来源 |
|------|------|
| agent_tasks 记录 | `data/lapwing.db` → `agent_tasks` 表 |
| agent_events (7 条) | `data/lapwing.db` → `agent_events` 表 |
| mutation log (6 条) | `data/logs/mutations_2026-05-08.log` |
| 对话时间线 | mutation log + agent_events occurred_at |
| 代码路径 | 多文件代码审查 (见各节引用) |
