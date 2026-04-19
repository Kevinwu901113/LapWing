# Step 6 — TeamLead 去留 + 双工具结构判定

## 评估两个耦合决策

两个问题必须一起决定——它们一致或一致地反：

1. **TeamLead**：保留为独立 Agent（有自己的 LLM 循环），还是降级为
   `delegate_task` 工具内的编排逻辑？
2. **工具结构**：保留 `delegate` + `delegate_to_agent` 两段，还是合并为
   单个 `delegate_task(agent=...)`？

## TeamLead 的实际工作

读现有 `src/agents/team_lead.py` 的 system prompt——TeamLead 做三件事：

1. 分析 Lapwing 的请求
2. 选择合适的 Agent（只有 researcher / coder 两个候选）
3. 拆解多步任务（如"查资料然后写代码"→ researcher 先 → coder 后）

第 1 + 2 步在只有两个 Agent 的当下其实很薄——Lapwing 自己判断"这是查
资料还是写代码"的成本和 TeamLead 判断的成本差不多。真正有价值的是第 3
步（多步编排），但也只是在多步任务时才触发。

## 延迟成本

每次 delegate 多一轮 LLM 调用（TeamLead round1 + round2）。按当前
`agent_execution` slot 的平均延迟（~3–5 秒/轮），相当于每次 delegate
多 6–10 秒用户等待。`tell_user` 在 delegate 之前已经说了"等我让团队
看看"——但 10 秒也是实打实的等待。

## 保留 vs 降级

| 维度 | 保留独立 Agent | 降级为编排逻辑 |
|------|----------------|----------------|
| 任务拆解质量 | LLM-driven，flexible | 需要硬编码 or 二次让 Lapwing 拆 |
| 延迟（单 Agent 任务） | +6–10s | 0 |
| 延迟（多 Agent 任务） | +6–10s | 0 + Lapwing 需自己链式调 |
| 代码量 | 当前 68 行 + 1 个 prompt | 需要编排 helper，可能相当 |
| 可观察性 | 多一层 `AGENT_STARTED/COMPLETED` | 少一层；更扁平 |
| 换 Agent 集合时 | 只改 TeamLead prompt | 需改 Lapwing prompt |

## 结论：保留 TeamLead + 双工具

**判断：TeamLead 保留独立 Agent 形式；工具继续 delegate + delegate_to_agent
双层结构。**

理由：

1. **当前只有 2 个 Agent，TeamLead 看似薄；但 Agent Team 刚启航**——
   Step 7+ 会加更多 Agent（Browser Agent、Writer Agent 等）。届时
   "选哪个"会真正变复杂，让 LLM 去选比硬编码规则好。现在拆了以后再加
   回来更贵。

2. **多步任务需要 LLM 编排**。硬编码"先 researcher 再 coder"解不了
   "看情况再决定下一步"这类动态依赖。TeamLead 的 LLM round2 本来就
   是做这件事——它拿到 researcher 的结果，再决定要不要派 coder。

3. **延迟成本不是 Step 6 的关键路径问题**。`tell_user("等我让团队看看")`
   + `commit_promise` 是 Step 5 配好的 UX 护栏——用户已经知道这事有
   延迟。多 6–10 秒不会改变用户体验质量。当真变成瓶颈时，再做"单
   Agent 任务 bypass TeamLead"的 fast path 优化即可。

4. **双工具结构一致性**。TeamLead 是 Agent → 它用 delegate_to_agent
   调子 Agent → Lapwing 是 Lapwing（不是 Agent）→ 她用 delegate 调
   TeamLead。这个层级清晰、对称、可扩展。合并成单工具 `delegate_task(agent=...)`
   会让 Lapwing 直接知道 Agent 名字——有味但没必要的耦合。

5. **现有 38 个测试（agents + agent_tools）围绕双工具 + TeamLead 构造**。
   推倒重来会丢掉这部分覆盖面。

## 需要复盘的触发条件

如果以下任一成立，重新评估：

- **6 个月后**：实际 usage data 显示 TeamLead 90% 的判断都是"简单转发"
  （无真正拆解、无动态链式）
- **多 Agent 任务占比 < 10%**：大部分请求其实是单 Agent 就能搞定
- **用户反馈"太慢了"**：延迟成为抱怨点
- **Agent 集合稳定在 ≤ 3**：再加也不显著增加选择复杂度

届时可重新考虑：
- 保留 TeamLead 但加 fast path（单 Agent 任务跳过 TeamLead 的 LLM round1）
- 或降级为纯编排逻辑（Lapwing 自己选 Agent，delegate_task 单工具）

## 本次不做的事

- 不删 TeamLead
- 不合并 delegate / delegate_to_agent
- 不加 fast path 优化（过早优化）
- 不改 TeamLead prompt（保留现有编排语义）

## 本次做了的事（与这个决策相关）

- TeamLead profile（`AGENT_TEAM_LEAD_PROFILE`）只给 `delegate_to_agent`
  工具，不给 tell_user——结构性防止 TeamLead 直接插嘴
- 双工具的 description + enum 都从 AgentRegistry 动态填充——加新
  Agent 无需改工具 schema
