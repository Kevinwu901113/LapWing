# Agent Team 测试报告

日期：2026-04-26

测试目标：验证 Lapwing Agent Team（两层直调架构）的端到端调度链路，
为后续重构 / 调参提供基线观测。本报告基于 `master` 分支当前代码（HEAD `762bccb`）。

---

## 1. 架构概览

### 1.1 当前文件结构

```
src/agents/
├── __init__.py           （空 docstring）
├── types.py              AgentSpec / AgentMessage / AgentResult 数据类
├── base.py               BaseAgent — 通用 tool loop（每个 agent 独立循环）
├── researcher.py         Researcher — research/browse 工具，模型槽 agent_researcher
├── coder.py              Coder — ws_file_*/run_python_code，模型槽 agent_coder
└── registry.py           AgentRegistry — name → BaseAgent 的字典封装
```

```
src/tools/agent_tools.py
└── 注册两个对外工具：
    ├── delegate_to_researcher
    └── delegate_to_coder
    两者共享 _run_agent(agent_name, request, context_digest, ctx)
```

```
tests/agents/
├── test_types.py              数据模型字段/默认值
├── test_registry.py           AgentRegistry register/get/list
├── test_base_agent.py         BaseAgent.execute（无工具/带工具/超时/超轮/profile 路径）
├── test_researcher.py         Researcher.create + profile 白名单
├── test_coder.py              Coder.create + 工作区限制
├── test_e2e_delegation.py     既有端到端：context_digest、parent_task_id、白名单违规
└── test_e2e_chain_trace.py    本次新增 — 全链路追踪（每步输入/输出/耗时）
```

### 1.2 两层调度流程

当前架构是 **两层直调**（旧的 TeamLead 中间层已被移除）：

```
[user message]
    │
    ▼
LapwingBrain.think_conversational
    │   build StateView, call LLMRouter
    ▼
LLMRouter.complete_with_tools (slot=chat)
    │   返回 tool_call(name="delegate_to_researcher" 或 "delegate_to_coder")
    ▼
ToolRegistry.execute(req)
    │   按 name 查到 ToolSpec → 执行 executor
    ▼
delegate_to_researcher_executor（在 src/tools/agent_tools.py）
    │   ctx.services["agent_registry"].get("researcher")
    │   构造 AgentMessage(task_id, content, context_digest, parent_task_id)
    ▼
Researcher.execute (BaseAgent.execute)
    │   独立 tool loop：
    │     while round < max_rounds:
    │         LLMRouter.complete_with_tools(slot=agent_researcher, tools=profile)
    │         if no tool_calls: 结束 → AgentResult(status="done", result=text)
    │         for tc in tool_calls:
    │             ToolRegistry.execute(tc) — 工具走 RuntimeProfile 白名单
    │             记录 AGENT_TOOL_CALL mutation
    ▼
AgentResult → _serialize_agent_result → ToolExecutionResult(payload={result,...})
    │
    ▼
回到 Brain 的 tool loop → 再调一次 LLMRouter → 生成最终用户可见文本
    │
    ▼
[user reply]
```

**关键观察**：架构里没有独立的 `Dispatcher` 组件。"选择哪个 agent" 这一步完全由
**主脑 LLM 通过工具命名**（`delegate_to_researcher` vs `delegate_to_coder`）完成；
`AgentRegistry.get(name)` 只是按名取实例的字典查询。这一点直接影响测试设计——
"dispatcher 选择正确的 agent" 实际上等价于 "主脑 LLM 输出的 tool_call.name 正确"。

### 1.3 关键配置（`config.toml`）

| 项 | 值 | 说明 |
|---|---|---|
| `agent_team.enabled` | `true` | 模块开关 |
| `task.max_tool_rounds` | `32` | 主脑外层循环上限 |
| `loop_detection.enabled` | `true` | 启用循环检测 |
| `loop_detection.blocking` | `false` | 仅观察、不阻断 |
| `Researcher.max_rounds` | `15` | hard-code 在 `researcher.py` |
| `Researcher.timeout_seconds` | `300` | 单次 LLM 调用超时 |
| `Coder.max_rounds` | `20` | hard-code 在 `coder.py` |
| `Coder.timeout_seconds` | `600` | 同上 |

