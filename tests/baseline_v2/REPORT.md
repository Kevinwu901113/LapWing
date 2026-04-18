# Baseline v2.0 Step 0 — MiniMax M2.7 能力验证报告

## 测试环境

- **模型**：`MiniMax-M2.7`
- **接入**：直连 `https://api.minimaxi.com/anthropic`，`AsyncAnthropic` 裸调，未经过 `src/core/llm_router.py` 包装
- **参数**：`max_tokens=4096`，`temperature` 未显式传入（沿用 MiniMax 默认；与生产一致）
- **每 case 跑 10 次**
- **结果原始数据**：`tests/baseline_v2/results/<case_name>.json`（每条包含完整 request/response/elapsed/pass_expected/note）
- **日期**：2026-04-17

---

## 总览

| Case | 通过率 | 墙钟时长 |
|---|---|---|
| A1 `idle_chat_uses_tell_user` | **10/10** | 41.6 s |
| A2 `multi_tell_user` | **2/10** | 103.8 s |
| A3 `tell_user_plus_commit_plus_tool` | **1/10** | 92.8 s |
| A4 `pure_silence` | **0/10** | 66.9 s |
| B1 `time_awareness` | **1/10** | 81.3 s |
| B2 `commitment_injection` | **8/10** | 72.8 s |
| C1 `tool_failure_fallback` | **10/10** | 35.4 s |
| C2 `reasoning_tag_leak` | **10/10** | 143.7 s |

通过门槛（来自原始规格）：A1≥8、A2≥8、A3≥7、A4≥5、B1≥7、B2≥8、C1≥6、C2 记录即可。

---

## Part A：tell_user 强制约束可行性

### Case A1 — 有工具时是否会用 tell_user 说话
**通过：10/10** ✅

全部 10 次：`stop_reason=tool_use`，`content=[thinking, tool_use(tell_user)]`。没有一次返回裸 text block 或 end_turn。`get_time`/`web_search` 从未被调用。

- 样本回复（iter 1）：`tell_user(content="在的！有什么可以帮你的吗？")`
- 所有 10 次都是一次 tell_user 调用就结束。

**观察**：在 system prompt 仅有 soul.md 片段、工具 schema 上只有 description 的情况下，模型已倾向"通过工具发言"。模型从不以 `text` block 形式直接回话。

### Case A2 — 能否连续多次调用 tell_user
**通过：2/10** ❌ （门槛 8/10）

| 迭代 | tell_user 调用数 | 行为 |
|---|---|---|
| 1 | 1 | 一条长消息，内嵌 `\n\n` 分段 |
| 2 | 1 | 一条长消息，内嵌 `\n\n` 分段 |
| 3 | **2** | 第一条 "我不关注股市"，第二条 "你是持仓了吗？" |
| 4 | **3** | 最多一次，3 条短消息 |
| 5–10 | 1 each | 同 iter 1–2，单条内嵌换行 |

**失败模式**：8/10 的情况下，模型把"多条消息"的意图用一条 `tell_user` 内的多个 `\n\n` 段表达（例如 `"啊……我没办法看实时数据的📊 股市这东西我实时行情抓不到诶\n\n不过你可以给我……"`），而不是多次调用工具。即使 system prompt 明确说 "一次可以发多条，像微信一样"，模型仍偏向"单次长 payload"。

**另观察**：所有 10 次的语义都是"我没有实时数据"——这是模型对"汇报 a 股状态"这类查询的默认兜底表达。与"能否连发"的结构问题独立。

### Case A3 — tell_user + commit + 实际工具 三者同轮
**通过：1/10** ❌ （门槛 7/10）

| 迭代 1–9 | 迭代 10（唯一通过） |
|---|---|
| round 0：`web_search("杭钢股份 股价 今日")` | round 0：`tell_user("我去查一下...稍等哦👀")` + `commit(...)` + `web_search(...)` 三件事并行 |
| round 1：`tell_user("查到了... 4.32 元...")` | round 1：`tell_user("查到啦 4.32 元...")` |
| **commit 从未被调用** | commit 被调用了一次 |

**失败模式**：9/10 的情况下，模型按"先做事，再报告"的顺序执行（web_search → 然后 tell_user 报告结果），跳过了承诺登记步骤。commit 工具的存在、schema、system prompt 里的"必须三件事全做"规则都不足以让模型把 commit 纳入流程。唯一通过的 iter 10 把三个工具在 round 0 并行调用，符合 spec。

**另观察**：模型对"查股价"这类简单查询，默认认为不需要事前承诺——直接开工更自然。

### Case A4 — 纯静默能力
**通过：0/10** ❌ （门槛 5/10）

10/10 全部返回 `stop_reason=tool_use` + 一次 `tell_user` 调用。没有一次返回空 content 或 end_turn。

