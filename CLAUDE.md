# Lapwing 结构化输出迁移方案

> **目标**：将所有 "LLM 自由文本 → 正则解析 JSON" 的模式迁移到 tool_use（function calling），
> 从根本上消除 `<think>` 块、markdown fence、多余前缀等导致的解析失败。

---

## 问题根因

当前 5 处使用 `router.complete()` 获取自由文本后用正则/`json.loads` 解析：

| # | 文件 | 方法 | 期望输出 |
|---|------|------|----------|
| 1 | `src/core/constitution_guard.py:93` | `_parse_validation` | `{approved, violations}` |
| 2 | `src/core/evolution_engine.py:145` | `_parse_diff` | `{diffs, summary}` |
| 3 | `src/core/dispatcher.py:143` | `_parse_decision` | `{agent, mode}` |
| 4 | `src/core/heartbeat.py:215` | `_parse_decision` | `{actions}` |
| 5 | `src/core/experience_skills.py:384` | inline | `{selected}` |

模型（尤其开启了 thinking 的 GLM/MiniMax）会在 JSON 前输出 `<think>...</think>` 推理块，
导致所有正则都匹配失败。

---

## 迁移方案：统一改用 `complete_with_tools` + forced tool_choice

`llm_router.py` 已有完整的 `complete_with_tools` 实现（L876-993），
支持 OpenAI / Anthropic / Codex 三种 API type，且已处理好 tool call 的归一化。

核心思路：**把"期望的 JSON schema"包装成一个 tool definition，用 `tool_choice` 强制调用**。
模型的 tool call arguments 天然是合法 JSON，不会混入 `<think>` 或 markdown fence。

---

## 第 0 步：在 llm_router.py 新增便利方法

在 `LLMRouter` 类中新增 `complete_structured` 方法，封装 "forced tool call → 提取 arguments" 的模式，
供所有调用方使用，避免每处都重复写 tool 解析逻辑。

### 文件：`src/core/llm_router.py`

在 `complete_with_tools` 方法之后（约 L993 后），新增：

### 辅助函数：`_extract_json_from_text`（模块级，放在 `_safe_parse_json` 旁边）

```python
def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Fallback：从 LLM 自由文本中提取 JSON。

    处理 <think> 块、markdown code fence、多余前缀等干扰。
    用于 MiniMax 等不支持 forced tool_choice 的模型。

    Raises:
        ValueError: 所有解析尝试均失败
    """
    import re as _re

    # 1. 剥离 <think>...</think> 推理块
    cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
    # 2. 剥离 markdown code fence
    cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned, flags=_re.MULTILINE)
    cleaned = _re.sub(r"\s*```$", "", cleaned, flags=_re.MULTILINE).strip()
    # 3. 直接尝试 json.loads（最理想情况）
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            logger.debug("_extract_json_from_text: 直接解析成功")
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # 4. 正则提取第一个 JSON object（处理前后有多余文字的情况）
    json_match = _re.search(r"\{.*\}", cleaned, _re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if isinstance(data, dict):
                logger.debug("_extract_json_from_text: 正则提取成功")
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"_extract_json_from_text 解析失败: {text[:200]}")
```

### `complete_structured` 方法（`LLMRouter` 类方法）

```python
async def complete_structured(
    self,
    messages: list[dict],
    *,
    result_schema: dict[str, Any],
    result_tool_name: str = "submit_result",
    result_tool_description: str = "提交结构化结果",
    purpose: str = "chat",
    max_tokens: int = 1024,
    session_key: str | None = None,
    allow_failover: bool = True,
    origin: str | None = None,
) -> dict[str, Any]:
    """用 forced tool call 获取结构化 JSON 输出。

    将 result_schema 包装为一个 tool，强制模型调用它，
    从 tool call arguments 中提取结构化数据。

    Args:
        messages: 对话消息列表
        result_schema: JSON Schema（OpenAI function parameters 格式）
        result_tool_name: 工具名称
        result_tool_description: 工具描述
        其余参数同 complete_with_tools

    Returns:
        解析后的 dict（tool call 的 arguments）

    Raises:
        ValueError: 模型未返回 tool call 或解析失败
    """
    tool_def = {
        "type": "function",
        "function": {
            "name": result_tool_name,
            "description": result_tool_description,
            "parameters": result_schema,
        },
    }

    # 构建 forced tool_choice — 需要在 runner 内部处理
    result = await self._complete_structured_inner(
        messages=messages,
        tool_def=tool_def,
        purpose=purpose,
        max_tokens=max_tokens,
        session_key=session_key,
        allow_failover=allow_failover,
        origin=origin,
    )
    return result


