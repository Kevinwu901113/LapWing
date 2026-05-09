# Soak Test Error Analysis — 2026-05-09

Log file: `/home/kevin/lapwing/logs/lapwing.log`
Analysis window: 19:40 – 20:05 (30-min soak test period + pre-soak startup)

## Summary

190 个 WARNING/ERROR，归为 8 个独立根因。其中 2 个已在本次 session 修复，1 个是 pre-existing infra bug，5 个是外部环境/超时问题。

---

## 1. `delegate_to_researcher` — tuple indices error (20 次)

```
[tools] 工具 `delegate_to_researcher` 执行异常: tuple indices must be integers or slices, not str
```

**根因**: `delegate_to_researcher` tool executor 内部某处把 dict/tuple 搞混了。代码试图用字符串 key 索引一个 tuple（或类似 tuple 的对象）。

**影响**: 所有 research delegation 失败 → user 收到 "搜索工具又抽风了" 的 framework_fallback。Error burst guard 在连续 3 次失败后触发，停止 tool loop。

**严重度**: HIGH — 核心 research 路径完全不可用。

**修复方向**: 在 `delegate_to_researcher` executor 中定位 `result[index]` 或 `result["key"]` 的调用点，检查返回类型是否从 dict 变成了 tuple。

---

## 2. Permission denied: `/tmp/lapwing/agent_runs/` (2 次)

```
[Errno 13] Permission denied: '/tmp/lapwing/agent_runs/919231551/task_05d292d635ac'
```

**根因**: 之前 systemd service 以 `User=root` 运行，`/tmp/lapwing/agent_runs/` 下的子目录由 root 创建。切换到 `User=kevin` 后，kevin 无法在 root-owned 目录下创建子目录。

**影响**: 新 background task 无法创建 workspace → task 失败。

**严重度**: 已修复 — `chown -R kevin:kevin /tmp/lapwing/` + systemd `User=kevin`。

---

## 3. httpx 403 Forbidden (103 次)

```
httpx 抓取失败 https://www.hltv.org/... [403] strategy=direct
httpx 抓取失败 https://www.hltv.org/... [403] strategy=proxy
```

**涉及域名** (高频):
- `hltv.org` — CS 电竞数据，反爬严格
- `game-tournaments.com` — 电竞赛程
- `oddschecker.com` — 博彩赔率
- `escorenews.com` — 电竞新闻
- `betclic.com` — 博彩
- `scores24.live` — 比分
- `esports.gg` — 电竞新闻

**根因**: Research fetcher 的 User-Agent 被目标站点识别为 bot，返回 403。direct 和 proxy 两种策略都失败，说明不是 IP 问题而是 UA/请求头问题。

**影响**: Research 工具无法抓取电竞/体育类网站 → 相关查询超时返回空结果。

**严重度**: MEDIUM — 非核心功能，但影响 sports/esports 查询质量。

**修复方向**:
1. 更换 User-Agent 为最新 Chrome UA
2. 对 403 域名增加 retry-with-backoff
3. 考虑对 hltv.org 等反爬严格站点使用 browser-based fetch

---

## 4. httpx 连接失败 (13 次)

```
httpx 连接失败 https://bo3.gg/... strategy=proxy:
httpx 连接失败 https://escorenews.com/... strategy=proxy:
```

**根因**: Proxy 连接超时或被拒。部分站点 proxy 和 direct 都失败。

**影响**: 同 #3，research 查询降级。

**严重度**: LOW — 与 #3 同属 research fetcher 问题。

---

## 5. Research overall timeout (7 次)

```
research overall timeout 30s: question='Falcons vs K27 PGL Astana 2026...'
```

**根因**: Research engine 的 30s 总超时。根因是 #3 (403) + #4 (连接失败) 导致 fetch 全部超时/失败，research 引擎在 30s 内拿不到足够结果。

**影响**: 用户收到 "搜索超时" 类回复。

**严重度**: MEDIUM — 是 #3 的下游症状。

---

## 6. LLM 400 InvalidParameter (6 次)

```
LLM 调用失败: Error code: 400 - {'error': {'code': 'InvalidParameter', 'message': 'A parameter specified in the request is not valid: %s ...'}}
```

**时间分布**: 12:22, 12:28, 13:07, 13:42, 13:51, 16:19 — 全在 soak test 之前。

**根因**: LLM API 返回 400 Bad Request。`%s` 占位符未被格式化，说明错误消息是原始 API 返回。可能原因：
1. 请求体中某个参数格式不合法（如 tools schema 格式错误）
2. Context 长度超出 provider 限制
3. 特定模型 slot 不支持某些参数

**影响**: `think_inner` 失败（inner tick），不影响 foreground 对话。

**严重度**: MEDIUM — 间歇性，发生在 inner tick，不直接影响用户体验。

**修复方向**: 需要查看对应的 `llm.request` mutation log 记录，确认具体哪个参数无效。

---

## 7. tool call 循环超过上限 (13 次) + Error burst guard (11 次)

```
[runtime] tool call 循环超过上限，返回兜底说明
[runtime] Error burst guard triggered: 最近 3 次错误
```

**根因**: 这两个是 #1 (delegate_to_researcher tuple error) 的直接下游症状。连续 3 次 tool 调用失败 → burst guard 触发 → loop 终止 → "循环超过上限"。

**影响**: 用户收到兜底回复。

**严重度**: 是 #1 的症状，修复 #1 后这两个会消失。

---

## 8. foreground_turn_timed_out (1 次)

```
foreground_turn_timed_out chat_id=919231551 turn_id=evt_0d6e60d908a14336aa13e1db193c7ef9 timeout_seconds=300
```

**根因**: 单个 foreground turn 超过 300s 限制。结合时间线看，这是用户问 "道奇比赛结果" 触发的 research 查询，因 #3 (403) + #4 (连接失败) 导致 research 一直重试直到 300s 上限。

**影响**: 用户收到 "这次查询卡住了" 的超时回复。

**严重度**: 是 #3 的下游症状。

---

## 9. 其他零散

| Warning | 次数 | 说明 |
|---------|------|------|
| `genericRepeat` 警告 | 1 | `read_agent_task` 重复 10 次，loop detection 触发 |
| `browser fetch timeout` | 2 | 浏览器抓取 8s 超时 |
| `fetch overall timeout` | 3 | httpx 抓取 15s 超时 |
| `browser fetch failed` (context destroyed) | 1 | Playwright 页面导航导致 context 销毁 |
| `expression_gate` 相关 | 0 | gate 运行正常，无 warning |

---

## 优先级排序

| 优先级 | 问题 | 影响范围 | 状态 |
|--------|------|---------|------|
| P0 | #1 delegate_to_researcher tuple error | 所有 research 查询 | 待修 |
| P1 | #6 LLM 400 InvalidParameter | inner tick 间歇失败 | 待查 |
| P2 | #3+4 httpx 403/connection failure | esports/sports 查询质量 | 待改 |
| P2 | #2 Permission denied | agent_runs 目录 | **已修复** |
| P3 | #7+8 循环上限/burst guard | #1 的症状 | 修 #1 后消失 |
| P3 | #5+9 research/fetch timeout | #3 的症状 | 修 #3 后改善 |
| P3 | systemd User=root | data/logs 文件权限 | **已修复** |