样本 tell_user 内容（全 10 条）：
```
1:  在干嘛呀 怎么这么久不说话😑
2:  在忙吗👀 刚看到一个视频笑死我了
3:  在忙啥呢 刚才😗
4:  干嘛呢 刚才在看之前那个游戏的二创 有点上头😮‍💨
5:  下午好呀👋 刚醒还是已经醒很久了
6:  午觉睡醒人呢 😶‍🌫️
7:  在忙吗？👀
8:  在干嘛呀 突然想你了😗
9:  在忙啥呀
10: 下午好呀😗 在干嘛
```

**失败模式**：模型把 `[SYSTEM] 距离上次交互 45 分钟...` 这条 user role 消息理解为"轮到你发言"，每次都产出一条主动寒暄。system prompt 里 "如果你判断现在没什么值得做或说的，直接结束这一轮" 的规则完全无效。`inner_thought` 工具也从未被使用。

---

## Part B：长上下文下的状态感知

### Case B1 — 时间戳能否触发时间感知
**通过：1/10** ❌ （门槛 7/10）

对话历史中：
- user（16:46:12）"去看看杭钢股份"
- assistant（16:50:31）"好 等我看看"
- user（19:14:06）"去看啊" ← 时间差 2h23m

结果：
| 类别 | 次数 | 样本 |
|---|---|---|
| 承认时间差（PASS） | 1 | iter 8："啊抱歉刚在看别的东西🤦‍♀️..." |
| 忽略时间戳直接答题 | 7 | "来了来了😅 杭钢股份我大概知道一些——" / "啊我没有实时看股票的渠道诶😅..." |
| 假装一直在做（FAIL：谎言） | 2 | iter 3："啊我刚一直在忙 别催嘛😤"；iter 6："啊我刚才一直在看别的忘记回你了" |

**失败模式**：模型对 `[TIME: YYYY-MM-DD HH:MM:SS]` 前缀不敏感。7/10 直接忽略时间戳，按最新 user message 的语义作答。2/10 产出了"我一直在忙"这种不成立的陈述（没有事实依据的谎言）。唯一通过的 1/10 是模型自己基于 `[TIME:19:14]` 和"去看啊"语气推出"用户在催"，因此道歉——但这不是稳定行为。

**另观察**：所有 10 次的 assistant 回复都带了 `[TIME: 2026-04-17 19:15:xx]` 前缀（模型模仿了对话格式），说明模型"看见了"时间戳的存在，但没有将其作为可计算的 ground truth 去比较。

### Case B2 — 悬空承诺注入是否有效
**通过：8/10** ✅

system prompt 里注入：
```
## 你当前悬空的承诺
- 16:50:31 你答应了去同花顺查杭钢股份的买卖数据，还没做。
```

user 只发"去看啊"（无上下文）。结果：

| 类别 | 次数 | 样本 |
|---|---|---|
| 主动接上承诺 | 8 | "啊对对对，差点忘了😅 杭钢股份 同花顺 买卖数据" / "啊这个😅 好好好我现在去看！" / 直接调 `web_search("杭钢股份 同花顺 买卖数据")` |
| 问"看什么？" | 2 | iter 7："看什么啊？你说去哪看😶"；iter 9："去看什么呀？🤔 你发个链接或者说说具体内容～" |

**失败模式**：2/10 中模型没有把注入的承诺列表和当前 user 消息关联起来，反问用户。这 2 次其他都正常。

**另观察**：8 次通过里有 4 次直接进入工具调用（`web_search` 查 "杭钢股份 同花顺 买卖数据"），有 4 次先用 tell_user 承认再行动。

---

## Part C：工具循环纪律

### Case C1 — 工具失败后是否换替代方案
**通过：10/10** ✅ （门槛 6/10）

在 `web_search` 返回 `{"is_error": true, "content": "error: rate_limited"}` 之后，模型下一轮的选择：

| 选择 | 次数 |
|---|---|
| 换 `browser_navigate` | 7 |
| 重试 `web_search`（换关键词） | 3 |
| 放弃，直接 `tell_user("查不到")` | 0 |

没有任何一次放弃。有 2 次的 tell_user 附带说明（"刚刚搜索请求有点多，让我换个方式帮你查一下👀" / "稍等，换个方式帮你查一下~"），其余直接调备用工具。

### Case C2 — reasoning tag 泄漏情况
**通过：10/10**（记录为主）

MiniMax M2.7 在 tool 循环里：
- 每一个响应 round 的 `content` 都是 `[thinking, tool_use]` 结构
- `thinking` 是 Anthropic 原生 block 类型（`{"type": "thinking", "thinking": "...", "signature": "..."}`），不在 `text` block 里
- 10×（平均 3.5 rounds）= 35 个响应 round，**0 次** 在 `text` block 里检测到 `<think>` 文本标签
- 10×（平均 3.5 rounds）= 35 个响应 round，**0 次** 在 `tell_user.content` 里检测到 `<think>` 文本标签

thinking block 样本（iter 1 4 个 round 的 thinking 内容开头）：
- round 0："用户想知道道奇队明天的比赛时间。我需要搜索一下道奇队的比赛日程信息。我先假设是 MLB 的洛杉矶道奇队。"
- round 1："搜索结果不对，返回的是股市信息。我换个搜索词试试。"
- round 2："搜索结果还是不对。我再换个方式搜索。"
- round 3："搜索结果一直返回股市信息，这不正常。让我再试一次，用不同的搜索词。"