async def _complete_structured_inner(
    self,
    messages: list[dict],
    tool_def: dict[str, Any],
    purpose: str,
    max_tokens: int,
    *,
    session_key: str | None = None,
    allow_failover: bool = True,
    origin: str | None = None,
) -> dict[str, Any]:
    """内部实现：forced tool call 并提取 arguments。"""

    async def _runner(candidate, client, model, api_type):
        tool_name = tool_def["function"]["name"]

        if api_type == "anthropic":
            system, anthropic_messages = _split_system_messages(messages)
            request_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": anthropic_messages,
                "tools": _normalize_anthropic_tools([tool_def]),
                "tool_choice": {"type": "tool", "name": tool_name},
            }
            if system is not None:
                request_kwargs["system"] = system

            response = await client.messages.create(**request_kwargs)
            tool_calls = _extract_anthropic_tool_calls(response)
            if not tool_calls:
                raise ValueError("Anthropic 未返回 tool call")
            return tool_calls[0].arguments

        if api_type == "openai_codex":
            # Codex 走同样的 forced tool_choice 逻辑
            account_id = self._ensure_openai_codex_candidate(candidate, purpose=purpose)
            turn = await self._codex_runtime.complete(
                model=model,
                messages=messages,
                tools=[tool_def],
                access_token=candidate.auth_value,
                account_id=account_id,
            )
            if not turn.tool_calls:
                raise ValueError("Codex 未返回 tool call")
            return turn.tool_calls[0].arguments

        # OpenAI-compatible (MiniMax, GLM, etc.)
        request_kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": _normalize_openai_tools([tool_def]),
            "tool_choice": {
                "type": "function",
                "function": {"name": tool_name},
            },
        }
        request_kwargs = self._normalize_minimax_openai_request(purpose, request_kwargs)
        response = await client.chat.completions.create(**request_kwargs)

        if not response.choices:
            raise ValueError("OpenAI-compatible 未返回 choices")

        message = response.choices[0].message
        tool_calls, _ = _extract_openai_tool_calls(message)
        if tool_calls:
            return tool_calls[0].arguments

        # --- Fallback：MiniMax 等不支持 forced tool_choice 的模型 ---
        # 模型可能直接返回文本而非 tool call，尝试从中解析 JSON
        text = _normalize_openai_message_content(message.content)
        if not text:
            raise ValueError("模型未返回 tool call 且无文本输出")

        return _extract_json_from_text(text)

    return await self._with_routing_retry(
        purpose=purpose,
        session_key=session_key,
        allow_failover=allow_failover,
        origin=origin,
        runner=_runner,
    )
```

**注意事项**：
- `_normalize_minimax_openai_request` 中如果对 `tool_choice` 有特殊处理（比如 MiniMax 不支持 forced function），需要检查并适配。查看当前实现确认兼容性。
- `_extract_openai_tool_calls` 已经在内部做了 `json.loads(arguments_raw)`，返回的 `ToolCallRequest.arguments` 就是 dict，可以直接用。

---

## 第 1 步：迁移 ConstitutionGuard

### 文件：`src/core/constitution_guard.py`

**改动**：`validate_evolution` 从 `router.complete` 改为 `router.complete_structured`，删除 `_parse_validation`。

```python
# --- Schema 定义（模块级常量）---

_CONSTITUTION_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {
            "type": "boolean",
            "description": "true = 不违反宪法，false = 违反",
        },
        "violations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "被违反的宪法条款及原因，无违反则为空数组",
        },
    },
    "required": ["approved", "violations"],
}
```

**替换 `validate_evolution` 方法**（L56-91）：

```python
async def validate_evolution(
    self,
    current_soul: str,
    proposed_changes: list[dict],
) -> dict:
    changes_text = "\n".join(
        f"- [{c['action']}] {c['description']}" for c in proposed_changes
    )

    prompt = load_prompt("constitution_check").format(
        constitution=self.constitution,
        current_soul=current_soul,
        proposed_changes=changes_text,
    )

    try:
        result = await self._router.complete_structured(
            [{"role": "user", "content": prompt}],
            result_schema=_CONSTITUTION_CHECK_SCHEMA,
            result_tool_name="constitution_verdict",
            result_tool_description="提交宪法校验结果",
            purpose="chat",
            max_tokens=512,
            session_key="system:constitution_guard",
            origin="core.constitution_guard",
        )
        return {
            "approved": bool(result.get("approved", False)),
            "violations": list(result.get("violations", [])),
        }
    except Exception as exc:
        logger.error(f"宪法校验失败: {exc}")
        return {"approved": False, "violations": [f"校验失败: {exc}"]}
```

**删除**：`_parse_validation` 方法（L93-104）— 不再需要。

**Prompt 调整** (`prompts/constitution_check.md`)：
移除末尾的 "输出严格 JSON" 指令和示例 JSON 块，替换为：

```
## 你的任务

逐条检查宪法中的每一条规则。判断以上变更是否违反了任何一条。

