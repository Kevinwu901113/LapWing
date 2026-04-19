# step7_memory_reuse.md — RAPTOR 记忆树的复用决策

Step 7 目标：给 Lapwing 加分层长期记忆 — Episodic + Semantic + WorkingSet。本文
评估现有 `src/memory/` 模块能不能直接用、要不要改造、要不要新建，以及嵌入模型、
提取触发点的决策。

## §1 — 现状清单（Step 6 完成后）

| 文件 | 行数 | 职责 | 状态 | Step 7 处置 |
|---|---|---|---|---|
| `src/memory/vector_store.py::VectorStore` | ~150 | 每 chat 一个 Chroma collection；只写不查（`brain.clear_all_memory` 调 `delete_chat`） | 活跃但孤立 | **废弃**。`VectorStore`（每 chat）从未参与语义检索路径，`brain.clear_all_memory` 是唯一读点。Step 7 用 `MemoryVectorStore` 承接；`clear_chat` 改为按 `source_chat_id` 从单一 collection 清掉元数据匹配的条目 |
| `src/memory/vector_store.py::MemoryVectorStore` | ~250 | 单一 `lapwing_memory` collection；有综合评分（语义+时间衰减+信任+摘要深度+访问频次）+ 簇去重 | **复用核心** | 作为 Episodic/Semantic 共享的底层 Chroma 客户端。扩展：支持 `where` 元数据过滤，让 `query(note_type='episodic')` / `query(note_type='semantic')` 可用 |
| `src/memory/note_store.py` | ~300 | Markdown + YAML frontmatter 笔记文件管理 | 活跃（write_note / recall 工具） | **保留**。继续承接 LLM 主动笔记（9 工具）。Episodic/Semantic 走独立文件结构，不走 NoteStore |
| `src/memory/conversation.py` | ~375 | SQLite + 内存缓存；trajectory dual-write；todos/reminders facade | 活跃（legacy facade） | **保留不动**。Step 3 D-1（user_facts facade）+ D-2（todos/reminders facade）仍是 Step 7 之外的 debt。Step 7 不合并 user_facts → SemanticStore，理由见 §4 |
| `src/memory/compactor.py` | ~190 | Trajectory 窗口压缩摘要 | 活跃 | **保留**。trajectory compaction 和 Episodic 是互补的——compaction 处理的是窗口截短后的消息摘要（写回 cache / summaries/），Episodic 处理的是事件级记忆（独立 markdown）。两者共存 |
| `src/memory/embedding_worker.py` | ~46 | NoteStore pending note → MemoryVectorStore 异步后台嵌入 | 活跃 | **保留不动**。服务于 NoteStore。Episodic/Semantic 走同步嵌入（写入时直接 `await add`），理由：Episodic 提取频率低（每对话 1 次），没有吞吐压力 |

**计划提及但实际不存在的文件**（Step 指令写成"可能已有"）：

- `memory_index.py`（JSON 索引）— 不存在。Step 2f 起 trajectory 接手索引职责
- `auto_extractor.py`（自动记忆提取管线）— 不存在，被 compactor 内联调用但对象不存在。`compactor.py:62` 的 `auto_memory_extractor` 参数在 `AppContainer._configure_brain_dependencies` 里没人注入（始终是 None），代码死分支
- `fact_extractor.py`（LLM 事实提取）— 不存在
- `file_memory.py`（KEVIN.md / SELF.md 读取）— 不存在。KEVIN.md / SELF.md 在 `data/memory/` 里不存在（`ls data/memory/` 只有 `conversations/` 和 `notes/`），Step 3 已改走 `data/identity/soul.md`
- `user_facts.py` / `interest_tracker.py` / `discoveries.py` — 不存在，只有 `conversation.py` 里定义的同名 SQLite 表（`user_facts` / `discoveries` / `interest_topics`），且这些表在 Step 1-6 全程没有新写入。DB 里可能有旧数据，代码路径已死

**Cleanup_report_step6 §8 登记的 debt**：
- `MemorySnippets` 占位符（Step 3 C）— **Step 7 核心目标，M2 解决**
- `ConversationMemory.user_facts` facade（Step 3 D-1）— Step 7 **不清**，见 §4
- `ConversationMemory.reminders/todos` facade（Step 3 D-2）— Step 7 **不清**，不在记忆树范畴
- `compactor._auto_memory_extractor` 死参数 — Step 7 **清掉**（M4）
- SQLite 里的 `user_facts` / `discoveries` / `interest_topics` 表 — Step 7 **标死**（M4），保留 schema 避免 migration 风险，但代码里不再引用

## §2 — 嵌入模型决策

| 选项 | 优 | 劣 | 判 |
|---|---|---|---|
| ChromaDB 默认 (all-MiniLM-L6-v2) | 已经在用，CPU 可跑，PVE 够力；384 维，够语义检索用 | 英文强于中文，但 Lapwing 对话中英混杂问题不大 | **✓ 保留** |
| MiniMax embedding API | 对中文更好 | 增加 API 依赖 + 费用；每条记忆都要一次 API 调用；离线不能用 | ✗ |
| 本地 sentence-transformers (多语种模型) | 质量好于 default | PVE 无 GPU，推理慢；包体大；现有 ChromaDB 集合维度 384，换模型要重建 | ✗ |

