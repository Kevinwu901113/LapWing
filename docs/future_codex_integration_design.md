# 未来 Codex OAuth 集成设计约束

> 状态：设计备忘，非当前实现。Codex OAuth 接入 deprioritized 为"有空再做"。本文档记录届时开工必须遵守的约束。

## 约束一：Provider Namespace 物理切分

`openai/*`（API key + `api.openai.com/v1/responses`）与 `openai-codex/*`（OAuth + `chatgpt.com/backend-api/codex/responses`）必须注册为**两个独立的 Provider**，不是同一 provider 下的不同 model。base URL、auth 方法、API schema 全部独立。  
**依据**：OpenClaw issue #38706——一旦让 Codex 模型流回 Platform endpoint，OAuth token 因缺 `api.responses.write` scope 返回 401。

## 约束二：Instructions 字段 ~32 KiB 硬上限

Codex native responses 的 `instructions` 字段有 ~32 KiB 上限（对齐 OpenAI Codex CLI 的 `project_doc_max_bytes` 默认值）。Provider 层必须：
- 对 instructions 做 size check
- 超限时截断低优先级片段 + 把超出部分挪到 `input` messages 数组（messages limit 远大于 instructions）
- 不能硬塞  
**依据**：OpenClaw issue #57930——172 KB 请求体直接 400 Bad Request，30K/58K 二分确认上限 ~32 KiB。

## 约束三：工具协议严格对齐

Codex 会话必须用 Responses item 格式：

```json
{"type": "function_call_output", "call_id": "call_123", "output": "..."}
```

不能混入 Anthropic 的 `tool_result` 格式（`{"type":"tool_result","tool_use_id":...}`）——Codex 会 400。Provider 层必须按 `(provider, protocol)` 做格式 adapter。  
**依据**：OpenClaw OpenResponses API 文档。

## 约束四：OAuth Credential 独立存储

走独立的 PKCE 浏览器登录 / 设备码流程，**不**复用 Codex CLI 的 `~/.codex` auth store。每次刷新用文件锁防并发污染。  
**依据**：OpenClaw 最近 release note 明确砍掉了 CLI auth import path，改为 browser login / device pairing。

## 约束五：Transport 层保持简单

不抄 WebSocket-first + SSE fallback + cooldown 那套。Lapwing 主通道是 QQ + httpx/SSE 直连，WebSocket 边际收益低。保持 SSE 单路径。

## 参考链接

- OpenClaw OAuth: https://docs.openclaw.ai/concepts/oauth
- OpenClaw OpenAI Provider: https://docs.openclaw.ai/providers/openai
- OpenResponses API: https://docs.openclaw.ai/gateway/openresponses-http-api
- Issue #38706 (wrong endpoint 401): https://github.com/openclaw/openclaw/issues/38706
- Issue #57930 (instructions size cap): https://github.com/openclaw/openclaw/issues/57930