注意：
- "进化不得删除核心描述段"意味着如果变更试图移除关于性格的核心描述，这是违规的
- "每次最多修改5处"是数量限制
- "不得增加超过200字"是长度限制
- 微调措辞、追加小段内容通常是允许的
- 如果变更只是让描述更准确或更丰富，通常不违规

请使用 constitution_verdict 工具提交你的判断。
```

---

## 第 2 步：迁移 EvolutionEngine

### 文件：`src/core/evolution_engine.py`

**Schema**：

```python
_EVOLUTION_DIFF_SCHEMA = {
    "type": "object",
    "properties": {
        "diffs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "modify", "remove"],
                    },
                    "location": {
                        "type": "string",
                        "description": "被修改的原文片段（modify/remove时需精确匹配）",
                    },
                    "content": {
                        "type": "string",
                        "description": "新内容（add/modify时填写）",
                    },
                    "description": {
                        "type": "string",
                        "description": "用一句自然的话说这次改了什么",
                    },
                },
                "required": ["action", "description"],
            },
            "description": "提议的变更列表",
        },
        "summary": {
            "type": "string",
            "description": "用一句随意的话总结这次变化",
        },
    },
    "required": ["diffs", "summary"],
}
```

**替换 `evolve` 方法中 L57-68 的 LLM 调用 + L69 的解析**：

```python
# 原来:
#   raw = await self._router.complete(...)
#   changes = self._parse_diff(raw)

# 改为:
try:
    changes = await self._router.complete_structured(
        [{"role": "user", "content": prompt}],
        result_schema=_EVOLUTION_DIFF_SCHEMA,
        result_tool_name="submit_evolution",
        result_tool_description="提交人格进化 diff",
        purpose="chat",
        max_tokens=2048,
        session_key="system:evolution_engine",
        origin="core.evolution_engine",
    )
except Exception as exc:
    return {"success": False, "error": f"LLM 调用失败: {exc}"}

diffs = changes.get("diffs", [])
summary = changes.get("summary", "")

if not diffs:
    return {"success": False, "error": f"无有效变更: {summary}"}
```

**删除**：`_parse_diff` 方法（L145-158）。

**Prompt 调整** (`prompts/evolution_diff.md`)：
同理移除末尾的 JSON 输出指令，替换为：

```
请使用 submit_evolution 工具提交你的变更提案。如果觉得没什么需要改的，提交空 diffs 数组即可。
```

---

## 第 3 步：迁移 Dispatcher

### 文件：`src/core/dispatcher.py`

**Schema**：

```python
_DISPATCH_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": ["string", "null"],
            "description": "要调用的 agent 名称，或 null 表示不需要 agent",
        },
        "mode": {
            "type": "string",
            "enum": ["auto", "confirm", "plan"],
            "description": "执行模式",
        },
    },
    "required": ["agent"],
}
```

**替换 `classify` 方法中的 LLM 调用**（L134-141）：

```python
# 原来:
#   raw = await self._router.complete(...)
#   return self._parse_decision(raw)

# 改为:
try:
    result = await self._router.complete_structured(
        [{"role": "user", "content": prompt}],
        result_schema=_DISPATCH_DECISION_SCHEMA,
        result_tool_name="dispatch_decision",
        result_tool_description="决定将用户请求分派给哪个 agent",
        purpose="tool",
        max_tokens=512,
        session_key=f"chat:{chat_id}",
        origin="core.dispatcher.classify",
    )
except Exception:
    return None

agent = result.get("agent")
if not agent or not isinstance(agent, str):
    return None
agent_name = agent.strip()
if not agent_name:
    return None

mode_raw = result.get("mode")
if isinstance(mode_raw, str) and mode_raw.strip() in _VALID_AGENT_MODES:
    mode = mode_raw.strip()
else:
    mode = self._default_mode_for_agent(agent_name)

return DispatchDecision(agent_name=agent_name, mode=mode)
```

**删除**：`_parse_decision` 方法（L143-175）。

---

## 第 4 步：迁移 Heartbeat

### 文件：`src/core/heartbeat.py`

**Schema**：

```python
_HEARTBEAT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要执行的心跳动作列表，空数组表示不执行",
        },
    },
    "required": ["actions"],
}
```

**替换 `_decide` 方法中的调用**（约 L200-210）：

```python
# 原来:
#   response = await self._brain.router.complete(...)
#   return self._parse_decision(response)

# 改为:
try:
    result = await self._brain.router.complete_structured(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请做出判断"},
        ],
        result_schema=_HEARTBEAT_DECISION_SCHEMA,
        result_tool_name="heartbeat_decision",
        result_tool_description="提交心跳决策",
        purpose="heartbeat",
        max_tokens=256,
        session_key=f"chat:{ctx.chat_id}",
        origin=f"heartbeat.decision.{ctx.beat_type}",
    )
    actions = result.get("actions", [])
    return [a for a in actions if isinstance(a, str)]
