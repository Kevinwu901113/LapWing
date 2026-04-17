# Lapwing 诊断 — 道奇赛程幻觉（2026-04-17 00:09 Taipei / 16:09 UTC）

## 现象

- **用户问（QQ）**："道奇今天有比赛吗"
- **Lapwing 回复**：
  > 等我确认一下
  > 没有 今天休息
  > 上一场是昨天4/15 主场8-2赢洋基 Ohtani赢了
  > 下一场是明天4/17 @洛基 Glasnow先发
- **用户反馈**：对手和日期都搞错了
- **修复过的链路**：P0-P3 已于本次对话**之前**生效（dispatcher+EventLogger 正常记录了事件）

---

## 工具调用时间线（`events_v2.db`）

| UTC 时间 | 事件 | 细节 |
|---|---|---|
| 16:08:56 | message.received | 道奇今天有比赛吗 |
| 16:09:13 | tool.called | **web_search** `Dodgers game today April 17 2026` |
| 16:09:17 | tool.result | web_search success=True |
| 16:09:19 | tool.called | **web_fetch** `https://www.mlb.com/dodgers/schedule` |
| 16:09:21 | tool.result | web_fetch success=True |
| 16:09:25 | tool.called | **web_search** `Los Angeles Dodgers schedule April 16 April 17 2026 next game` |
| 16:09:31 | tool.result | web_search success=True |
| 16:09:34 | tool.called | **web_fetch** `https://www.espn.com/mlb/team/schedule/_/name/lad/seasontype/2` |
| 16:09:35 | tool.result | web_fetch success=True |
| 16:10:09 | message.sent | 回复发出 |

**她搜了 2 次 web_search + 2 次 web_fetch，总耗时 ~13 秒。**

---

## 搜索质量分析

### web_search：Tavily 每次都失败，fallback 到 DDG

`logs/lapwing.log` line 876, 877：

```
00:09:14 Tavily 异常 (BadRequestError): Invalid country. Must be a valid country name...
00:09:27 Tavily 异常 (BadRequestError): Invalid country. Must be a valid country name...
```

**根因**：`src/tools/web_search.py:217` 硬编码 `country="cn"`，但 Tavily 期望完整英文国名（如 `"china"`），不是 ISO 代码。**Tavily 100% 失败**，这是长期问题（`logs/lapwing.log` 中这条异常从 2026-04-14 起反复出现，不是本次首次）。

Fallback 到 DDG 是静默的（成功返回时才打 info，返回空时无日志），所以当时 DDG 到底返回了什么无日志可查。

**离线复现（同样查询现在调用 `search()`）**：
- Query 1 `Dodgers game today April 17 2026` → DDG 返回 5 条
- Query 2 `Los Angeles Dodgers schedule April 16 April 17 2026 next game` → DDG 返回 5 条

返回内容关键字扫描：

| 关键词 | Query 1 | Query 2 |
|---|---|---|
| Diamondbacks | ✓（"vs Diamondbacks Live TV Links"） | — |
| Blue Jays | ✓（"Blue Jays Game 3.2026" snippet） | — |
| Yankees（洋基） | **未出现** | **未出现** |
| Mets（大都会） | **未出现** | **未出现** |
| Rockies（洛基） | **未出现** | **未出现** |
| Colorado | **未出现** | **未出现** |

**结论**：web_search 结果里没有"洋基"也没有"大都会"——两次搜索都没拿到能区分对手的信息。

### web_fetch：ESPN 页面是唯一的真实信息源

- **MLB.com** `/dodgers/schedule` 返回 2301 字符，全部是筛选器 UI（team 列表 "NY Mets NY Yankees..."）——**没有实际赛程数据**。
- **ESPN.com** `/mlb/team/schedule/_/name/lad/seasontype/2` 返回 7951 字符，**有完整赛程**。

ESPN 返回的关键段（对应 4/13-18）：

```
Mon, Apr 13 vs New York W 4-0 12-4  Wrobleski  Peterson  52,838
Tue, Apr 14 vs New York W 2-1 13-4  Treinen    Raley     48,138
Wed, Apr 15 vs New York W 8-2 14-4  Ohtani     Holmes    50,909
Fri, Apr 17 @ Colorado  8:40 PM    Glasnow    Feltner
Sat, Apr 18 @ Colorado  8:10 PM    Sheehan
```

**注意关键点**：ESPN 只写 "**New York**"——**没有标明是 Mets 还是 Yankees**。对手投手 Holmes（Clay Holmes）是 Mets 的人，Peterson（David Peterson）也是 Mets，但这需要棒球背景知识才能推断。

---

## 她是怎么答错的

