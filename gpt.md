可以。下面我直接给你一份 **Lapwing 动手能力增强路线图**，按你现在的代码结构来拆，重点是把它先拉近 **Pi agent 的执行深度**，再给未来往 **OpenClaw 的平台化** 留口子。

---

# 总判断

你现在的“动手能力”瓶颈，不在模型，也不在 prompt，主要在 4 个地方：

1. **执行闭环分散在 `LapwingBrain` 里**
   现在是产品逻辑、工具调度、shell 策略、确认状态混在一起。

2. **文件修改能力太粗**
   目前更像“整文件写入 + shell 命令修改”，缺细粒度 patch/edit。

3. **任务完成验证不够标准化**
   现在更多是“命令成功了”，不是“任务真的完成了”。

4. **事件流和任务状态机不明确**
   桌面端能看事件，但 agent/runtime 还没有统一的任务生命周期。

所以路线应该是：

```text
先把执行能力做深
-> 再把执行流程做标准
-> 再把入口和平台层解耦
```

---

# 第一阶段：先补“Pi 式执行深度”

目标：让 Lapwing 从“会调用工具”升级成“能稳定完成任务”。

这阶段先不要碰多渠道，也不要急着做 gateway。

---

## 1. 把执行闭环从 `brain.py` 拆出来

### 当前问题

`src/core/brain.py` 现在承担了太多职责：

* 对话上下文
* system prompt
* tool schema
* tool rounds
* shell consent
* shell 状态机
* memory 拼装
* dispatcher 协作

这会让“动手能力”变成 Brain 的副产物，而不是一个独立运行时。

### 建议拆分

新增：

```text
src/runtime/
  session.py
  tool_loop.py
  task_runner.py
  execution_state.py
  verifier.py
```

### 具体拆法

#### A. 新建 `src/runtime/tool_loop.py`

把 `brain.py` 里这些职责挪进去：

* `_chat_tools()`
* 工具轮次控制
* tool call 解析与执行
* `_MAX_TOOL_ROUNDS`
* tool result 回填

建议定义一个统一入口：

```python
class ToolLoop:
    async def run(
        self,
        messages: list[dict],
        tools: list[dict],
        context: RuntimeContext,
    ) -> ToolLoopResult:
        ...
```

### 为什么先拆这个

因为这是你最接近 Pi 的地方。
Pi 的强项就是标准化 tool loop。你现在不是没有，而是埋在 `brain.py` 里了。

---

#### B. 新建 `src/runtime/execution_state.py`

把 shell 相关状态从 `brain.py` + `shell_policy.py` 里分离成统一的任务状态对象：

```python
@dataclass
class ExecutionState:
    task_id: str
    chat_id: str
    status: Literal[
        "started", "planning", "awaiting_consent",
        "executing", "verifying", "recovering",
        "completed", "failed", "blocked"
    ]
    attempts: int = 0
    last_error: str | None = None
    artifacts: list[str] = field(default_factory=list)
```

### 价值

这样你以后：

* API
* 桌面端
* heartbeat
* agent
  都能看到统一状态，而不是各自猜。

---

#### C. 新建 `src/runtime/task_runner.py`

把“任务级多步执行”从 Brain 中独立出来：

```python
class TaskRunner:
    async def run_task(self, task: AgentTask, ctx: RuntimeContext) -> TaskResult:
        ...
```

这里统一负责：

```text
prepare -> execute -> verify -> recover -> finalize
```

你的 coder、researcher、file 这些 agent 后面都可以走这条骨架。

---

## 2. 增强文件编辑能力，不要只靠 `write_file`

### 当前问题

你现在的本地执行更多靠：

* `execute_shell`
* `read_file`
* `write_file`

这会导致复杂修改很脆弱：

* 改一小段逻辑要重写整文件
* 修改容易覆盖上下文
* 回滚不方便
* diff 不清晰

### 建议新增工具

在 `src/tools/` 下新增：

```text
src/tools/file_editor.py
```

提供至少这几个工具：

* `read_file_segment(path, start_line, end_line)`
* `replace_in_file(path, old_text, new_text)`
* `replace_lines(path, start_line, end_line, new_text)`
* `insert_after(path, anchor, new_text)`
* `insert_before(path, anchor, new_text)`
* `append_to_file(path, content)`
* `diff_file(path, new_content)` 或 `preview_patch(...)`

