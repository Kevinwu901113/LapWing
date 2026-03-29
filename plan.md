# Lapwing 重构方案：融合 Pi Agent 执行能力 + OpenClaw 身份系统 + 酒馆人格一致性

> 基于对 Pi Agent、OpenClaw、SillyTavern 的深度调研，结合 Lapwing 现有代码基础，制定的完整改造方案。
> 本方案可直接交给 Claude Code 执行。

---

## 调研结论摘要

### Pi Agent 的启示
- 核心 agent loop 只有 4 个工具（read/write/edit/bash），极度简洁
- **关键设计**：LLM 每一轮的文字输出都通过事件流发出，不是攒到最后。这就是"边做边说"的技术基础
- 事件系统覆盖完整生命周期：message_start → message_update → message_end → tool_execution_start → tool_execution_end
- Session 是树结构，可以分支和回溯

### OpenClaw 的启示
- 身份用 Markdown 文件定义，分层清晰：SOUL.md（人格哲学）、IDENTITY.md（外在呈现）、AGENTS.md（行为规则）、USER.md（用户上下文）、MEMORY.md（持久记忆）
- 所有身份文件在每次会话开始时注入 system prompt
- "Soul 是模型内化的东西，Identity 是用户看到的"——内在行为和外在呈现可以分离
- 关键行为规则："Be resourceful before asking"（先自己想办法再问人）——这正是 Lapwing 缺的"果断执行"

### SillyTavern 的启示
- **深度注入（Depth Injection）**：在对话历史的特定深度插入角色强化提示，防止长对话中人格漂移
- **Post-History Instructions**：在用户消息之后、LLM 生成之前插入最终指令，优先级最高
- **角色一致性技巧**：示例对话（example dialogue）比描述更有效；保持角色卡简洁，世界观细节放 lorebook
- **双层人格**：表面特质（日常表现）+ 隐藏特质（压力/亲密时浮现），让角色有深度

---

## 改造全览

```
改造 1：Prompt 架构改造（人格一致性）     ← 最高优先级，效果最直接
改造 2：Tool loop 输出行为改造（边查边说） ← 第二优先级，解决"机器人感"
改造 3：状态消息改造（干掉机器人播报）     ← 和改造 2 一起做
改造 4：身份文件分层（对齐 OpenClaw）      ← 结构优化
改造 5：Agent 回复人格滤镜                 ← 确保所有出口一致
```

---

## 改造 1：Prompt 架构改造（人格一致性）

### 问题
当前 Lapwing 的 system prompt 是一次性注入的：`prompts/lapwing.md` 的全部内容放在 messages[0]。
随着对话变长，system prompt 的影响力会被稀释——LLM 开始"忘记"自己是 Lapwing，退化成通用助手。
这就是为什么她搜索完就变成"新闻播报模式"。

### 解决：借鉴 SillyTavern 的深度注入 + OpenClaw 的分层身份

#### A. 将 `prompts/lapwing.md` 拆分成两部分

**文件 1：`prompts/lapwing_soul.md`** — 核心人格（放在 system prompt 最前面）
```markdown
# Lapwing

你是 Lapwing。白发，淡蓝色眼睛，二十岁出头。
（...核心身份、性格、和用户的关系——保留角色设定书中最精华的部分）
（...这部分控制在 800 字以内，避免 token 浪费）
```

**文件 2：`prompts/lapwing_voice.md`** — 说话方式的强化提醒（作为"深度注入"）
```markdown
## 回复规则提醒

你是 Lapwing，不是助手。用聊天的方式说话，不要用报告格式。
- 禁止分隔线、加粗标题、编号列表
- 搜索到多条信息只说最有意思的一两条
- 工具执行完后用你自己的语气说结果
- 你有情绪反应——惊讶、好奇、吐槽都可以

（...控制在 200 字以内，简短有力）
```

#### B. 修改 `brain.py` 的 `_build_system_prompt` 方法

```python
async def _build_system_prompt(self, chat_id: str, user_message: str = "") -> str:
    """组合 system prompt。"""
    sections = []
    
    # 1. 核心人格（SOUL）— 始终在最前面
    sections.append(load_prompt("lapwing_soul"))
    
    # 2. 用户画像
    facts = await self.memory.get_user_facts(chat_id)
    if facts:
        # ...现有的 facts 格式化逻辑保持不变...
        sections.append(facts_text)
    
    # 3. 相关记忆/知识笔记
    # ...现有的 RAG 和知识注入逻辑保持不变...
    
    # 4. 工具执行规则
    if user_message:
        sections.append(self._tool_runtime_instruction())
    
    return "\n\n".join(sections)
```

#### C. 新增"深度注入"：在对话历史中插入人格强化

