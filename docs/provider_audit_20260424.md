# Provider 层对标体检（2026-04-24）

范围：只读审查现有 provider 层与 prompt/tool/context 相关代码；不改实现。题目中提到的 `src/llm/*` 在当前仓库不存在，等价实现主要在：

- `src/core/model_config.py`
- `src/core/llm_router.py`
- `src/core/llm_protocols.py`
- `src/core/task_runtime.py`
- `src/core/brain.py`
- `src/core/state_serializer.py`
- `src/core/state_view_builder.py`
- `src/core/minimax_vlm.py`
- `src/auth/service.py`
- `config.toml`
- `data/config/model_routing.json`

外部对标参考：OpenClaw 当前公开文档仍把 `openai/*` 与 `openai-codex/*` 作为独立路由族；`openai-codex/*` 走 ChatGPT/Codex OAuth，不是 OpenAI Platform API key 路径。Issue #38706 记录了 `openai-codex` 被错误送到 `api.openai.com/v1/responses` 后因缺 `api.responses.write` scope 返回 401 的问题；Issue #57930 记录了 oversized `instructions` 导致 Codex 400，并建议约 32 KiB 上限。

备注：OpenClaw 2026-04-24 当前 OAuth 文档仍提到“复用外部 CLI credentials 时由 CLI 管理，OpenClaw 只重读外部源”的路径；本仓库的 future Codex 设计备忘按本任务要求保留更严格的“Lapwing 不复用 Codex CLI auth store”约束。

## A1. Provider Namespace 物理切分审查

### 当前 Provider 清单

| Provider / 路径 | 当前状态 | Base URL / Endpoint | Auth 方式 | 覆盖模型 | 协议 |
| --- | --- | --- | --- | --- | --- |
| `volcengine` 火山方舟 Coding Plan | 已配置，承担主对话、人格、记忆、agent 等大部分 slot | `https://ark.cn-beijing.volces.com/api/coding` | `Authorization: Bearer <LLM_API_KEY>`，由 `model_routing.json` 的 `FROM_ENV` 经 `_resolve_env_keys()` 解析 | `minimax-m2.7`、`glm-5.1`、`kimi-k2.6`、`doubao-seed-2.0-pro` | `api_type=anthropic`，走 Anthropic-compatible message/tool 协议 |
| MiniMax Coding Plan VLM | 单独客户端，默认未启用 | `https://api.minimaxi.com/v1/coding_plan/vlm` | `Authorization: Bearer <MINIMAX_VLM_API_KEY>`；若未显式配置会 fallback 到 `LLM_CHAT_API_KEY or LLM_API_KEY` | VLM endpoint，不走 `model_routing.json` 模型列表 | 自定义 VLM JSON：`prompt` + `image_url` |
| `nvidia` NVIDIA NIM | 已配置，用于 `heartbeat_proactive` | `https://integrate.api.nvidia.com/v1` | `NIM_API_KEY`，缺失时 `model_config._resolve_env_keys()` 仍可能 fallback 到 `LLM_API_KEY` | `moonshotai/kimi-k2-instruct` | `api_type=openai`，走 OpenAI Chat Completions-compatible |
| `codex-oauth` | 已出现在 `model_routing.json`，但当前没有任何有效 slot 指向它 | `model_routing.json` 中为空；实际 `src/core/codex_oauth_client.py` 硬编码 `https://chatgpt.com/backend-api/codex/responses` | 当前实现读取 `~/.codex/auth.json`，并在检测到测试 token 时尝试从 `~/.lapwing/auth/auth-profiles.json` 恢复 | `gpt-5.3-codex`、`gpt-5.4`、`gpt-5.4-mini` | `api_type=codex_oauth`，走 Responses item/SSE |
| Claude / Anthropic native | 代码支持，当前配置未注册独立 provider | 取决于 provider base URL；`api.anthropic.com` 被识别为 native Anthropic | API key / auth profile 路由取决于配置 | 未在当前 `model_routing.json` 注册 | `anthropic` |

