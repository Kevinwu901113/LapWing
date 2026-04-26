# Step 6 — Delegation 复用判定

**背景：** 原 Step 6 spec 假设需要从零构建 Agent Team（依赖已不存在的
`src/core/delegation.py` 345 行 + `src/tools/delegation_tool.py` 94 行）。
经实地排查：Phase 6（`2026-04-16-agent-team-phase6.md`）已在 master
完成，与 spec 描述的"旧 delegation"不是同一时代物——老框架早在 Phase 6
时就被删除替换。Step 6 的真实工作面是"把 Phase 6 产出对齐 Refactor v2
架构（StateMutationLog / RuntimeProfile / tell_user 唯一性）"。

## 选项评估

| 选项 | 动作 | 风险 | 收益 |
|------|------|------|------|
| A. 重写，对齐 spec 原意 | 删除 `src/agents/*`、按 spec 重建 | 大：21 天前刚合入，未有实战反馈的代码再推倒 | 跟 spec 1:1 |
| B. 保留 Phase 6 框架 + 对齐观测/能力 | 在 `src/agents/*` 上增量改 | 低：变更集中在 4 类关键接线点 | 架构一致，历史设计沉淀保留 |
| C. 保留不改 | 不动 | 中：Desktop SSE 拿不到 agent 事件、tell_user 没有结构性护栏 | 零工作量，但 debt 继续积累 |

## 结论：选 B

Phase 6 设计的领域建模（三类 Agent、Team Lead 编排、双工具分层、工作区
沙箱）本身没有错，错的只是它的观测源头（Dispatcher）已经不是 Refactor
v2 架构里的真值源（StateMutationLog），并且能力限制没切到
RuntimeProfile——这两个点都是局部可替换的，不需要整体重写。

## 实际保留 / 改动清单

| 模块 | 决定 | 说明 |
|------|------|------|
| `src/agents/types.py` | 保留 + 扩展 | 新增 `runtime_profile` 字段，`tools` 降为 legacy fallback |
| `src/agents/base.py` | 重写但保留骨架 | 构造签名 `dispatcher` → `mutation_log`；_get_tools 走 profile；新增 4 个 mutation 埋点 |
| `src/agents/registry.py` | 完全保留 | 不涉及观测或能力 |
| `src/agents/researcher.py` | 保留 prompt，改 .create() | profile=AGENT_RESEARCHER_PROFILE；prompt 加一句"没有 tell_user 权限" |
| `src/agents/coder.py` | 保留 prompt，改 .create() | 同上 |
| `src/agents/team_lead.py` | 保留（见 `step6_teamlead_design.md`）| profile=AGENT_TEAM_LEAD_PROFILE |
| `src/tools/agent_tools.py` | 保留结构，改实现 | 删除 4 处 dispatcher.submit；description 从 AgentRegistry 动态填充；enum 动态 |
| `src/tools/workspace_tools.py` | 完全保留 | 沙箱逻辑无需改动 |
| `src/app/container.py` | 微调 | 传 `self.mutation_log` 取代 `self.dispatcher`；`register_agent_tools` 移到 Agent 注册之后以激活动态填充 |

## 删除的 Dispatcher 事件 emit 点（5 处）

| 位置 | 事件类型 | 替代 |
|------|----------|------|
| `base.py:40` | `agent.task_started` | `AGENT_STARTED` via mutation_log |
| `base.py:102` | `agent.tool_called` | `AGENT_TOOL_CALL` via mutation_log |
| `agent_tools.py:43` | `agent.task_created` | 不再重复 emit——BaseAgent 已覆盖生命周期 |
| `agent_tools.py:61` | `agent.task_{done,failed}` | 不再重复 emit |
| `agent_tools.py:116` | `agent.task_assigned` | 不再重复 emit |
| `agent_tools.py:134` | `agent.task_{done,failed}` | 不再重复 emit |

事件字符串值（`agent.task_started` 等）保留——Desktop v2 的
`useSSEv2.ts` + `types/events.ts` 直接认这些字符串，保持字符串语义让
前端零修改就接上新源（mutation_log 取代 dispatcher）。

## 没动的老 Dispatcher 订阅者

`src/core/dispatcher.py` 的 `Event` / `submit` / `subscribe` / `subscribe_all`
都在原位——其它子系统（consciousness、reminder）还在用 Dispatcher 做
实时广播；只有 `agent.*` 这条链路迁到了 mutation_log。Dispatcher 整体
退场是 Step 7+ 的 debt。

## 回滚路径

若发现缺陷：`git revert bcf2845`（Step 6 对齐 commit）可干净回退到
Phase 6 原状，`src/agents/*` 已归档到
`~/lapwing-backups/pre_step6_20260419_151734/agents.bak/`。