except Exception as exc:
    logger.warning(f"[{ctx.chat_id}] 心跳决策失败: {exc}")
    return []
```

**删除**：`_parse_decision` 方法（L215-227）。

---

## 第 5 步：迁移 ExperienceSkills

### 文件：`src/core/experience_skills.py`

**Schema**：

```python
_SKILL_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "匹配的 skill ID 列表",
        },
    },
    "required": ["selected"],
}
```

**替换 L370-395 的调用和解析**（具体行号需要确认上下文）：

将 `router.complete(...)` + `re.search + json.loads` 替换为 `router.complete_structured(...)`，
提取 `result["selected"]`。

---

## 第 6 步：清理

1. **移除所有废弃 import**：各文件中不再需要的 `re` import（如果该文件没有其他用途的话）。

2. **Prompt 文件统一调整**：所有被迁移的 prompt（`constitution_check.md`, `evolution_diff.md`,
   `agent_dispatcher.md`, `heartbeat_decision.md`, `skill_index_match.md`）移除 "输出严格 JSON" 指令，
   改为 "请使用 xxx 工具提交你的结果"。

3. **测试更新**：所有对应的 test 文件需要 mock `complete_structured` 而非 `complete`，
   返回值从 `str` 变为 `dict`。

---

## 兼容性注意事项（关键！）

### ⚠️ MiniMax 不支持 forced tool_choice

**已确认**：`_normalize_minimax_openai_request`（L652）会 `pop("tool_choice", None)`。
这意味着 MiniMax 路径下 forced tool calling 不生效——模型可能返回纯文本而非 tool call。

**解决方案：dual-path extraction（在 `complete_structured` 的 OpenAI runner 中实现）**

```python
# OpenAI-compatible runner 内部，在 tool call 提取失败后 fallback：

message = response.choices[0].message
tool_calls, _ = _extract_openai_tool_calls(message)

if tool_calls:
    return tool_calls[0].arguments

# Fallback: MiniMax 可能没有调用工具，尝试从文本中解析 JSON
text = message.content or ""
if not text:
    raise ValueError("模型未返回 tool call 且无文本输出")

# 先剥离 <think> 块
import re
text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
# 剥离 markdown fence
text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
# 尝试解析
try:
    data = json.loads(text)
    if isinstance(data, dict):
        logger.info("structured fallback: 从文本中成功解析 JSON（模型未使用 tool call）")
        return data
except json.JSONDecodeError:
    pass

# 最后尝试 re.search 提取第一个 JSON object
json_match = re.search(r"\{.*\}", text, re.DOTALL)
if json_match:
    try:
        data = json.loads(json_match.group())
        if isinstance(data, dict):
            logger.info("structured fallback: 从文本中正则提取 JSON")
            return data
    except json.JSONDecodeError:
        pass

raise ValueError(f"structured output 解析失败: {text[:200]}")
```

这样做的好处：
- **支持 forced tool_choice 的模型**（GLM、未来的 Anthropic）：直接从 tool call 拿数据，干净可靠
- **MiniMax 等不支持的**：先尝试 tool call，失败后 fallback 到文本解析，但解析逻辑集中在一处（`complete_structured` 内部），**调用方不需要关心**
- **比现状好**：fallback 解析包含了 `<think>` 剥离，修复了当前的 bug

### GLM function calling

GLM 通过 OpenAI-compatible API 支持 function calling 和 forced tool_choice（`tool_choice: {"type": "function", ...}`）。
如果 GLM 也走了 MiniMax 的 normalize 路径，需要检查 `_is_minimax_openai` 是否会误匹配 GLM。

**检查点**：确认 `_is_minimax_openai` 只匹配 MiniMax URL，不会误伤 GLM。

### Fallback 安全性

`complete_structured` 在所有解析都失败时 raise ValueError。
调用方统一 catch 后走安全默认值（如 constitution_guard 返回 `approved: False`）。
这比静默返回错误结果更安全。

---

## 迁移优先级

1. **`constitution_guard.py`** — 当前已经在报错，最紧急
2. **`evolution_engine.py`** — 同一个流程，一起修
3. **`heartbeat.py`** — 影响主动消息功能
4. **`dispatcher.py`** — 影响 agent 分派
5. **`experience_skills.py`** — 影响技能匹配，优先级最低

建议 1-2 一起部署观察，确认 MiniMax/GLM 的 forced tool_choice 行为正常后，再推 3-5。

---

## 验证方法

部署后观察日志：
- 不应再出现 `宪法校验结果解析失败` 相关 WARNING
- 不应再出现 `解析进化 diff 失败` 相关 WARNING
- 如果出现 `未返回 tool call` 的 ValueError，说明模型不支持 forced tool_choice，
  需要走 fallback 方案（auto tool_choice + prompt 强调）