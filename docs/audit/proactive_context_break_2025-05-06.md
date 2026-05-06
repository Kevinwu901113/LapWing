# Proactive 消息上下文断裂问题分析

**日期**: 2026-05-06
**发现来源**: Kevin 观察到的 LapWing QQ 对话异常
**严重程度**: 中高 — 导致 AI 在用户回复 proactive 消息后完全失忆

---

## 1. 现象

```
LapWing (15:34:42): 下午好～第二个盲审有消息了吗？
awwaw   (15:35:18): 还没
LapWing (15:35:38): 什么还没？
```

LapWing 通过 proactive 通道主动发送了一条关于"第二个盲审"的询问。用户在 36 秒后回复"还没"，但 LapWing 反问"什么还没？"——完全忘记了自己刚问过什么。

---

## 2. 根因

### 两条独立的数据路径

LapWing 有两个完全隔离的消息循环：

| 维度 | Inner Tick（主动消息） | Chat（被动回复） |
|------|----------------------|-----------------|
| 入口 | `brain.think_inner()` | `brain.think_conversational()` |
| 轨迹类型 | `INNER_THOUGHT` | `CHAT_MESSAGE` |
| source_chat_id | `NULL` | 真实 chat_id |
| 上下文构建 | `build_for_inner()` | `build_for_chat()` |
| 历史加载 | 仅当前 inner prompt | `include_inner=False` |

### 关键代码路径

**send_message 只做投递，不做轨迹记录** (`src/tools/personal_tools.py:187-249`):

```python
async def _send_message(req, ctx):
    target = req.arguments.get("target")
    content = req.arguments.get("content")
    # ... gate checks ...
    # 消息通过 channel manager 发出
    # ❌ 没有任何代码把这条消息写入 target chat 的 trajectory
    # ❌ 没有任何代码建立 "proactive → chat" 的关联
```

**聊天上下文加载时显式排除 inner thought** (`src/core/state_view_builder.py:431-432`):

```python
entries = await self._trajectory.relevant_to_chat(
    chat_id, n=self._history_turns * 2, include_inner=False,  # ← 不加载 INNER_THOUGHT
)
```

**inner thought 不入聊天轨迹** (`src/core/brain.py:987-991`，代码自带注释):

```python
# Inner rows land with source_chat_id=NULL, so _load_history
# (include_inner=False) would return []. Build the recent list
# directly from inner_prompt — past inner thoughts aren't replayed
# into the message window; StateView surfaces runtime state.
recent = [{"role": "user", "content": inner_prompt}]
```

### 链条

1. Inner tick 触发 → LLM 决定发 proactive 消息
2. 消息内容 `"下午好～第二个盲审有消息了吗？"` 通过 QQ 发出
3. 该消息的内容、上下文、以及"为什么要发这条消息"的推理都**仅存在于 inner thought 的 reply 中**
4. Inner thought 的 reply 写入 trajectory：`entry_type=INNER_THOUGHT, source_chat_id=NULL`
5. 用户回复 → 进入 `think_conversational()`
6. `_load_history()` / `_build_trajectory_for_chat()` 用 `include_inner=False` 加载历史 → **inner thought 被跳过**
7. `StateView.build_for_chat()` 不包含任何关于 "我刚发了 proactive 消息 X" 的信息
8. 记忆检索 (`WorkingSet.retrieve()`) 基于 chat trajectory 做语义搜索 → 可能检索不到"第二个盲审"
9. 模型看到孤立的消息 `"还没"` → 不知道在说什么 → 回复 `"什么还没？"`

---

## 3. 同样受影响的其他场景

这个问题不仅限于 `think_inner` 路径。`compose_proactive()` (`brain.py:1244`) 虽然目前无调用方，但设计上同样存在此问题——它也是自主 outbound 路径，发出的消息不会被后续聊天上下文感知。

---

## 4. 影响范围

- **所有通过 `send_message` 发出的 proactive 消息**，如果用户在短时间内回复，都会出现上下文断裂
- 用户回复越快，断裂越明显（记忆检索来不及通过 scratch_pad 等间接路径覆盖到该话题）
- 如果 proactive 消息的话题恰好出现在 scratch_pad.md 或其他 StateView 上下文（commitments、reminders 等），模型可能"碰巧"知道在说什么——但这是不可靠的

---

## 5. 修复方向（未实施）

### A. 最小修复 — 轨迹回写
`send_message` 成功后，把发出的消息内容写入 target chat 的 trajectory（如 `PROACTIVE_OUTBOUND` 类型），在 `_load_history` / `_build_trajectory_for_chat` 中加载为系统备注。至少让模型知道"我刚才发了什么"。

**改动文件**: `personal_tools.py` (send_message), `trajectory_store.py` (新增 entry type 或复用), `state_view_builder.py` (加载逻辑)

### B. 中等修复 — 带推理上下文的轨迹回写
在 A 的基础上，同时注入发送时的 inner thought 摘要（为什么要发这条消息），让模型在回复时不仅知道自己发了什么，还知道为什么发。

### C. 更完整修复 — chat 上下文中的 proactive 感知
在 `build_for_chat()` 中检索目标用户最近 N 分钟内的 proactive outbound 消息，注入到 attention context 或 system note 中。不依赖 trajectory 回写，改为查询 send_message 的 mutation log。

---

## 6. 相关提交

| Commit | 说明 |
|--------|------|
| `a4e2841` | 引入 send_proactive_message 工具 |
| `0576792` | 添加 rate-limit + quiet-hours gate |
| `51b8756` | P2 修复：拦截无检索强事实声明 |
| `7bfbe4b` | 重构：profiles 集中管理 tool surface |

注：以上提交均未涉及"发出消息后的上下文回写"问题。

---

## 7. 附录：完整的数据流图

```
┌─────────────────────────────────────────────────────┐
│                    INNER TICK                        │
│                                                     │
│  build_inner_prompt()                               │
│       │                                             │
│       ▼                                             │
│  think_inner()                                      │
│       │                                             │
│       ▼                                             │
│  LLM tool loop (INNER_TICK_PROFILE)                 │
│       │                                             │
│       ├── send_message("下午好～第二个盲审...")       │
│       │        │                                    │
│       │        ▼                                    │
│       │   QQ adapter 投递 ───► 用户收到消息           │
│       │        │                                    │
│       │        ▼                                    │
│       │   ❌ 轨迹不回写   ←── 问题在这里              │
│       │                                             │
│       └── 其他工具调用 (notes, reminders...)          │
│       │                                             │
│       ▼                                             │
│  _record_turn(INNER_THOUGHT, source_chat_id=NULL)   │
│       │                                             │
│       ▼                                             │
│  仅 inner thought 可见 —— chat 路径不可达             │
└─────────────────────────────────────────────────────┘

                      ═══════════ 时间线 ═══════════

┌─────────────────────────────────────────────────────┐
│                    USER REPLIES                      │
│                                                     │
│  QQ adapter 收到 "还没"                               │
│       │                                             │
│       ▼                                             │
│  think_conversational(chat_id, "还没")               │
│       │                                             │
│       ▼                                             │
│  _load_history(include_inner=False)                  │
│       │                                             │
│       ▼                                             │
│  ❌ INNER_THOUGHT 被跳过                             │
│  ❌ proactive 消息内容不可见                          │
│  ❌ 模型只看到孤立的 "还没"                           │
│       │                                             │
│       ▼                                             │
│  LLM: "什么还没？"  ← 上下文断裂                      │
└─────────────────────────────────────────────────────┘
```
