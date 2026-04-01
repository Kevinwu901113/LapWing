# 紧急修复：structured output <think> 截断问题

> 问题：MiniMax 不支持 forced tool_choice，模型输出被 `<think>` 推理占满所有 tokens，
> `</think>` 从未出现导致正则匹配失败，fallback 解析也失败。

---

## 修复 1：`_extract_json_from_text` — 处理未闭合的 `<think>`

### 文件：`src/core/llm_router.py`

**替换** L168-169 的 think 剥离逻辑：

```python
# 旧代码：
# cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()

# 新代码：
# 1a. 剥离完整的 <think>...</think> 块
cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
# 1b. 处理未闭合的 <think>（被 max_tokens 截断的情况）
#     从 <think> 开始到字符串末尾全部删除
cleaned = _re.sub(r"<think>.*$", "", cleaned, flags=_re.DOTALL)
cleaned = cleaned.strip()
```

这样即使 `</think>` 不存在也能正确剥离。

---

## 修复 2：`complete_structured` — MiniMax 路径注入反思考指令

### 文件：`src/core/llm_router.py`

在 `_complete_structured_inner` 的 OpenAI-compatible 分支中（约 L1220 附近），
在发送请求前对 messages 注入反思考指令：

找到这一段（约 L1220-1231）：
```python
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
```

**在 `request_kwargs` 构建之前**，插入：

```python
            # OpenAI-compatible (MiniMax, GLM, etc.)

            # MiniMax 会 pop tool_choice，模型可能不走 tool call。
            # 注入指令抑制 <think> 输出、强制 JSON 格式。
            structured_messages = list(messages)  # 浅拷贝
            if self._is_minimax_openai(purpose):
                anti_think = (
                    "重要：不要使用 <think> 标签。不要输出任何思考过程。"
                    "如果无法调用工具，直接输出纯 JSON，不要有任何其他文字。"
                )
                # 插入为第一条 system message
                structured_messages.insert(0, {"role": "system", "content": anti_think})

            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": structured_messages,
                "tools": _normalize_openai_tools([tool_def]),
                "tool_choice": {
                    "type": "function",
                    "function": {"name": tool_name},
                },
            }
            request_kwargs = self._normalize_minimax_openai_request(purpose, request_kwargs)
```

---

## 修复 3：提高 constitution_guard 的 max_tokens

### 文件：`src/core/constitution_guard.py`

将 `max_tokens=512` 改为 `max_tokens=1536`。

理由：即使反思考指令不完全生效，1536 tokens 足够模型完成思考 + 输出 JSON。
512 太紧了——光 `<think>` 推理就能用完。

```python
            result = await self._router.complete_structured(
                [{"role": "user", "content": prompt}],
                result_schema=_CONSTITUTION_CHECK_SCHEMA,
                result_tool_name="constitution_verdict",
                result_tool_description="提交宪法校验结果",
                slot="persona_expression",
                max_tokens=1536,  # 从 512 提高，防止 thinking 截断
                session_key="system:constitution_guard",
                origin="core.constitution_guard",
            )
```

---

## 修复 4（可选但推荐）：MiniMax 尝试 response_format

MiniMax M2.7 支持 OpenAI 兼容的 `response_format: {"type": "json_object"}`。
可以在 `complete_structured` 的 MiniMax 路径中额外传入此参数，
双重约束输出格式（tool_choice 被 pop 后，至少 response_format 还在）。

### 文件：`src/core/llm_router.py`

在 `_normalize_minimax_openai_request` 中，或在 `_complete_structured_inner` 的
request_kwargs 构建时，对 MiniMax 补充：

```python
            if self._is_minimax_openai(purpose):
                # MiniMax 不支持 tool_choice，用 response_format 兜底
                request_kwargs["response_format"] = {"type": "json_object"}
```

**注意**：这行要放在 `_normalize_minimax_openai_request` 调用之前，
确认 normalize 不会 pop 掉 `response_format`。

---

## 验证

部署后检查日志：
- `_extract_json_from_text 解析失败` 不应再出现
- 如果看到 `_extract_json_from_text: 直接解析成功` 或 `正则提取成功`，说明 fallback 路径生效
- 如果模型开始正确调用 tool，会看不到 fallback 日志（最理想情况）

## 修复总结

| 层 | 问题 | 修复 |
|----|------|------|
| 正则 | `<think>` 未闭合，正则匹配不到 | 加第二条正则处理未闭合标签 |
| Prompt | 模型浪费 tokens 在思考上 | MiniMax 路径注入反思考指令 |
| Tokens | 512 不够模型思考+输出 | 提高到 1536 |
| 格式 | tool_choice 被 pop，无格式约束 | 补充 response_format=json_object |