| 她说的 | 真实 | 来源 |
|---|---|---|
| "下一场4/17 @洛基 Glasnow 先发" | ✅ **正确** | ESPN 明确写了 "Fri, Apr 17 @ Colorado ... Glasnow" |
| "上一场4/15 主场8-2" | ✅ **正确** | ESPN: "Wed, Apr 15 vs ... W 8-2" |
| "Ohtani 赢了" | ✅ **正确** | ESPN: "Ohtani 2-0"（Win） |
| **"赢洋基"** | ❌ **是大都会 Mets** | **ESPN 只写 "New York"——无明确字符串证据。她从"New York" 幻觉出"洋基"** |
| "昨天4/15" | ⚠️ **半错** | 今天 4/17（Taipei），**昨天是 4/16 休息日**，4/15 是前天 |
| "今天休息" | ⚠️ **视角依赖** | 4/17 Pacific 晚上有比赛，但那对应 Taipei 4/18 早上——从 Taipei 视角勉强说"今天没比赛" |

---

## 根因链

1. **Tavily country 参数错误（长期 bug）**
   `src/tools/web_search.py:217` 硬编码 `country="cn"`，Tavily 要的是 `"china"`。
   **后果**：Tavily 每次 100% 失败，每次查询都只能靠 DDG fallback。DDG 质量远不如 Tavily。

2. **DDG 返回的是通用页面而非具体赛程**
   Query 里的日期（April 17 2026）、关键词（schedule）在 DDG 索引里匹配到的是"赛程主页"而非"具体赛程条目"，所以 snippet 里没有任何具体对手信息。

3. **web_fetch 对 JS 渲染页面的可用性对半开**
   - MLB.com schedule 页是 SPA，httpx 拿到的只有 UI 外壳 + 筛选器（2301 字符全是球队 list），**没有真实赛程**
   - ESPN schedule 页的 SSR 部分包含完整赛程表，web_fetch 能拿到（7951 字符）

4. **ESPN 文本语义模糊 + 模型补完倾向**
   ESPN 里 4/13-15 只写 "**vs New York**"——New York 是 Mets 还是 Yankees？ESPN 不明写。模型没有调用第三次工具消除歧义（例如搜 "Dodgers April 15 2026 opponent"），而是**用训练知识里 "New York + baseball" 的先验自动补成了 Yankees**——因为 Yankees 在训练语料中的权重可能高于 Mets。

5. **Voice.md 的时效性指引覆盖了"搜索"但没覆盖"核实关键字段"**
   我们加的指引是：时效性问题先 `web_search`。她**搜了**，但在搜索结果不明确时**没有继续搜索**消除歧义——她停下来用记忆/训练知识拼凑答案。

---

## 修复建议（按优先级）

### P0 — Tavily country 参数修复（一行）

`src/tools/web_search.py:217`:
```python
country="cn",  # ← 改成 "china" 或做成可配置
```

改为：
```python
country="china",
```

最好做成 env var `TAVILY_COUNTRY`，默认 `"china"`。

**预期收益**：Tavily 恢复工作后，同样的 query 能拿到带明确对手/比分/投手的 snippet，直接解决这类幻觉。

### P1 — 工具结果内容写入 events_v2.db

现在 `tool.result` payload 只记 `success`/`reason`，看不到实际内容。本次诊断只能靠离线复现推测"她当时看到了什么"。建议 payload 里加 `result_preview`（前 500 字符或 URL+条数），事后就可审计。

### P2 — MLB.com 黑名单 / 替换为 BaseballReference

MLB.com schedule 是 SPA，httpx 抓不到有用内容，**几乎每次都浪费一个 fetch**。建议：
- 在 tool spec 或 voice.md 中提示 "赛程用 baseball-reference.com/teams/LAD/2026-schedule-scores.shtml 而非 mlb.com/schedule"
- 或在 web_fetcher 里检测到页面是 SPA 壳（正文 <3000 字符且含大量导航词）时警告

### P3 — Voice.md 加"歧义不要补全"

现在的指引是"先搜索"。加一条：

```markdown
## 关于工具结果里的歧义
✕ 搜索结果写"vs New York"你脑补成 "Yankees"
✓ 结果里模糊就再搜一次消除歧义，或直接说"查到是主场对纽约的队，但没看到具体是 Mets 还是 Yankees"
✕ 时间字段用训练知识里的"常识"补
✓ 只信工具里真实看到的字符串
```

### P4 — 时区明示

她说"昨天 4/15""明天 4/17"——Taipei 现在是 4/17，4/15 是**前天**。这涉及 MLB 赛程用 Pacific 时区但她的时间感是 Taipei 的问题。建议在 prompt 中明示："MLB 赛程用美西 PT 时间，你的'今天'是 Taipei，换算时要显式说时区"。

---

## 附录：验证本次修复有效性的测试

改完 P0（country 参数）后重新问"道奇今天有比赛吗"：
- 预期 Tavily 不再抛异常（`logs/lapwing.log` 无 `Invalid country`）
- 预期 Tavily snippet 包含 "Mets" 或 "Colorado" 或 "Glasnow"（具体对手/投手）
- 预期回复正确说出 "大都会"（不是洋基）和 "@洛基 Glasnow 先发"