> Agent 的 `max_rounds` / `timeout_seconds` 写死在源码而不读 `config.toml`，
> 这是 P2 级别的可改进点（见 §5）。

---

## 2. 已有测试运行结果

命令：`PYTHONPATH=. python -m pytest tests/agents/ -v --tb=short`
（输出存档：`/tmp/agent_test_output.txt`）

| 项 | 数量 |
|---|---|
| 总用例 | 35 |
| 通过 | **35** |
| 失败 | 0 |
| 跳过 | 0 |
| 总耗时 | 0.17s |

按文件分布：

| 文件 | 用例 | 结果 |
|---|---|---|
| `test_types.py` | 8 | ✅ 8/8 |
| `test_registry.py` | 4 | ✅ 4/4 |
| `test_base_agent.py` | 9 | ✅ 9/9 |
| `test_researcher.py` | 3 | ✅ 3/3 |
| `test_coder.py` | 3 | ✅ 3/3 |
| `test_e2e_delegation.py` | 8 | ✅ 8/8 |

无失败、无跳过。已有覆盖面：数据模型、注册表、tool loop（含超时/超轮/circuit
breaker）、工具白名单、parent_task_id 传递、context_digest 注入。

---

## 3. 端到端链路追踪记录

新增测试文件：`tests/agents/test_e2e_chain_trace.py`
完整命令：`PYTHONPATH=. python -m pytest tests/agents/test_e2e_chain_trace.py -v -s --tb=long`
完整输出：`/tmp/agent_e2e_output.txt`，结构化 trace：`/tmp/agent_e2e_chain_trace.json`

**结果：2/2 通过（0.11s）**

### 3.1 测试设计说明

`Brain` 类依赖太重（StateViewBuilder、TrajectoryStore、EventBus、MainLoop、
mutation_log、auth_gate……），实例化它需要大量 fixture。本测试**没有**实例化
Brain，而是把"主脑外层 tool loop"裁成最小骨架（`while not response.tool_calls`）
直接驱动 ToolRegistry。这层裁剪不会改变被测目标——主脑的真实路径
`TaskRuntime.complete_chat`（`src/core/task_runtime.py:389`）正是同样的循环结构。

mock 范围：仅 `LLMRouter`（用 `side_effect` 给定确定的响应序列）和 `research`
工具的 executor。`ToolRegistry`、`AgentRegistry`、`Researcher`、`Coder`、
`BaseAgent.execute`、`delegate_to_*_executor` 全是真实代码。

### 3.2 完整链路：用户请求 → Researcher 调研 → 返回结果

| 步骤 | 组件 | 输入（节选） | 输出（节选） | 耗时 |
|---|---|---|---|---|
| 1 | `Brain.think_conversational` | `chat_id="trace-chat-1"`, user="帮我查一下 2026 年最新的 RAG 论文" | 入队 | 0.00ms |
| 2 | `LLMRouter[brain.outer].complete_with_tools` | slot=`chat`, 4 个工具暴露（research/browse/delegate_to_researcher/delegate_to_coder） | tool_call: `delegate_to_researcher(request="…RAG…", context_digest="Kevin 在准备一份调研报告")` | 0.01ms |
| 3 | `Dispatcher (LLM-selected)` | requested_tool=`delegate_to_researcher` | selected_agent=`researcher` | 0.00ms |
| 4 | `LLMRouter[agent:researcher].complete_with_tools` | slot=`agent_researcher`, 2 个工具（research/browse） | tool_call: `research(question="2026 年最新的 RAG 论文有哪些")` | 0.01ms |
| 5 | `ToolRegistry[research].execute` | question, ctx.chat_id=`agent-task_6e9cb6e32c8d`, auth_level=1 (TRUSTED) | `{answer:"模拟答案：…", evidence_count:1}` | 0.01ms |
| 6 | `LLMRouter[agent:researcher].complete_with_tools` | last_message = tool_result(agent_tc_1, …) | text="调研报告：2026 年的 RAG 论文以 Mock RAG Paper 为代表。\[来源: …\]", no tool_calls | 0.01ms |
| 7 | `ToolRegistry.execute(delegate_to_researcher)` 整体耗时 | name + arguments | `success=true, payload={task_id, result, artifacts, evidence, execution_trace}` | **0.42ms** |
| 8 | `LLMRouter[brain.outer].complete_with_tools` | last_message = tool_result(brain_tc_1, …) | text="找到了，2026 年的 RAG 论文主要是 Mock RAG Paper。", no tool_calls | 0.01ms |
| 9 | `Brain.emit_user_reply` | round=1 | reply="找到了，2026 年的 RAG 论文主要是 Mock RAG Paper。" | 0.03ms |

