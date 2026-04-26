# Step 6 — AgentResult 字段判定

## 原 spec 期望 vs 现状

| 字段 | Spec 期望 | 现状 (`src/agents/types.py`) |
|------|-----------|------------------------------|
| success | bool | 无——由 `status == "done"` 派生 |
| output | str | `result: str` |
| artifacts | list | `artifacts: list[str]`  |
| tool_calls_made | int | 无 |
| duration_seconds | float | 无 |
| error | str \| None | `reason: str` |
| - | - | `task_id: str` |
| - | - | `status: "done"/"failed"/"blocked"` |
| - | - | `evidence: list[dict]` |
| - | - | `attempted_actions: list[str]` |

## 消费者实际需要什么

`AgentResult` 出生处：`BaseAgent._finalize_done` / `_finalize_failed`。
全量消费者只有两个：

1. **`src/tools/agent_tools.py` 的 `delegate_executor` /
   `delegate_to_agent_executor`**：把 AgentResult 转成 ToolExecutionResult。
   - 读 `status` 判成功与否
   - 读 `result` 填 payload
   - 读 `artifacts` / `evidence` 填 payload
   - 读 `reason` 填 `ToolExecutionResult.reason`
   - 不读 `tool_calls_made` / `duration_seconds`

2. **`BaseAgent._finalize_*` 本身**：emit mutation 时需要 `tool_calls_made`
   + `duration_seconds`，但这两项在 emit 时已经在本地作用域可用——不需要
   先塞进 AgentResult 再从 AgentResult 读。

结论：**现有字段完全覆盖消费需求。Spec 提议的 `tool_calls_made` /
`duration_seconds` / `success` / `output` / `error` 都没有实际消费
场景，加上只会增加无用字段 + 导致 producer/consumer 两边都要写冗余
逻辑。**

## 决定：不改 AgentResult schema

保留现有 7 个字段（task_id/status/result/artifacts/evidence/reason/
attempted_actions）。不加 property alias（`success` / `output` 等），
不做破坏性重命名。

`tool_calls_made` + `duration_seconds` 仅以 **mutation payload 字段**存在
（`AGENT_COMPLETED` / `AGENT_FAILED` 的 payload），不污染 AgentResult。

## 退出条件 — 何时要改

| 触发条件 | 动作 |
|----------|------|
| MainLoop 或 StateSerializer 需要直接消费 AgentResult（当前它们不消费） | 加新字段 |
| 发现多个下游需要 `tool_calls_made` 做决策 | 把它从 mutation payload 抽回 AgentResult |
| 现有字段在新 Agent（browser / writer）下表达不足 | 按需扩展，不破坏现有 |

## 风险

无——ToolExecutionResult 的转换逻辑不变，前端 SSE 消费的 mutation
payload 字段齐全。