**决策：继续用 ChromaDB 默认 all-MiniLM-L6-v2。** 现有 `MemoryVectorStore` 的嵌入路径不改。
如果后续中文检索质量不足，单独出一个 issue 评估切换成本。

## §3 — 架构决策

### §3.1 — 单 collection vs 多 collection

EpisodicStore / SemanticStore 都用 `MemoryVectorStore` 底下的同一个 `lapwing_memory` collection，
通过 metadata `note_type` 字段区分：

```
lapwing_memory (Chroma collection)
├── note_type="episodic" → Episodic 条目
├── note_type="semantic" → Semantic 条目
├── note_type="observation"/"reflection"/"fact"/"summary" → 现有 NoteStore 手动写入
```

理由：
- 现有 9 工具的 `recall` 默认查全量 collection，切换到多 collection 会破坏 recall 语义
- ChromaDB `collection.query(where={...})` 原生支持 metadata filter，性能够
- 单一 collection 减少 Chroma 初始化开销和磁盘占用

**风险**：元数据 filter 可能在不同 chromadb 版本有不同行为。Step 7 测试里覆盖 filter 路径。

### §3.2 — 文件组织

```
data/memory/
├── conversations/     # 现有，对话摘要 (compactor)
├── notes/             # 现有，NoteStore 手动笔记
├── episodic/          # 新增
│   ├── 2026-04-17.md  # 一天一个文件，内部按 section 组织
│   └── 2026-04-18.md
└── semantic/          # 新增
    ├── kevin.md       # 一个分类一个文件
    ├── lapwing.md
    └── world.md
```

Episodic：按天切文件。一天的多条事件写进同一个 `.md`，每条为一节（`## HH:MM — 标题`）。
- 文件小（一天几十到几百行）不会膨胀成巨文件
- 方便人工浏览/编辑（Lapwing 的记忆对 Kevin 是透明的）
- 文件名 ISO 8601（`YYYY-MM-DD.md`），跨时区固定为 `Asia/Taipei`

Semantic：按分类切文件。分类由 LLM 在提炼时决定（`kevin` / `lapwing` / `world` 这种大类，具体清单见 §3.4）。
- 每个 fact 是一个 section，section 标题是 fact 的一句话摘要
- 可能扩展分类 → 子目录（未来可能 `semantic/kevin/preferences.md`，Step 7 暂不启用）

### §3.3 — 写入路径

**Episodic 提取触发**：`CONSCIOUSNESS_CONVERSATION_END_DELAY` 后对话结束回调里触发一次。
- 复用 brain 现有 `_schedule_conversation_end`，它已经在对话静默一段时间后 fire
- 在该回调里增加 `await episodic_extractor.extract_from_chat(chat_id)`
- fire-and-forget (`asyncio.create_task`)，不阻塞下一条消息

**Semantic 提炼触发**：InnerTickScheduler 慢节拍（每日一次）扫描最近 N 天 Episodic，
- 在 `MaintenanceTimer` 新增一个 daily 任务
- 或在 inner tick 里检测 `episodic_count_since_last_distill > N`（阈值 20）触发

**决策：两个都做。**
- Episodic → `_schedule_conversation_end` 回调（对话结束 delay 后）
- Semantic → `MaintenanceTimer` 每日任务

这样 Semantic 和 Episodic 解耦：Semantic 不依赖对话事件频率，Episodic 不依赖定时器。

### §3.4 — 提取器设计

`EpisodicExtractor`：
- 输入：最近 trajectory 窗口（`relevant_to_chat(chat_id, n=N)`）
- LLM 调用：用 `memory_processing` slot（tool 模型），prompt 在 `prompts/episodic_extract.md`
- 输出：`{date, summary, source_trajectory_ids}`
- 写入：EpisodicStore.add_episode()

`SemanticDistiller`：
- 输入：最近 N 天 Episodic 条目
- LLM 调用：`memory_processing` slot，prompt `prompts/semantic_distill.md`
- 输出：`[{category, content, source_episodes}]`
- 去重：写入前先 `SemanticStore.query(content, top_k=3)` 检查相似度 > 阈值的已存在条目，跳过
- 写入：SemanticStore.add_fact()

**失败兜底**：提取/提炼失败不 retry，记 log，下一轮再来。记忆系统的数据丢失容忍度高。

### §3.5 — WorkingSet 检索策略

```
retrieve(query_text, trajectory_window=None, top_k=10) -> MemorySnippets:
  episodic_hits = EpisodicStore.query(query_text, top_k=top_k // 2)
  semantic_hits = SemanticStore.query(query_text, top_k=top_k // 2)
  merged = sorted(episodic_hits + semantic_hits, key=score, reverse=True)
  return MemorySnippets(snippets=merged[:top_k])
```

