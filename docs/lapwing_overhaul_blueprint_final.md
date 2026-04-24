# Lapwing 全面改造执行 Blueprint（精确版）

**执行者**: Claude Code
**代码基线**: 34,539 行 Python，1,383 tests
**目标**: 修复 8 个系统层根因，参照 OpenClaw/Hermes/Claude Code/Codex 的行业最佳实践

---

## 背景文件（Claude Code 必读）

在开始修改前，请阅读以下 3 份分析报告（位于 `docs/` 或项目根目录）：
1. **问题诊断报告** — 从 3274 个对话事件中提取的具体问题清单（P0-P3 分级）
2. **系统层根因分析** — 8 个根因的详细分析，假设模型无问题
3. **Agent 调研报告** — OpenClaw/Hermes/Claude Code/Codex 四个系统的架构对比

核心发现：**Lapwing 的 tell_user 机制和意识系统的开放式 prompt 在行业中没有任何先例，四个主流 agent 无一采用类似方案。**

---

## 执行顺序（有依赖关系，不可打乱）

```
Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6 → Step 7 → Step 8
```

---

## Step 1: 废除 tell_user，采用"直接输出 = 说话"

**这是最核心的改动。** 四个主流 agent 全部采用"模型纯文本输出 = 用户可见消息"。Lapwing 是唯一把说话做成工具调用的系统，这导致模型经常"忘了说话"。

### 1.1 当前机制（需要反转）

**当前流程**（定义于 `src/core/brain.py:749-901`）:
```
用户消息 → brain.think_conversational()
  → _complete_chat() → task_runtime.complete_chat()
    → 工具循环：模型每轮输出 text + tool_calls
      → text → on_inner_monologue() → 写入 trajectory 作为 INNER_THOUGHT（用户看不到）
      → tool_call(tell_user) → tell_user_executor → send_fn(text)（用户看到）
      → 其他 tool_call → 执行 → 结果追加 → 继续循环
  → 循环结束后，brain 从 tell_user_buffer 拼接 memory_text
```

关键注释在 `brain.py:49-52`:
> "裸文本结构性地视为内心独白...真正要说的话必须通过 tell_user 工具——契约取代过滤"

**这个契约需要反转为**: 裸文本 = 说话，tool_call = 内部操作。

### 1.2 需要修改的文件

#### A. `src/core/brain.py` — think_conversational() 方法（行 749-901）

**改动要点**:

1. **移除 `tell_user_buffer`**（行 799）— 不再需要
2. **反转 `on_inner_monologue` 的语义**（行 801-824）— 模型裸文本不再是内心独白，而是用户可见的回复
3. **移除行 850-855 的"裸文本永远不发给用户"逻辑** — 改为裸文本通过 send_fn 发送
4. **修改行 857-871 的后处理** — memory_text 改为从 send_fn 发送的文本中收集

**新的 think_conversational 核心流程**:
```python
# 收集本轮所有发送给用户的文本
spoken_parts: list[str] = []

async def on_model_text(text: str, **_kw) -> None:
    """模型裸文本 → 直接发送给用户。"""
    stripped = strip_internal_thinking_tags(text).strip()
    if not stripped:
        return
    # 按双换行分割为多条消息
    segments = [s.strip() for s in stripped.split("\n\n") if s.strip()]
    for segment in segments:
        segment = sanitize_outgoing(segment)
        if segment:
            await send_fn(segment)
            spoken_parts.append(segment)

# _complete_chat 中，on_interim_text 现在是发送给用户的回调
full_reply = await self._complete_chat(
    ...,
    on_interim_text=on_model_text,  # 裸文本 → 发给用户
    ...
    # 不再传 tell_user_buffer
)

# 最后一轮的裸文本也需要发送
tail = strip_internal_thinking_tags(full_reply).strip()
if tail:
    tail = sanitize_outgoing(tail)
    if tail:
        segments = [s.strip() for s in tail.split("\n\n") if s.strip()]
        for segment in segments:
            if segment:
                await send_fn(segment)
                spoken_parts.append(segment)

# 后处理：记录"她真正说出口的话"
memory_text = "\n\n".join(spoken_parts) if spoken_parts else ""
if memory_text:
    await self._record_turn(chat_id, "assistant", memory_text)
```