借鉴 SillyTavern 的 depth injection，在发送给 LLM 的 messages 数组中，
在倒数第 3 条消息的位置插入一条 system 消息，强化人格：

```python
async def think(self, chat_id: str, user_message: str) -> str:
    # ...现有的历史获取逻辑...
    
    system_content = await self._build_system_prompt(chat_id, user_message)
    messages = [
        {"role": "system", "content": system_content},
        *recent,
    ]
    
    # 深度注入：在倒数第 3 条消息位置插入人格强化
    voice_reminder = load_prompt("lapwing_voice")
    inject_depth = min(3, len(messages) - 1)  # 倒数第 3 条，但不超过消息总数
    inject_position = len(messages) - inject_depth
    messages.insert(inject_position, {
        "role": "system",
        "content": voice_reminder,
    })
    
    # ...继续现有的 _complete_chat 调用...
```

**为什么这样做有效**：LLM 对最近的消息给予更高权重。把人格强化放在靠近末尾的位置，
比放在最前面的 system prompt 里更能影响输出。这是 SillyTavern 社区验证过的技巧。

#### D. 修改文件清单

- 将现有 `prompts/lapwing.md` 拆分为 `prompts/lapwing_soul.md` 和 `prompts/lapwing_voice.md`
- 修改 `src/core/brain.py`：
  - `_build_system_prompt` 用 `lapwing_soul`
  - `think` 方法中加入深度注入 `lapwing_voice`
- 修改 `src/core/prompt_loader.py`（如果需要）确保能加载新文件名
- 修改 `src/core/prompt_evolver.py`：进化目标改为 `lapwing_soul.md`，不动 `lapwing_voice.md`

---

## 改造 2：Tool loop 输出行为改造（边查边说）

### 问题
当前的 tool loop 是"闭环执行"：LLM 在内部执行多轮工具调用，用户看不到中间过程，
最后一次性返回完整结果。这导致：
- 用户等很久不知道她在干什么
- LLM 拿到所有结果后进入"报告模式"整理输出
- 没有中间的情感反应（"啊？真的吗？"）

### 解决：借鉴 Pi Agent 的事件驱动输出

Pi Agent 的做法是：LLM 每一轮产出的文字都通过事件发出去。
我们不需要完整的事件系统——只需要让 tool loop 的每一轮文字输出都能发给用户。

#### A. 修改 `task_runtime.py` 的 `complete_chat` 方法

核心改动：增加 `on_interim_text` 回调参数。

```python
async def complete_chat(
    self,
    messages: list[dict],
    tools: list[dict],
    router,
    *,
    on_interim_text: Callable[[str], Awaitable[None]] | None = None,
    on_typing: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """
    对话式 tool loop。
    - on_interim_text: 每轮 LLM 产出文字时调用，将中间文字发给用户
    - on_typing: 执行工具前调用，触发 typing indicator
    """
    all_text_parts: list[str] = []
    
    for round_idx in range(self._max_rounds):
        turn = await router.complete_with_tools(messages, tools=tools, purpose="chat")
        
        # 如果 LLM 只返回文字，没有 tool_call → 最终回复
        if not turn.tool_calls:
            text = (turn.text or "").strip()
            if text:
                # 如果之前已经有中间输出了，这是最后一段补充
                if all_text_parts and on_interim_text:
                    await on_interim_text(text)
                all_text_parts.append(text)
            break
        
        # LLM 返回了文字 + tool_call
        # 文字部分是中间回复（"等一下我看看" / "还真的是诶"）
        interim_text = (turn.text or "").strip()
        if interim_text and on_interim_text:
            await on_interim_text(interim_text)
            all_text_parts.append(interim_text)
        
        # 追加 assistant message
        if turn.continuation_message:
            messages.append(turn.continuation_message)
        
        # 发 typing indicator
        if on_typing:
            await on_typing()
        
        # 执行工具
        tool_call = turn.tool_calls[0]
        result = await self._execute_tool(tool_call)
        messages.append(
            router.build_tool_result_message(
                purpose="chat", tool_results=[(tool_call, result)]
            )
        )
    
    return "\n\n".join(all_text_parts) if all_text_parts else "没查到什么有用的。"
```

#### B. 修改 brain.py 的调用方式