**最终用户可见回复**：`"找到了，2026 年的 RAG 论文主要是 Mock RAG Paper。"`

### 3.3 验证的不变量（断言）

1. ✅ Dispatcher 选择正确：第一次主脑 LLM 输出 `delegate_to_researcher` 而非 `delegate_to_coder`
2. ✅ Researcher 的 tool call 序列正确：恰好调用一次 `research`，question 包含 "RAG"
3. ✅ LLM 调用计数正确：主脑 2 次 + Researcher 2 次 = **4 次**（无中间 TeamLead 层）
4. ✅ Mutation log 完整 lifecycle：`AGENT_STARTED` + `AGENT_TOOL_CALL` + `AGENT_COMPLETED` 都出现，无 `AGENT_FAILED`
5. ✅ 结果回传到主脑：最终回复包含 Researcher 调研出的关键词

### 3.4 第二个变体测试

`test_dispatcher_selects_coder_when_requested`：验证主脑 LLM 选择 `delegate_to_coder`
时，调度真的走到 Coder 实例（而非 Researcher）、内层 `run_python_code` 被执行、
最终结果含 `"hello"`。也通过（2 次 LLM 调用，Coder 跑了 1 个 tool call + 1 个终止轮）。

### 3.5 结构化 trace 摘录（`/tmp/agent_e2e_chain_trace.json`）

完整 trace 共 9 步、5812 字节 JSON。前 3 步示例：

```json
[
  {
    "seq": 1,
    "component": "Brain",
    "action": "think_conversational(receive)",
    "input": {"chat_id": "trace-chat-1", "user_message": "帮我查一下 2026 年最新的 RAG 论文"},
    "output": {"queued": true},
    "duration_ms": 0.001,
    "started_at_s": 0.0
  },
  {
    "seq": 2,
    "component": "LLMRouter[brain.outer]",
    "action": "complete_with_tools",
    "input": {"slot": "chat", "tool_count": 4, "tool_names": ["research", "browse", "delegate_to_researcher", "delegate_to_coder"], ...},
    "output": {"text": "", "tool_calls": [{"name": "delegate_to_researcher", "arguments": {...}}]},
    ...
  },
  {
    "seq": 3,
    "component": "Dispatcher(LLM-selected)",
    "action": "route",
    "input": {"requested_tool": "delegate_to_researcher", "request_head": "..."},
    "output": {"selected_agent": "researcher"},
    ...
  }
]
```

---

## 4. 发现的问题

### P1 — 没有真正的 Dispatcher，"选 agent" 等价于 LLM 选 tool

**现象**：架构注释（`agent_tools.py` line 4）写"两层调度（Lapwing → Agent），
取代旧的三层（Lapwing → TeamLead → Agent）"，但读完代码会发现根本没有
"Dispatcher" 这个角色——选择 agent 是 LLM 在主脑外层一次 tool call 中完成的。
`AgentRegistry.get(name)` 只是字典 lookup，没有任何路由逻辑。

**影响**：
- 用户题面让我"验证 dispatcher 选择了正确的 agent"，但其实没有 dispatcher 可测——
  能测的只有 LLM 是否选对了工具名。这是**真实测试覆盖盲区**：当前所有
  delegate 测试都用 `side_effect` 直接喂 tool_call，从来没有测过 LLM 真的会
  在两个 delegate 工具间做出正确选择。