### 路由双键情况

正向配置路径基本是 `(provider_id, model_id)` 双键：

- `SlotAssignment` 明确包含 `provider_id` 和 `model_id`。
- `ModelConfigManager.resolve_slot(slot_id)` 会先找 provider，再验证 model 是否属于该 provider。
- `LLMRouter._setup_routing()` 按 slot 注册 `base_url/model/api_type`。

但运行时仍有几处退化成 model-name-only 或 URL 推断：

1. `LLMRouter._model_provider_map` 是 `dict[model_id, ProviderInfo]`。如果两个 provider 暴露同名 model，后注册的 provider 会覆盖前者，session override 可能串 provider。
2. `AuthManager.register_slot_config()` 丢失显式 `provider_id`，改用 `_infer_provider_from_route(base_url, model)` 推断 provider。当前 provider 身份在 auth 层不是一等字段，未来同 host 多 provider、代理 host、空 base URL provider 都容易误判。
3. `_resolve_client(..., model_override=...)` 在有 model override 时用 `_detect_api_type(base_url, model)`，只有 `_with_routing_retry()` 针对 `codex_oauth` 做了额外分支；其它跨 provider override 仍可能沿用原 slot 的 base URL/protocol。
4. `agent_team_lead` 存在于 `model_routing.json`，但不在 `SLOT_DEFINITIONS`，因此 `_deserialize()` 会忽略它，实际不可用。

### Provider 内 model 配置独立性

当前 provider 下不同 model 的独立配置不足：

- `ProviderInfo` 只有 provider 级 `reasoning_effort`、`context_compaction`，没有 per-model `temperature`、`max_tokens`、`tool_choice`、`supports_parallel_tools`、`supports_forced_tool_choice`。
- `complete_with_tools()` 对 Anthropic-compatible 模型统一使用 `tool_choice={"type":"auto","disable_parallel_tool_use":true}`，对 OpenAI-compatible 模型统一使用 `tool_choice="auto"` 和 `parallel_tool_calls=false`。
- `complete_structured()` 对 Anthropic/OpenAI-compatible 强制 tool call；Codex 路径仅提供 tool 而不强制，再 fallback 到自由文本 JSON 解析。
- 常规调用的 `max_tokens` 来自调用点默认值或参数，不来自 provider/model metadata。

### 串台风险点

- `codex-oauth` provider 已存在于配置，但 `api_key` 会被 `_resolve_env_keys()` 误填为 `LLM_API_KEY`；虽然当前 Codex OAuth 分支不使用这个 key，但配置层语义会混淆。
- `config.toml` 下 `[codex] runtime_base_url/runtime_client_version/runtime_timeout_seconds` 不在 `src/config/settings.py::CodexConfig` 中，当前会被 Pydantic `extra="ignore"` 忽略；实际 Codex runtime endpoint 仍由 `codex_oauth_client.py` 常量决定。
- MiniMax VLM 的 key fallback 到 chat/global LLM key。若 VLM endpoint 与文本 provider 使用不同 key，会产生隐式串 key 风险。
- 浏览器视觉 fallback 构造的是 Anthropic-style image block；若 `browser_vision` slot 未来指向 OpenAI-compatible 文本模型，`_normalize_openai_message_content()` 会丢弃 image block，只保留文本 prompt。

## A2. Prompt 大小防护现状

### 当前 prompt 拼接点

主路径：

1. `LapwingBrain._prepare_think()` 记录用户消息，执行 `ConversationCompactor.try_compact(chat_id)`，加载历史并做 trust tagging。
2. `LapwingBrain._render_messages()` 调用 `StateViewBuilder.build_for_chat()` 或 `build_for_inner()`。
3. `StateSerializer.serialize()` 生成单个 system prompt，并返回 trajectory window messages。
4. `LLMRouter.complete()` / `complete_with_tools()` 再按 provider 协议转换：
   - Anthropic-compatible：system messages 被拆到 Anthropic `system` 字段。
   - OpenAI-compatible：保留 Chat Completions messages。
   - Codex OAuth：`_convert_messages_to_responses_api()` 把所有 system 消息合并成 `instructions`。