#### B. `src/core/task_runtime.py` — 工具循环中的文本处理（行 744-799）

**改动要点**:

1. **移除行 748-768 的 `missing_tell_user_retries` 逻辑** — 不再需要提醒模型调 tell_user
2. **移除行 804 的"默认静默"注释和 `<user_visible>` 标签提取逻辑**（行 806-813）— 模型在工具调用之间产生的文本，仍然通过 on_interim_text 发送给用户
3. 行 783-789 的 `on_interim_text` 调用保留 — 但语义从"内心独白"变为"发送给用户"

**关键改动**: `ToolLoopContext` 中移除 `missing_tell_user_retries` 字段。

#### C. `src/tools/tell_user.py` — 整个文件

**保留但改变语义**: tell_user 工具不再是"唯一说话方式"，而是变成 `send_message` 工具 — 用于意识 tick 或 agent 需要主动给用户发消息的场景（这些场景没有 send_fn）。

重命名为 `src/tools/send_message.py`，修改描述：
```python
SEND_MESSAGE_DESCRIPTION = (
    "主动给 Kevin 发一条消息。仅在你不在对话上下文中时使用（如内部思考、意识活动）。"
    "在正常对话中不需要调用此工具——你直接说话就行了。"
)
```

#### D. `src/tools/registry.py` — 工具注册（行 169-180）

**改动**:
- 将 `tell_user` 重命名为 `send_message`
- 修改描述为"仅用于主动消息场景"
- 移除"模型唯一对外说话的路径"的注释

#### E. `prompts/lapwing_soul.md` 和 `src/core/state_serializer.py`

**`state_serializer.py` 行 42-48 的 `_PERSONA_ANCHOR`**:
```python
# 当前：
_PERSONA_ANCHOR = (
    "记住：你是 Lapwing，说话像发微信，短句为主。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，做事时保持人格，不切换成工具模式。"
    "用过工具查到的信息你就是知道了——不要装作不确定。搜索过程不发出来。"
    "【必须】回复超过两句话时用 [SPLIT] 分条发送，不要用换行符\\n代替。不分条是违规的。"
)

# 改为：
_PERSONA_ANCHOR = (
    "记住：你是 Lapwing，说话像发微信，短句为主。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，做事时保持人格，不切换成工具模式。"
    "用过工具查到的信息你就是知道了——不要装作不确定。搜索过程不发出来。"
    "你说的每一句话 Kevin 都能直接看到。想分多条消息就用空行隔开。"
)
```

#### F. `src/tools/commitments.py` — 承诺跟踪

更新所有引用 `tell_user` 的地方为新的语义。承诺系统的核心逻辑（"你承诺了什么"）保留，但不再绑定 tell_user 工具调用。

#### G. `src/core/output_sanitizer.py`

保留但简化。tell_user 废除后，sanitize_outgoing 仍然作为安全网运行在 send_fn 之前。

### 1.3 验证标准

- [ ] `tell_user` 不再作为工具名存在（改名为 `send_message` 且仅用于主动消息）
- [ ] 模型输出纯文本后，用户在 QQ 上立即收到消息
- [ ] 模型执行工具调用时，用户看不到工具调用细节
- [ ] 所有现有测试中引用 `tell_user` 的地方更新为新语义
- [ ] `pytest tests/ -x -q` 全部通过

---

## Step 2: 输出管道清理

Step 1 完成后，text/tool 的分离在架构层实现。清理残留：