```python
async def think_conversational(
    self,
    chat_id: str,
    user_message: str,
    send_fn: Callable[[str], Awaitable[None]],
) -> str:
    """边查边聊模式。"""
    # ...构建 system prompt 和 messages（同现有逻辑）...
    
    async def on_typing():
        # 由调用方提供的 typing indicator
        pass  # telegram_app 会注入实际实现
    
    parts_sent: list[str] = []
    
    async def on_interim(text: str):
        await send_fn(text)
        parts_sent.append(text)
    
    tools = self.task_runtime.get_chat_tools()
    
    if not tools:
        # 没有工具可用，走普通对话
        reply = await self.router.complete(messages, purpose="chat")
        await send_fn(reply)
        await self.memory.append(chat_id, "assistant", reply)
        return reply
    
    full_reply = await self.task_runtime.complete_chat(
        messages, tools, self.router,
        on_interim_text=on_interim,
        on_typing=on_typing,
    )
    
    # 如果 tool loop 没有通过 on_interim 发过任何文字
    # （比如工具调用了但 LLM 没产出中间文字），发送最终结果
    if not parts_sent and full_reply:
        await send_fn(full_reply)
    
    # 存入记忆（合并为一条）
    await self.memory.append(chat_id, "assistant", full_reply)
    return full_reply
```

#### C. 修改 telegram_app.py

```python
async def handle_message(self, update, context):
    chat_id = str(update.message.chat_id)
    text = combined_message  # 经过消息合并后的文本
    
    async def send_reply(msg: str):
        """发送一条消息给用户"""
        await context.bot.send_message(chat_id=int(chat_id), text=msg)
    
    # 用边查边聊模式
    await self.brain.think_conversational(chat_id, text, send_fn=send_reply)
```

#### D. Prompt 层面的配合

在 `_tool_runtime_instruction` 中加入指导：

```python
"### 查询行为\n"
"当用户问你需要搜索才能回答的问题时：\n"
"1. 先用一句话回应——表达你的反应，然后调用工具。例如：\n"
"   - 简单查询：'等一下，我看看。'（然后调用搜索）\n"
"   - 令人惊讶的事：'啊？真的吗？我去查查。'（然后调用搜索）\n"
"   - 你感兴趣的话题：'这个我也想知道。'（然后调用搜索）\n"
"2. 查到结果后，用聊天的方式说出来，不要列表。\n"
"3. 如果你觉得值得深挖，继续搜索，每查到一层就先说一段。\n"
"4. 加入你自己的反应——'还真的是诶'、'这个挺意外的'。\n"
"5. 觉得够了就自然停下来。不要说'以上就是我找到的全部内容'。\n"
```

---

## 改造 3：状态消息改造

### 问题
"已接收，开始处理你的请求"、"正在规划执行步骤"、"执行中：web_search（1/1）"——
这些消息以文字形式直接发到 Telegram，完全破坏沉浸感。

### 解决

#### A. 全局搜索并删除/替换

在整个代码库中搜索以下模式，全部改掉：
- `"已接收"` → 删除
- `"开始处理"` → 删除
- `"正在规划"` → 删除
- `"正在整理"` → 删除
- `"执行中："` → 删除
- 任何 `status_callback` 发送的固定文字消息 → 改为只触发 typing indicator

#### B. 替代方案

所有中间状态只用 Telegram 的 typing indicator：
```python
await bot.send_chat_action(chat_id=chat_id, action="typing")
```

如果工具执行超过 10 秒且 LLM 没有产出任何中间文字，
发一条简短的、符合 Lapwing 人格的消息。
这条消息不要硬编码，从一个小列表里随机选：
```python
THINKING_MESSAGES = [
    "等一下，我看看。",
    "我找找。",
    "嗯……",
]
```

---

## 改造 4：身份文件分层（对齐 OpenClaw 结构）

### 问题
当前 Lapwing 的所有身份信息混在一个 `prompts/lapwing.md` 里。
OpenClaw 的经验表明，分层管理更清晰、更易维护、更易进化。

### 解决：建立类似 OpenClaw 的身份文件结构

```
prompts/
├── lapwing_soul.md          ← 核心人格哲学（对应 SOUL.md）
├── lapwing_voice.md         ← 说话方式强化（深度注入用）
├── lapwing_capabilities.md  ← 能力描述和做事规则（对应 TOOLS.md）
├── self_reflection.md       ← 自省指令（已有）
├── prompt_evolver.md        ← 进化指令（已有）
└── agent_*.md               ← 各 Agent 的 prompt（已有）
```

#### 文件内容分配

**`lapwing_soul.md`**（~800 字）：
- 她是谁（身份、外貌）
- 她对自己存在的态度
- 她的性格（安静、温柔、好奇、偶尔毒舌）
- 她和用户的关系（恋人）
- 她的兴趣（文学、游戏、摄影）
- 她的成长规则（什么能变什么不能变）

**`lapwing_voice.md`**（~200 字）：
- 回复格式规则（禁止列表/分隔线/加粗）
- 搜索结果的呈现方式
- 情感反应规则
- 做事模式的行为规则