System prompt 的静态/动态层顺序：

1. `data/identity/soul.md`
2. `data/identity/constitution.md`
3. `prompts/lapwing_voice.md`
4. `_PERSONA_ANCHOR`
5. 时间/环境感知
6. runtime state：通道、活跃承诺、提醒、任务、技能等
7. memory snippets
8. corrections

### 现场静态长度样本

仅按当前文件字节数统计，不包含 runtime state、memory snippets、corrections、trajectory、tool schemas：

| 片段 | 当前大小 |
| --- | ---: |
| `data/identity/soul.md` | 1,461 bytes |
| `data/identity/constitution.md` | 987 bytes |
| `prompts/lapwing_voice.md` | 1,174 bytes |
| `_PERSONA_ANCHOR` | 约数百 bytes |
| 静态 identity+voice 小计 | 约 4 KiB |
| `prompts/lapwing_soul.md` fallback | 3,126 bytes |
| `prompts/browser_vision_describe.md` | 471 bytes |

动态部分没有 provider-aware cap：

- `memory_top_k=10`，每条 snippet 内容直接拼进 `## 记忆片段`。
- active commitments/tasks/reminders/skill summary 有数量截断，但不是 token/byte 预算。
- chat trajectory 由 `MAX_HISTORY_TURNS * 2` 控制，当前 `MAX_HISTORY_TURNS=20`，即最多约 40 条 user/assistant turn。
- inner loop 默认 `inner_history_turns=50`。
- 当前已有 93 个 conversation summary 文件，但 prompt 内是否注入取决于 trajectory/history，而不是按 provider 预算裁剪。

### 各 provider 硬上限建模现状

| Provider | 代码中是否建模 input/context 硬上限 | 代码中是否建模 instructions/system 硬上限 | 当前行为 |
| --- | --- | --- | --- |
| 火山方舟 Coding Plan / Anthropic-compatible | 否 | 否 | 超限依赖 API 报错，再由 `PromptTooLongError` 分类触发 reactive compact |
| NVIDIA NIM / OpenAI-compatible | 否 | 否 | 超限依赖 API 报错；没有 provider-specific message budget |
| MiniMax VLM endpoint | 否 | 否 | 只发送单图 + prompt，无通用 prompt 拼接 |
| Codex OAuth | 否；只有 provider 级 `context_compaction` 布尔 | 否；没有 32 KiB `instructions` 检查 | system messages 会直接合并进 `instructions` 后发送 |
| Claude / Anthropic native | 否 | 否 | 只区分 native Anthropic prefix cache，不做长度 cap |

### 现有防护机制

- `ConversationCompactor.should_compact()` 基于消息条数：`history_length >= int(MAX_HISTORY_TURNS*2*0.8)` 时摘要前 60%，保留后 40%。这是 history compaction，不是 provider-aware token budget。
- `TaskRuntime._reactive_compact()` 在 `PromptTooLongError` 后清理旧 tool results；只识别 OpenAI `role="tool"` 和 Anthropic `tool_result` block。
- `TaskRuntime._budget_tool_result()` 超过 `TOOL_RESULT_BUDGET_MAX_CHARS=50_000` 时把完整工具结果落盘，只留 preview。
- `TaskRuntime._format_tool_result_for_llm()` 超过 `_TOOL_RESULT_MAX_CHARS=12_000` 时截断传给 LLM 的文本。
- Codex payload 日志有 `_payload_summary()`，会记录 payload 结构和字符数，但不阻断超限请求。

### 缺口