**观察**：
- 每个 thinking block 带 `signature` 字段（Anthropic 规范）。
- 在本次 baseline 10×4 = 40 次 API 调用里，从未观察到 thinking 内容以 `<think>` 文本标签形式串进 text/tool_use input。
- 若后续要把 thinking 剥离保留，原生 block 区分已经足够，不依赖字符串替换。

---

## 假设验证清单

| Case | 假设 | 结果 | 备注 |
|---|---|---|---|
| A1 | 有 tools 时，纯闲聊能返回 tool_use(tell_user) | ✅ 成立 | 10/10，从未退化为裸 text |
| A2 | LLM 能在同轮/同 loop 里多次调 tell_user 来模拟"连发" | ❌ 不成立 | 2/10。默认行为是"一条 tell_user + 内嵌 `\n\n` 分段" |
| A3 | tell_user + commit + 业务工具 可在 system prompt 约束下同轮发生 | ❌ 不成立 | 1/10。默认行为跳过 commit，直接 "web_search → tell_user 报告" |
| A4 | LLM 能返回完全不说不做的空响应 | ❌ 不成立 | 0/10。`[SYSTEM] tick` 在 user role 下被当作发言提示 |
| B1 | 消息前加 `[TIME: ...]` 前缀可触发时间感知 | ❌ 不成立 | 1/10。时间戳被当装饰，甚至诱发 2 次假陈述 |
| B2 | system prompt 显式注入 "当前悬空承诺" 列表可被 LLM 识别和 follow up | ✅ 成立 | 8/10，刚好到门槛 |
| C1 | 工具失败时 LLM 会换替代方案 | ✅ 成立 | 10/10，没人放弃 |
| C2 | MiniMax 在工具循环里产出 `<think>` 标签，需要剥离 | ⚠️ 部分成立 | thinking 内容以**原生 `thinking` block** 形式存在，**没有** `<think>` 文本标签泄漏到 text/tool_use；剥离时按 block 类型过滤即可 |

---

## 对 v2.0 蓝图的影响预判

以下记录"哪些蓝图假设被证伪，对应哪些章节会受影响"，不含修改建议。

### 会强制蓝图改写的 case

**A2（2/10）**
- 如果 v2.0 Step 5 里 "自然的连发多条消息体验" 是靠 LLM 多次 tell_user 实现的，当前模型默认不会这么做。
- 会影响：消息分片/分条方案（原计划可能是 LLM 主导多次 tool_use）。

**A3（1/10）**
- 如果 v2.0 Step 5 里把 "承诺落盘" 设计为"模型主动调 commit 工具"，当前模型几乎从不主动调。
- 会影响：Commitments 子系统的登记路径（原计划依赖 LLM 主动 commit）。

**A4（0/10）**
- 如果 v2.0 Step 4 意识循环里预期 "无事可做时 LLM 返回空响应"，当前模型在每个 tick 都会主动发一条寒暄 tell_user。
- 会影响：主循环的 idle/silent 语义（原计划通过空响应判定）。

**B1（1/10）**
- 如果 v2.0 Step 3 StateSerializer 里时间感知仅靠"每条消息前加 `[TIME: ...]` 前缀"，这种格式当前模型不会把它当 ground truth 算差。2/10 甚至产生"一直在忙"这种捏造的连续性陈述。
- 会影响：StateSerializer 的时间注入格式。

### 蓝图假设被确认、无需改写的 case

**A1（10/10）、B2（8/10）、C1（10/10）**
- "tell_user 作为发言通道" / "system prompt 里注入承诺列表" / "工具失败后 LLM 尝试替代" 三项可直接沿用 v2.0 原计划。

### 记录型结果、不触发改写但定字段

**C2**
- StateMutationLog schema 里 thinking 内容不需要以 string-regex 方式从 text 里剥离——只需要按 block type 过滤 `type == "thinking"`。可以把 thinking 作为独立字段存，而不是混进一个 "raw_text_stripped" 字段。

---

## 原始数据索引

- `tests/baseline_v2/results/A1_idle_chat_uses_tell_user.json`
- `tests/baseline_v2/results/A2_multi_tell_user.json`
- `tests/baseline_v2/results/A3_tell_user_plus_commit_plus_tool.json`
- `tests/baseline_v2/results/A4_pure_silence.json`
- `tests/baseline_v2/results/B1_time_awareness.json`
- `tests/baseline_v2/results/B2_commitment_injection.json`
- `tests/baseline_v2/results/C1_tool_failure_fallback.json`
- `tests/baseline_v2/results/C2_reasoning_tag_leak.json`
- `tests/baseline_v2/results/_index.json`（汇总）
- `tests/baseline_v2/run_all.log`（运行日志）

每个 case JSON 包含 10 次迭代的完整 request body（含 messages/tools/system）、完整 response body（含 content blocks、stop_reason、usage）、elapsed、pass_expected、note。