1. 移除 `src/core/reasoning_tags.py` 中的 `strip_split_markers` — [SPLIT] 不再使用
2. `_extract_user_visible()` 函数（`task_runtime.py` 中）— 不再需要 `<user_visible>` 标签机制
3. `output_sanitizer.py` — 保留 `<think>` 清理和基本安全过滤，移除 `[SPLIT]`、`[ENTER]`、`<user_visible>` 的 pattern
4. 添加异常输出检测：text > 2000 字符时截断 + 记录 warning

---

## Step 3: System Prompt 精简

### 3.1 当前 Prompt 组装流程

`StateViewBuilder.build_for_chat()` → `StateView` → `state_serializer.serialize()`:
1. Layer 1: `identity_docs.soul`（`data/identity/soul.md`，回退 `prompts/lapwing_soul.md`）
2. Layer 2: `identity_docs.constitution`（`data/identity/constitution.md`）
3. Layer 3: runtime state（时间、通道、到期提醒、活跃任务）
4. Layer 4: memory snippets（检索结果）
5. Voice: depth-injected（`prompts/lapwing_voice.md`，310 行）

### 3.2 改动

1. **精简 `prompts/lapwing_voice.md`**（310 行 → ~100 行）— 保留 ✕/✓ 对比的核心部分，删除重复和低权重的规则
2. **创建 `data/identity/constitution.md`** — 从对话历史中提取的高频违反规则（见附录）
3. **工具定义按需加载** — `StateViewBuilder` 或 `brain._complete_chat()` 中，根据场景（chat vs inner）加载不同工具集
4. **强制加载最近 5 条 correction** — `StateViewBuilder.build_for_chat()` 中，在 Layer 4 追加

### 3.3 新的 constitution.md 初始内容

```markdown
# 宪法级规则（不可违反）

1. 不要去 Reddit 找内容分享——逛国内网站（小红书、知乎、微博）
2. 不要自己算时区——使用 convert_timezone 工具
3. 查到结果要立即告诉 Kevin——不能只搜不说
4. 确认信息正确再说——不确定的事情要说"我不确定"
5. 不要编造经历——没做过的事不要说做过
6. 叫他 kuan——不要叫 Kevin
7. 说话不要加句号——口语化，自然
8. 不要反复发相同话题的内容——24小时内不重复
```

---

## Step 4: 意识系统重写（Heartbeat 模式）

### 4.1 修改 `src/core/inner_tick_scheduler.py`

#### `build_inner_prompt()` 重写（行 59-129）

当前 prompt 的问题：
- 行 77: "你可以做任何你觉得应该做的事，**或者什么都不做**" — 给了不做事的许可
- 行 82: "如果没有需要做的事，回复\"无事\"即可" — 鼓励空转
- 行 107-117: 10+ 个开放式建议 — 太多选项导致什么都不选

**新 prompt**:
```python
def build_inner_prompt(urgent_items: list[dict] | None = None) -> str:
    from src.core.time_utils import now as _now
    now = _now()
    now_str = now.strftime("%Y-%m-%d %H:%M %A")
    
    parts = [
        f"[Heartbeat — {now_str}]",
        "",
        "这是定期检查。请逐项完成以下清单：",
        "",
    ]
    
    if urgent_items:
        parts.append("## ⚡ 紧急事件（最优先）")
        for item in urgent_items:
            parts.append(f"- [{item.get('type', 'unknown')}] {item.get('content', '')}")
        parts.append("")
    
    # 读取 heartbeat checklist
    heartbeat_path = DATA_DIR / "consciousness" / "heartbeat.md"
    if heartbeat_path.exists():
        try:
            checklist = heartbeat_path.read_text(encoding="utf-8").strip()
            if checklist:
                parts.append(checklist)
                parts.append("")
        except Exception:
            pass
    else:
        # 默认 checklist
        parts.append("## 检查项")
        parts.append("1. Kevin 上次联系是什么时候？需要主动问候吗？")
        parts.append("2. 有没有到期的提醒或承诺？")
        parts.append("3. 有什么值得分享给 Kevin 的吗？（不要发 Reddit 内容）")
        parts.append("")
    
    parts.append("## 规则")
    parts.append("- 如果所有检查项都不需要行动，回复 HEARTBEAT_OK")
    parts.append("- 想给 Kevin 发消息就调用 send_message 工具")
    parts.append("- 每次最多发 1 条主动消息")
    parts.append("- 不要重复发相同话题（24小时内）")
    
    return "\n".join(parts)
```