- 没有 provider-aware byte/token estimator，也没有 instructions/system prompt 单独预算。
- Codex `instructions` 没有约 32 KiB 上限保护，也没有“低优先级片段截断 + 溢出挪到 input messages”的路径。
- `PromptTooLongError` 后的 reactive compact 只清工具结果，不会裁剪 memory snippets、corrections、runtime state、identity docs。
- Codex 的 `context_management` 在 payload 里可注入，但 `_sanitize_codex_payload()` 的测试预期表明当前会过滤掉未知/不支持字段；它不能替代本地预算。
- 现有 `max_tokens` 只控制输出预算，不控制输入上下文预算。

## A3. 工具协议路径矩阵

### Provider × 协议矩阵

| Provider / 场景 | 请求协议 | Tool call 提取 | Tool result 回传 | 当前判断 |
| --- | --- | --- | --- | --- |
| 火山方舟 Coding Plan | Anthropic-compatible：system 拆分，messages 为 user/assistant，tools 归一化为 Anthropic `input_schema` | `_extract_anthropic_tool_calls()` 读取 `tool_use` block | `{"role":"user","content":[{"type":"tool_result","tool_use_id":...}]}` | 主路径正确；协议依赖 `api_type=anthropic` 和 `/api/coding` |
| Claude / native Anthropic | Anthropic native；额外启用 prefix cache marker | 同上 | 同上 | 代码支持，当前未配置 provider |
| NVIDIA NIM | OpenAI Chat Completions-compatible | `_extract_openai_tool_calls()` 读取 `message.tool_calls` | `{"role":"tool","tool_call_id":...,"content":...}` | 通用 OpenAI tool path 正确 |
| MiniMax VLM endpoint | 自定义 HTTP JSON，不参与 tool loop | 无 | 无 | 与 tool loop 物理分离 |
| Codex OAuth | Responses item API；system 合并成 `instructions`，messages 转 `input` item | `_extract_responses_api_tool_calls()` 读取 `function_call` output item | `{"type":"function_call_output","call_id":...,"output":...}` | 适配器存在；当前没有有效 slot 指向它；仍有 budget/compaction 缺口 |
| Browser vision router fallback | 构造 Anthropic-style image content block 后调用 `LLMRouter.complete()` | 无 | 无 | 若 slot 指向 OpenAI-compatible，image block 会被文本归一化丢弃，属于视觉 fallback 风险 |

### 提前退出和 tool_result 补齐

`TaskRuntime._run_step()` 的正常工具循环和 Error Burst Guard 早退路径都会调用 `LLMRouter.build_tool_result_message(slot="main_conversation", session_key=f"chat:{chat_id}")`，因此当前“先补齐 tool_result 再早退/注入系统警告”的路径覆盖了 Anthropic、OpenAI、Codex 三种协议格式。

需要注意的边角：

- `build_tool_result_message()` 已经根据 session override 查 provider；这避免了主 slot 是 Anthropic，但当前 chat override 到 Codex 时仍发 `tool_result` 的问题。
- `_reactive_compact()` 仍不识别 Codex `function_call_output` item。Codex 会话里如果 prompt-too-long 由旧 tool outputs 堆积触发，reactive compact 可能清不到旧结果。
- `ConversationCompactor._prune_tool_outputs()` 只识别 OpenAI `role="tool"`，不识别 Anthropic user-content `tool_result` 和 Codex `function_call_output`；摘要前的工具输出剪枝覆盖不完整。
- `_split_system_messages()` 会把非 user/assistant/system role 映射成 Anthropic user；实际 tool result 不应走这里，而应由 `build_tool_result_message()` 构造后进入下一轮。

## A4. Context Budget 分层现状与建议

### 当前现状

Lapwing 目前没有 OpenClaw 风格的 `native contextWindow` 与 runtime `contextTokens` 双字段。

现有相关字段/机制：