- 如果未来要加路由策略（例如按 token 预算选模型、按上下文长度选 Researcher
  还是 Coder），没有对应组件可挂钩。

**建议**：见 §5。

### P2 — Agent 配置硬编码，`config.toml [agent_team]` 只有一个 enabled 开关

**现象**：`Researcher.create()` 和 `Coder.create()` 把 `max_rounds`、
`max_tokens`、`timeout_seconds` 直接写死在源码里。`config.toml` 的
`[agent_team]` 段只有 `enabled = true`，没有任何调参口。

**影响**：要换 timeout 或 max_rounds 必须改源码 + 重启，无法 hot-reload。
对调试 / 在线调参不友好。

**建议**：把这些字段下沉到 `config.toml`，例如：

```toml
[agent_team.researcher]
max_rounds = 15
timeout_seconds = 300
max_tokens = 40000

[agent_team.coder]
max_rounds = 20
timeout_seconds = 600
max_tokens = 50000
```

### P2 — `BaseAgent` 的 LLMRouter 是构造时注入的硬依赖

**现象**：`BaseAgent.__init__` 接收 `llm_router`，没有 setter 也没有 factory
hook。这本身没问题（DI 模式正确），但**子 agent 与主脑共用同一个 router 实例**，
没有"per-agent 的客户端隔离"——所有 agent 的请求计费、限流、debug 标签都混在
同一组 metric 里。

**影响**：观测层难以区分主脑 vs 子 agent 的 token 消耗，cost 归因不清晰。
现在唯一的区分手段是 `origin=f"agent:{self.spec.name}"` 字段，但要保证调用方
正确读取这个字段。

**建议**：在 `LLMRouter` 内部按 `origin` tag 维度聚合 metrics（可能已有，需
verify），并在 `__init__` 提示 "agent 必须传 origin 否则计费混淆"。

### P3 — `delegate_to_*` 和 `research` / `browse` 同时暴露给主脑

**现象**：测试 trace 第 2 步显示主脑 LLM 同时看到 4 个工具：`research`、`browse`、
`delegate_to_researcher`、`delegate_to_coder`。这意味着主脑可以**直接**调
`research`，绕过 Researcher。

**影响**：
- 选择熵增大：主脑要在"自己 research"和"派给 Researcher"之间纠结
- 与 CLAUDE.md "更多工具 → 更高选择熵和延迟" 警告冲突
- 可能让 Agent Team 形同虚设——如果主脑总是直接调 research，Researcher 永远
  不会被触发

**建议**：明确分工——主脑只看到 `delegate_to_*`，调研类 raw 工具从
`CHAT_SHELL_PROFILE` / `CHAT_EXTENDED_PROFILE` 中移除（或反过来：去掉
`delegate_to_*` 让主脑自己跑）。当前两条路都开着是最差的设计。

### P3 — `services` 字典是 weakly-typed dict[str, Any]

**现象**：`ToolExecutionContext.services: dict[str, Any]`。`agent_tools.py`
里 `ctx.services.get("agent_registry")` 拿不到时只返回模糊错误 "Agent Team 未就绪"。

**影响**：可测性 OK（很容易塞 mock），但调试 production 配置错误时只能看错误
信息猜哪个 service 没注册。

**建议**：建一个 typed `ServicesContainer` dataclass（即使内部仍存 dict），暴露
`.agent_registry` / `.trajectory_store` 等字段，类型检查可在启动期发现缺漏。

### P3 — `_extract_evidence` 只看 `role=="tool"` 的 message

**现象**：`base.py:346` 的 `_extract_evidence` 遍历 `messages` 找 `role=="tool"`，
但 `build_tool_result_message` 实际返回的是 `role="user"` + `tool_result` block
（Anthropic 风格）或者 `tool` role（OpenAI 风格）——格式取决于 provider。