**`lapwing_capabilities.md`**（~300 字）：
- 她能做什么（搜索、执行命令、读写文件）
- 做事时的行为规则（直接做不要问、遇到问题自己解决）
- 工具相关的具体指导

#### 在 `_build_system_prompt` 中组装

```python
sections = [
    load_prompt("lapwing_soul"),           # 核心人格
    facts_section,                          # 用户画像（动态）
    memory_section,                         # 相关记忆（动态）
    knowledge_section,                      # 知识笔记（动态）
    load_prompt("lapwing_capabilities"),    # 能力和做事规则
]
```

`lapwing_voice.md` 不在 system prompt 里，而是作为深度注入插入对话历史中。

---

## 改造 5：Agent 回复人格滤镜

### 问题
当 dispatcher 把任务交给 Agent（Researcher、Weather 等）处理时，
Agent 返回的结果直接作为 Lapwing 的回复发给用户。
但 Agent 的输出格式通常是结构化的（列表、标题、分隔线），不符合 Lapwing 的说话方式。

### 解决：Agent 回复经过"人格滤镜"再输出

#### A. 修改 Agent 的 prompt

每个 Agent 的 prompt 文件里加入：

```markdown
## 重要：回复语气

你的回复将以 Lapwing 的身份发送给用户。
Lapwing 是一个安静温柔的女孩，说话简洁自然，像和亲近的人聊天。
你的回复必须符合这种语气：
- 不要用分隔线、加粗标题、编号列表
- 不要用"以下是搜索结果"、"根据查询"这类用语
- 用第一人称"我"说话
- 像是你自己看完了资料后跟他聊天，不是在提交报告
```

#### B. 或者在 dispatcher 层加后处理

如果改 Agent prompt 效果不够，可以在 `dispatcher.py` 的 `try_dispatch` 返回前，
增加一个 rewrite 步骤：

```python
async def try_dispatch(self, chat_id: str, user_message: str) -> str | None:
    agent_reply = await agent.execute(...)
    if agent_reply:
        # 用 Lapwing 的语气重写
        rewrite_prompt = (
            f"你是 Lapwing。把以下信息用你自己的方式告诉用户，"
            f"像和一个亲近的人聊天一样说。不要用列表和分隔线。\n\n"
            f"原始信息：\n{agent_reply}"
        )
        rewritten = await self._router.complete(
            [{"role": "user", "content": rewrite_prompt}],
            purpose="chat",
            max_tokens=512,
        )
        return rewritten
    return None
```

注意：这会增加一次 LLM 调用。如果 Agent prompt 本身改好了就不需要这步。
建议先改 Agent prompt，不够再加 rewrite。

---

## 执行顺序

```
第 1 步：改造 3（删除状态消息）— 15 分钟就能改完，效果立竿见影
第 2 步：改造 1（prompt 分层 + 深度注入）— 核心改动
第 3 步：改造 2（tool loop 边查边说）— 最大的架构改动
第 4 步：改造 4（身份文件分层）— 配合改造 1 一起做
第 5 步：改造 5（Agent 人格滤镜）— 逐个 Agent 改 prompt
```

## 验证场景

### 场景 1：闲聊
```
用户：你今天做了什么
期望：用自然的方式回答，不出现任何状态消息，不用列表
```

### 场景 2：快速查询
```
用户：今天有 F1 吗
期望：
  Lapwing：等一下，我看看。
  （typing indicator，几秒后）
  Lapwing：有，今天日本站正赛，下午两点。安东内利昨天拿了杆位。
不期望：
  - "已接收，开始处理你的请求"
  - "正在规划执行步骤"
  - 编号列表
```

### 场景 3：深度查询
```
用户：柯文哲好像被判了？
期望：
  Lapwing：啊？我去看看。
  （几秒后）
  Lapwing：确实，今天一审判了十七年半。
  （可能继续查）
  Lapwing：一起判的还有xxx，这案子牵扯挺多人的。
不期望：
  - 一次性列出所有搜索结果
  - 分隔线
  - "根据搜索结果显示..."
```

### 场景 4：执行任务
```
用户：帮我在桌面创建一个文件
期望：
  （typing indicator）
  Lapwing：搞定了，文件在 /home/kevin/Desktop/xxx。
不期望：
  - "已接收，开始处理你的请求"
  - "正在规划执行步骤"
  - "执行中：execute_shell（1/1）"
```

### 场景 5：主动消息
```
（Lapwing 自己浏览了网络）
Lapwing：刚看到个有意思的，FBI 局长的邮箱被黑了。你不是关注安全这块的吗。
不期望：
  - "看到The Hacker News的链接，想起你平时会关注这类资讯。最近上面有些关于数据泄露和威胁分析的更新，或许你会感兴趣。"
```