- `ProviderInfo`：`reasoning_effort`、`context_compaction`，没有 `contextWindow`、`contextTokens`、`instructionMaxBytes`、`maxInputTokens`。
- `MAX_HISTORY_TURNS=20`：主聊天最多取约 40 条 user/assistant turn。
- `StateViewBuilder(history_turns=30, inner_history_turns=50)`：builder 默认窗口值与 `brain._recent_messages()` 的 20 turn 配置不是同一个来源；实际 chat path通常使用 brain 传入的 override。
- `ConversationCompactor`：按消息条数和 `COMPACTION_TRIGGER_RATIO=0.8` 触发摘要。
- `TaskRuntime`：按字符数控制工具结果，不控制全 prompt。
- `TaskBudget.max_tokens` 和 agent specs 的 `max_tokens` 多数是任务/输出预算，不是 provider runtime context cap。

### trajectory / memory 使用哪个 cap

- trajectory：主对话由 `_recent_messages()` 使用 `MAX_HISTORY_TURNS * 2`；inner loop由 `StateViewBuilder._inner_history_turns`；都不是 provider-specific context budget。
- memory snippets：`WorkingSet.retrieve(query_text, top_k=memory_top_k)`，默认 top_k=10；没有按 provider 或剩余 token 预算裁剪。
- prompt sections：identity、ambient、runtime state、memory、corrections 全部先拼出完整 system prompt，再交给 router；没有“高/中/低优先级”预算层。

### 建议方案

后续 provider 层应把“模型声明能力”和“Lapwing 实际使用预算”拆开：

1. Provider/model metadata 增加：
   - `context_window_tokens`：native context window，只描述模型声明能力。
   - `runtime_context_tokens`：Lapwing 实际使用 cap，默认小于等于 native。
   - `instruction_max_bytes`：system/instructions 字段硬上限，Codex 先设约 32 KiB。
   - `max_output_tokens`：provider/model 默认输出预算。
   - `supports_parallel_tools`、`supports_forced_tool_choice`、`tool_protocol`。
2. Router resolve 结果应携带 `(provider_id, model_id, api_type, protocol, budgets)`，不要在 auth 或 session override 阶段退化为 model name。
3. `StateSerializer` 输出分层结构，而不是只输出单个 `system_prompt` 字符串；provider adapter 再按预算装配：
   - high priority：identity / constitution / core rules。
   - medium priority：runtime state / active commitments。
   - lower priority：memory snippets / ambient entries / corrections tail。
4. 对 Codex：
   - 先检查 `instructions` UTF-8 byte length。
   - 超限时保留 high priority，裁剪 lower priority，剩余内容转成 `input` message 或摘要。
   - 任何溢出都打结构化 warning log，包含 provider/model/bytes/裁剪 section。
5. 对 trajectory 和 memory：
   - 统一引入 `PromptBudget`，从 provider/model runtime cap 推导 `system_budget`、`history_budget`、`memory_budget`、`tool_result_budget`。
   - compactor 根据预算而不是消息条数触发；reactive compact 扩展到 Anthropic/OpenAI/Codex 三种 tool-result item。

## 汇总风险清单

| 风险 | 严重度 | 说明 |
| --- | --- | --- |
| session override/provider map 用 `model_id` 单键 | 高 | 同名 model 跨 provider 时可串 provider/protocol |
| auth 层丢失显式 `provider_id` | 高 | 依赖 base URL 推断 provider，代理/空 URL/同 host 多 provider 易误判 |
| 没有 instructions/system prompt 硬上限 | 高 | Codex 32 KiB 场景会直接 400；其它 provider 也只能事后补救 |
| per-model 参数能力缺失 | 中 | 同 provider 多模型无法独立声明 tool_choice、max_tokens、parallel tools 支持度 |
| Codex runtime config 在 TOML 中但未进 settings | 中 | 配置看似可调，实际硬编码 |
| MiniMax VLM key fallback 到文本 key | 中 | VLM 与文本接口 key 不同时可能串 key |
| browser vision fallback 图片格式不跨协议 | 中 | OpenAI-compatible slot 下图片内容会被丢弃 |
| reactive/summary compaction 不识别 Codex item | 中 | Codex 工具链在 prompt-too-long 恢复路径仍可能失败 |