### 为什么这一步很重要

这是你和 Pi 差距里最直观的一块。

Pi 类 coding agent 之所以更强，不只是因为会跑 shell，而是因为它能对代码库做**稳定的小步编辑**。
你现在如果补上这一层，coding 任务成功率会明显提升。

### 文件级改动建议

* `src/core/brain.py`：删除 `read_file/write_file` schema 的直接定义，改为从 registry 注入
* `src/agents/coder.py`：优先使用 `file_editor`，少用整文件写回
* `src/agents/file_agent.py`：改成直接围绕 `file_editor` 能力编排

---

## 3. 增加统一验证器，而不是“执行完就算完”

### 当前问题

现在很多任务成功标准偏弱：

* shell return code == 0
* 文件存在
* 有 stdout

但这不等于用户目标完成。

### 建议新增

```text
src/runtime/verifier.py
```

定义统一验证接口：

```python
class TaskVerifier:
    async def verify(self, objective: TaskObjective, result: TaskExecutionResult) -> VerificationResult:
        ...
```

### 第一批 verifier

先做 4 类：

#### A. 文件结果验证

* 文件是否存在
* 文件名/扩展名是否符合约束
* 内容是否包含要求字段
* 路径是否符合用户指定目录

这一部分你 `shell_policy.py` 已经有不少约束提取逻辑，可以复用。

#### B. 命令结果验证

* 命令成功不等于完成
* 是否生成预期产物
* 是否在正确目录执行

#### C. Python 代码验证

* 是否能运行
* 是否超时
* stdout/stderr 是否符合期望
* 是否需要二次修复

#### D. 项目任务验证

后面再加：

* pytest
* unittest
* lint
* mypy / pyright
* build

### 文件级改动建议

* `src/agents/coder.py`：执行完 `code_runner` 后走 verifier，而不是直接格式化输出
* `src/tools/code_runner.py`：增加结构化结果字段，例如 `artifacts`, `checks`
* `src/core/shell_policy.py`：把“约束提取”和“验证”拆成可复用函数供 verifier 调用

---

## 4. 给 `CoderAgent` 做成真正的多轮修复闭环

### 当前问题

`src/agents/coder.py` 现在逻辑还是偏短链：

```text
生成代码 -> 运行 -> 如果失败则修一次 -> 返回
```

这还不够 Pi 式。

### 建议改成

```text
analyze task
-> generate code or patch
-> run
-> inspect error
-> repair
-> re-run
-> verify
-> stop on success / max attempts
```

### 建议修改

新增：

```python
_MAX_FIX_ATTEMPTS = 3
```

然后把 `CoderAgent.execute()` 改成循环式：

```python
for attempt in range(_MAX_FIX_ATTEMPTS):
    run result
    if verify ok:
        break
    code = await self._fix_code(...)
```

### 再补两点

#### A. 区分“生成新代码”和“修改现有文件”

现在 `CoderAgent` 更偏“生成并运行 Python 片段”。
你要补一个模式：

* 当用户目标是修改现有项目时，先读目标文件
* 然后生成 patch
* 应用 patch
* 跑测试/命令
* 修复

#### B. 增加工作区模式

比如新增：

```python
metadata = {
  "mode": "snippet" | "workspace_patch",
  ...
}
```

这样它才能慢慢从“代码片段代理”升级成“代码库代理”。

---

## 5. 给工具做统一注册，不要继续在 `brain.py` 手写 schema

### 当前问题

现在工具 schema 直接写在 `brain.py` 里，这样：

* 加工具要改 Brain
* 工具实现与 schema 分离
* 权限管理不方便
* 扩展性差

### 建议新增

```text
src/tools/registry.py
src/tools/types.py
```

### 统一结构

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    risk: Literal["low", "medium", "high"]

class Tool:
    spec: ToolSpec

    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        ...