`query_text` 的来源：
- `build_for_chat`：取 trajectory window 最后 1-3 条 user/assistant 的 content 拼起来
- `build_for_inner`：取 AttentionState.context 或最近 trajectory 的 1 条

Top-K 默认 10（5 episodic + 5 semantic）。配置名 `MEMORY_WORKING_SET_TOP_K`，env gate。

### §3.6 — StateView.memory_snippets 渲染

现有 `StateSerializer` 应该已经对 `memory_snippets` 字段有 placeholder 渲染（Step 3 搭骨架时）。
M2.c 验证并补全。渲染格式建议：

```
## 相关记忆

- [情景] 4/17 — Kevin 问了道奇比赛，我查了但超时
- [知识] Kevin 喜欢看道奇比赛
- ...
```

score 不显示给模型（是内部排序信号）。

## §4 — 为什么不把 `user_facts` 合并进 SemanticStore

`ConversationMemory.user_facts` 是 Step 3 登记的 facade（D-1）。方案两条：

1. **合并到 SemanticStore**：所有 user fact 改走 SemanticStore，删掉 user_facts 表
2. **共存保留**：user_facts facade 继续给 legacy 代码路径用，SemanticStore 做新的语义记忆

Step 7 选 **方案 2（共存保留）**。理由：

- `ConversationMemory.user_facts` 在代码里**没人写**（grep 只在 facade 定义处；没有 `self.memory.user_facts`），
  DB 表里也没增量。它是死 schema。
- SemanticStore 不需要接手 user_facts 的"每条只记一个 (key, value) 对"的结构化模型——
  SemanticStore 是自由文本语义记忆，两者职责不重合
- 删 user_facts 表需要 migration（动 lapwing.db 的 schema）。Step 7 不做 DB schema 变更
- 把 user_facts 标记为 "Step 7 确认死代码" 写进 cleanup_report §8，等后续专门做一次 memory
  schema 清理（可能是 Step 8）

## §5 — Step 7 改动范围

**新增**：
- `src/memory/episodic_store.py` — 日志式情景记忆
- `src/memory/semantic_store.py` — 类别化语义记忆
- `src/memory/working_set.py` — 两层合并检索
- `src/memory/episodic_extractor.py` — LLM 驱动的对话 → 事件提取
- `src/memory/semantic_distiller.py` — LLM 驱动的事件 → 知识提炼
- `prompts/episodic_extract.md` — Episodic 提取 prompt
- `prompts/semantic_distill.md` — Semantic 提炼 prompt
- `docs/refactor_v2/memory_naming_conventions.md` — 命名约定
- `docs/refactor_v2/cleanup_report_step7.md` — 10 节总结

**改动**：
- `src/memory/vector_store.py::MemoryVectorStore` — 新增 `where` filter 参数
- `src/core/state_view_builder.py` — `build_for_chat` / `build_for_inner` 接 WorkingSet
- `src/core/state_serializer.py` — 确认 `memory_snippets` 渲染（估计已有骨架，补细节）
- `src/core/brain.py` — `_schedule_conversation_end` 里加 episodic 提取触发
- `src/core/maintenance_timer.py` — 每日任务里加 semantic 提炼触发
- `src/app/container.py` — 装配 EpisodicStore / SemanticStore / WorkingSet，注入 state_view_builder
- `src/memory/compactor.py` — 删 `_auto_memory_extractor` 死参数
- `config/settings.py` — `MEMORY_WORKING_SET_TOP_K` / `EPISODIC_EXTRACT_ENABLED` / `SEMANTIC_DISTILL_ENABLED`

**不改**：
- `NoteStore` + 9 memory 工具（recall / write_note / ...）
- `ConversationMemory` 主体
- 现有 `VectorStore`（per-chat）— 标记为死代码，等 Step 8 彻底删，这步先保留 `delete_chat` 路径避免破坏 `brain.clear_all_memory`

## §6 — 风险登记

| 风险 | 缓解 |
|---|---|
| ChromaDB `where` filter 在不同版本行为不一致 | 测试里覆盖 filter 路径；如果 API 变更在 M1.b/c 发现立刻降级为 Python 端 post-filter |
| Episodic 提取失败静默丢失 → 记忆空洞 | 失败记 error log；cleanup_report §8 登记为 known degradation |
| Semantic 重复写入 → collection 膨胀 | 写入前先 `query(top_k=3)` 检查相似度；阈值 0.85 跳过 |
| `memory_snippets` 填充后 system prompt 急剧变长 → token 超限 | 每条 snippet content 截断到 300 字符；total_chars 上限 2000 |
| `MemoryVectorStore` 现有 recall 路径在加 `where` filter 后回归 | M1.e 回归测试覆盖 |
| 对话结束触发的异步任务在进程退出时被吞 | `_schedule_conversation_end` 的任务注册到 brain，shutdown 时 cancel + await |