#### `is_inner_did_nothing()` 更新（行 54-56）

```python
_INNER_NO_OP_RESPONSES = frozenset({
    "无事", "无事。", "无事，", "nothing",
    "HEARTBEAT_OK", "heartbeat_ok",
})
```

### 4.2 添加 activeHours 检查

在 `InnerTickScheduler._run()` 的循环中（行 281-316），添加时间窗检查：

```python
from src.core.time_utils import now as _now

# 在 await self._queue.put(InnerTickEvent.make(...)) 之前：
current_hour = _now().hour
if current_hour >= 23 or current_hour < 8:
    # 深夜不触发（除非有紧急事件）
    if self._urgency_queue.empty():
        logger.debug("activeHours: skipping tick (hour=%d)", current_hour)
        continue
```

### 4.3 添加工具调用预算

在 `brain.think_inner()` 中（行 651-747），通过 `RuntimeProfile` 或 `services` 传递预算限制。

在 `config/settings.py` 中添加：
```python
HEARTBEAT_TOOL_BUDGET: int = _s.consciousness.get("tool_budget", 5)
```

### 4.4 创建 `data/consciousness/heartbeat.md`

```markdown
## 检查项

1. Kevin 上次联系是什么时候？超过 6 小时就主动问候
2. 有没有到期的提醒或承诺？（用 check_reminders 工具）
3. working memory 中有没有待处理的事？
4. 有什么想跟 Kevin 分享的？（不要发 Reddit 内容，找国内网站）

## 如果都不需要行动
回复 HEARTBEAT_OK
```

---

## Step 5: Correction 闭环修复

### 5.1 创建 `src/memory/correction_manager.py`

```python
"""Correction 闭环管理。

三级系统：
- constitutional: 高频违反规则，每次强制加载
- active: 最近 30 天 correction，强制加载最近 5 条
- archive: 超过 30 天未违反的 correction
"""

class CorrectionManager:
    def __init__(self, data_dir: Path):
        self._constitution_path = data_dir / "identity" / "constitution.md"
        self._active_path = data_dir / "corrections" / "active.json"
        self._archive_dir = data_dir / "corrections" / "archive"
    
    async def add_correction(self, rule: str, source: str) -> None: ...
    async def get_active_corrections(self, n: int = 5) -> list[dict]: ...
    async def record_violation(self, rule_id: str) -> None: ...
    async def check_and_promote(self) -> list[str]: ...  # 返回升级的规则
```

### 5.2 集成到 `StateViewBuilder`

在 `build_for_chat()` 中，强制加载 active corrections 到 memory snippets 层。

---

## Step 6: 结构化工具补充

### 6.1 `src/tools/timezone_tools.py`（新建）

```python
from zoneinfo import ZoneInfo
from datetime import datetime

async def convert_timezone_executor(request, context):
    time_str = request.arguments.get("time_str", "")
    from_tz = request.arguments.get("from_tz", "America/Los_Angeles")
    to_tz = request.arguments.get("to_tz", "Asia/Taipei")
    # 解析并转换
    ...
```

### 6.2 `src/tools/sports_tools.py`（新建）

对接 MLB Stats API（`https://statsapi.mlb.com/api/v1/`）获取 Dodgers 赛程和比分。

### 6.3 `src/tools/calendar_tools.py`（新建）

`lunar_solar_convert` + `get_current_datetime` 工具。

### 6.4 注册到 `src/tools/registry.py`

---