**影响**：在 Anthropic 路径下 `evidence` 永远是空 list。本次 e2e 测试 trace 第
7 步的 `payload.evidence` 实际为空就反映了这点。

**建议**：让 `_extract_evidence` 同时识别两种格式，或者把 evidence 收集移到
`_execute_tool` 内部，那里直接拿到原始 ToolExecutionResult.payload 不需要回猜
message 格式。

---

## 5. 建议

### 5.1 测试覆盖率改进

1. **真实 LLM 选择测试**（覆盖 §4 P1）：用 deepeval 或简易 fixture 实测主脑
   面对 "查一下 X" / "写段代码" / "查完后写代码" 三类 prompt 时，分别选择
   `delegate_to_researcher` / `delegate_to_coder` / 串联两者。可以用 eval 标记
   gating（`pytest --run-evals`），不影响日常 CI。
2. **mutation_log payload schema 断言**：当前测试只断言 `MutationType.X in types`，
   没断言 payload 字段。建一个共用 helper `assert_agent_lifecycle_payload(ml)`
   验证 `task_id` / `agent_name` / `tool_calls_made` / `duration_seconds` 都存在。
3. **`evidence` 字段端到端断言**（覆盖 §4 P3 evidence bug）：写一个测试，让
   `research` 工具返回带 `source_url` 的结果，断言最终 `AgentResult.evidence` 非空。
4. **delegate 失败传播**：当前没有"Researcher 内部超时 → delegate 工具收到
   `success=false` → 主脑如何处理"的链路测试。

### 5.2 可测试性改进

1. **`Brain` 拆分 testable 核心**：当前 `Brain.__init__` 接受 ~15 个 optional
   依赖，写测试要么 mock 全套（重）要么直接绕过 Brain（本次做法）。建议抽出
   `BrainCore`（只含 `_complete_chat` + tool loop），`Brain` 类剥成 thin wrapper
   做依赖装配。这样 e2e 测试可以直接用 `BrainCore` + minimal deps。
2. **暴露 `Dispatcher` 接口**（覆盖 §4 P1）：即使内部仍是 `AgentRegistry.get`，
   建一个 `AgentDispatcher.choose(tool_name) -> BaseAgent` 接口，让"路由策略"
   有可挂钩点。未来要加"按上下文长度自动切 Coder/Researcher"或"负载均衡多个
   同名 agent 实例"时不必动 `agent_tools.py`。
3. **配置注入**（覆盖 §4 P2）：`Researcher.create()` 增加 `spec_overrides` 参数
   或读 `config.toml`。这样测试可以用 `max_rounds=2` 跑得更快。
4. **可注入的时钟**：trace 里带了 `time.perf_counter()`，要测"超时分支" / 
   "duration_seconds 字段" 必须真的等。建议把 `time.perf_counter` 抽成
   `BaseAgent._clock` 属性，测试可以用 `MagicMock` 控制。
5. **trace hook**：把本次测试里的 `ChainTracer` 思路抽成 `BaseAgent` 的可选
   `tracer` 注入参数，production 不开销，测试和 debug 模式打开就能拿到完整
   step-by-step trace。比读 mutation log 更方便。

### 5.3 立刻可做的小修

- 删掉或注释 `agent_tools.py` line 4 的"取代旧的三层"误导性表述，改成"由主脑
  LLM 直接选择 delegate_to_* 完成路由"。
- `agent_tools.py` 的 `_AGENT_MAX_ITERATIONS = 30` 是死代码（没人引用）。删除。
- `BaseAgent._execute_tool` 的 `_noop_shell` 把 stderr 写成英文 "Shell disabled
  for agents"，与项目其他错误信息中文化的风格不一致——小事，但一致性问题。

---

## 附录 A：本次新增文件

- `tests/agents/test_e2e_chain_trace.py`（无修改任何 src/）

## 附录 B：关键产物路径

- 已有测试输出：`/tmp/agent_test_output.txt`
- 新测试输出：`/tmp/agent_e2e_output.txt`
- 结构化 trace JSON：`/tmp/agent_e2e_chain_trace.json`