```

### 第一批纳管的工具

* shell_executor
* file_editor
* code_runner
* web_search
* web_fetcher
* transcriber

### 文件级改动建议

* `src/core/brain.py`：删 `_chat_tools()` 的硬编码
* `src/core/llm_router.py`：继续只负责模型接口，不碰具体工具
* `src/agents/*`：通过 registry 取工具，而不是直接 import 执行函数

---

# 第二阶段：把执行过程“可观测、可恢复”

目标：让动手能力不只是能跑，还要能看、能停、能恢复。

---

## 6. 扩展 event bus，让事件真正覆盖执行生命周期

### 当前问题

你已经有：

* `src/api/event_bus.py`
* `src/api/server.py`
* 桌面端 SSE

这是很好的基础，但现在事件更像“产品日志”，不是“runtime 事件协议”。

### 建议统一事件类型

新增统一规范：

```text
src/runtime/events.py
```

事件至少包括：

* `task.started`
* `task.planning`
* `tool.called`
* `tool.succeeded`
* `tool.failed`
* `verification.started`
* `verification.passed`
* `verification.failed`
* `recovery.started`
* `task.completed`
* `task.failed`
* `task.blocked`

### 价值

这一步做完以后：

* 桌面端能清楚显示 agent 在干嘛
* heartbeat 行为也能被观察
* 以后做 control plane 时不需要重写协议

### 文件级改动建议

* `src/api/event_bus.py`：从松散 event 改成统一 schema
* `src/core/brain.py`、`src/agents/coder.py`、`src/tools/*`：统一发事件
* `desktop/`：按 task id 聚合展示，而不是按时间乱堆

---

## 7. 给 shell 执行增加“审批点”和“恢复建议”，但不要把策略绑死在 Brain

### 当前问题

你的 `src/core/shell_policy.py` 已经写得不少了，这很好。
但问题是它现在更像 Brain 的一个附属模块，而不是“执行治理层”。

### 建议调整定位

保留 `shell_policy.py`，但把它升级成：

```text
src/policy/
  shell_policy.py
  file_policy.py
  approval_policy.py
```

### 新职责

* 只做风险判断
* 只做审批决策
* 只做约束校验
* 不直接参与对话编排

### 这样拆的好处

以后不管入口是 Telegram、桌面端、还是未来 Web，
都能走同一套治理逻辑。

---

# 第三阶段：让 agent 更像“runtime 组件”，而不是业务脚本

目标：拉近 Pi，而不是继续堆产品逻辑。

---

## 8. 统一 `Agent` 接口，让 agent 都挂在任务运行时上

### 当前问题

现在 `src/agents/base.py` 的方向是对的，但各 agent 还是偏业务流程脚本。

### 建议演进

把 agent 统一成：

```python
class BaseAgent:
    name: str

    async def plan(self, task, ctx) -> PlanResult: ...
    async def execute(self, task, ctx) -> AgentResult: ...
    async def verify(self, task, result, ctx) -> VerificationResult: ...
```

不是每个 agent 都必须复杂实现，但接口先留好。

### 为什么重要

这样：

* `ResearcherAgent`
* `CoderAgent`
* `FileAgent`
* `TodoAgent`
  才会变成“标准执行单元”。

否则它们会越来越像分散的小应用。

---

## 9. 把 `dispatcher.py` 从“分类器”升级成“路由器”

### 当前问题

`src/core/dispatcher.py` 现在更像在做：

* 根据文本判断交给谁

这没问题，但还不够。

### 建议升级成

* 任务类型路由
* 能力路由
* 风险路由
* 执行模式路由

比如：

```python
@dataclass
class DispatchDecision:
    agent_name: str
    mode: Literal["chat", "tool_task", "workspace_task", "research"]
    requires_approval: bool
    verifier_type: str | None
```

### 文件级改动建议

* `src/core/dispatcher.py`：增加结构化返回，而不是只返回 agent
* `src/core/brain.py`：按 dispatch decision 决定走聊天、task runner、还是 agent path

---

## 10. 给 `LLMRouter` 加“执行用途”，而不只是 `chat/tool/heartbeat`

### 当前问题

`src/core/llm_router.py` 已经有 purpose 路由，这很好。
但现在 `tool` 太宽泛。

### 建议细化成

* `chat`
* `planning`
* `tool_reasoning`
* `codegen`
* `heartbeat`

哪怕底层暂时还是同一个模型，接口先细化。

### 价值

以后你能更容易做：

* 更便宜的 planning model
* 更强的 codegen model
* 更快的 heartbeat model

这一步会让你更接近 Pi 的 provider/runtime 抽象。

---

# 第四阶段：给未来 OpenClaw 化留接口，但先不全做

目标：现在不强行平台化，但把路修好。

---

## 11. 把 Telegram 从核心层剥开

### 当前问题

`main.py` 现在很重，初始化都堆在一起：

* 日志
* Brain
* agent 注册
* API server
* Telegram handlers
* heartbeat 启动

这是未来最大的扩展阻力。

### 建议拆成

```text
src/app/
  bootstrap.py
  container.py
  telegram_app.py
  desktop_app.py
```

### 具体拆法

#### A. `src/app/container.py`

负责组装：

* brain
* task runner
* registry
* event bus
* memory
* heartbeat
* agents

#### B. `src/app/telegram_app.py`

只负责 Telegram adapter：

* 接消息
* 调用 brain / runner
* 回发消息

#### C. `main.py`

只做：

```python
async def main():
    app = build_telegram_app()
    await app.run()
```

### 为什么这一步重要

以后你要接：

* Web chat
* CLI
* 桌面控制台
* API trigger
  都不会再动核心。

---

## 12. 桌面端从“观察面”升级成“执行控制台”

你现在的 `src/api/server.py` 已经很好了，是未来 control plane 的起点。

### 建议新增 API

* `/api/tasks`
* `/api/tasks/{task_id}`
* `/api/tasks/{task_id}/cancel`
* `/api/tasks/{task_id}/retry`
* `/api/tools`
* `/api/policies`
* `/api/approvals/pending`

### 这一步的意义

先别想着 OpenClaw 全量 gateway。
先把你自己的桌面端变成真正控制面。

这样你就已经比“纯 Telegram bot”高一个层次了。

---

# 我建议你的实际开发顺序

下面这个顺序最稳。

---

## Sprint 1：执行骨架重构

先做：

1. 新建 `src/runtime/tool_loop.py`
2. 新建 `src/runtime/task_runner.py`
3. 新建 `src/runtime/execution_state.py`
4. `brain.py` 只保留对话编排，不再亲自管完整执行流

### 验收标准

* 一条工具任务能被赋予 task id
* 有 started/executing/completed 状态
* 桌面端能看到任务流

---

## Sprint 2：文件编辑与验证

做：

1. `src/tools/file_editor.py`
2. `src/runtime/verifier.py`
3. `CoderAgent` 改成多轮修复
4. shell/file/code 都走 verifier

### 验收标准

* “修改某个 Python 文件并修复语法错误”能稳定闭环
* “在指定目录创建文件”能验证路径约束
* 失败时能明确进入 recovering

---

## Sprint 3：工具注册与策略分层

做：

1. `src/tools/registry.py`
2. `src/tools/types.py`
3. `src/policy/` 目录
4. `brain.py` 不再硬编码工具 schema

### 验收标准

* 新增工具不需要改 `brain.py`
* tool risk 能统一管理
* 不同 agent 共用同一套工具定义

---


## Sprint 4：入口解耦

做：

1. 拆 `main.py`
2. 新建 `src/app/container.py`
3. 新建 `src/app/telegram_app.py`
4. API/desktop 改走统一 task 视图

### 验收标准

* Telegram 只是 adapter
* 核心执行逻辑可以被 API 直接调用
* 为未来 CLI/Web 留好入口

---

# 你改完之后，会发生什么

## 对 Pi 的差距会明显缩小

因为你会补上最关键的几块：

* 标准 tool loop
* 任务状态机
* 文件 patch/edit
* 多轮修复
* verifier
* 工具注册

这些正是“执行深度”。

---

## 对 OpenClaw 的差距会开始变成“平台层差距”

也就是说，到那时你不再是“执行都不够稳”，
而会变成：

* 还没多渠道
* 还没 gateway
* 还没 node capability
* 还没完整 control plane

这就对了。因为平台层本来就该后做。

---

# 最后给你一个最直接的版本目标

## 你下一版最应该追求的形态

不是：

> 再多加几个 agent

也不是：

> 再做更复杂的人格 prompt

而是：

> **让 Lapwing 成为一个“有标准执行闭环的 personal agent runtime”**

具体对应到代码，就是这条主线：

```text
brain.py 变薄
tool_loop.py 成型
task_runner.py 成型
file_editor.py 补上
verifier.py 补上
main.py 拆掉
telegram 从核心剥离
```

这条路走完，你的“动手能力”会有一次非常明显的台阶式提升。

如果你要，我下一条可以继续直接给你一份
**“文件级重构清单（每个文件改什么、先后顺序、建议接口草图）”**。