## Step 7: 断路器 + 失败缓存

### 7.1 `src/utils/circuit_breaker.py`（新建）

```python
class CircuitBreaker:
    def __init__(self, cooldown_sequence=(600, 1800, 7200)):
        self._failures: dict[str, list[float]] = {}
        self._cooldowns = cooldown_sequence
    
    def should_allow(self, key: str) -> tuple[bool, str]: ...
    def record_failure(self, key: str) -> None: ...
    def record_success(self, key: str) -> None: ...
```

### 7.2 集成到 `src/core/task_runtime.py`

在 `_execute_tool()`（约行 1444）调用工具前检查断路器：
```python
if not circuit_breaker.should_allow(f"{tool_name}:{key_args}"):
    return ToolExecutionResult(success=False, reason="操作暂时不可用，稍后重试")
```

### 7.3 工具调用预算

在 `ToolLoopContext` 中添加 `tool_call_budget: int` 字段。
在工具循环中检查：
```python
if ctx.tool_calls_count >= ctx.tool_call_budget:
    logger.info("Tool call budget exhausted (%d)", ctx.tool_call_budget)
    # 强制模型用当前信息生成最终回复
    break
```

---

## Step 8: 测试全面更新

### 8.1 需要更新的测试文件

```bash
# 搜索所有引用 tell_user 的测试
grep -rn "tell_user" tests/ --include="*.py" -l
```

每个文件中：
- `tell_user` 工具名 → `send_message`
- `tell_user_buffer` 逻辑 → 新的 `spoken_parts` 逻辑
- 验证用户收到消息的断言 → 检查 `send_fn` 被调用

### 8.2 新增测试

1. **test_direct_output.py** — 验证模型纯文本 → send_fn 调用
2. **test_heartbeat_silent.py** — 验证 HEARTBEAT_OK → 无外部行为
3. **test_heartbeat_active_hours.py** — 验证深夜不触发
4. **test_circuit_breaker.py** — 验证断路器行为
5. **test_correction_promotion.py** — 验证 correction 自动升级
6. **test_timezone_tool.py** — 验证时区转换
7. **test_prompt_budget.py** — 验证 prompt token 预算

### 8.3 最终验证

```bash
python -m pytest tests/ -x -q
# 目标：全部通过，0 failures
```

---

## 附录 A: 从对话历史中提取的 Constitutional Corrections

| 规则 | 被违反次数 | 首次教学日期 |
|------|----------|------------|
| 不要去 Reddit 找内容 | 5+ | 2026-04-05 |
| 不要自己算时区 | 3+ | 2026-04-04 |
| 查完要发给我 | 4+ | 2026-04-04 |
| 确认正确再说 | 5+ | 2026-04-03 |
| 不要编造经历 | 2+ | 2026-04-04 |
| 叫 kuan 不叫 Kevin | 多次 | 2026-04-03 |
| 不要加句号 | 多次 | 2026-04-03 |
| 不要重复发相同内容 | 3+ | 2026-04-08 |

## 附录 B: 行业参照

| 维度 | OpenClaw | Hermes | Claude Code | Codex | Lapwing 当前 | Lapwing 改后 |
|------|---------|--------|------------|-------|-------------|------------|
| 说话 | text=说话 | text=说话 | text=说话 | text=说话 | tell_user工具 | **text=说话** |
| 主动 | Heartbeat+checklist | Cron+nudge | 无(被动) | Automations | 开放式tick | **Heartbeat+checklist** |
| Prompt | 4源合并 | 动态+压缩 | 条件加载+compaction | prefix cache | 8层全量 | **4层+按需** |
| 纠正 | SOUL.md直改 | memory.md程序写入 | CLAUDE.md | AGENTS.md | 写文件但不保证读 | **强制加载+升级** |
| 失败 | Model Resolver | Budget+fallback | Timeout+compaction | 沙箱+timeout | 无限重试 | **断路器+预算** |
