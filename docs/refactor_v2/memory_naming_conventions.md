# memory_naming_conventions.md — 记忆文件命名约定

Step 7 落地 v1.1 强制修正 #5。本文是 `data/memory/` 下所有文件命名约定的
权威来源。修改本文档前先确认向后兼容（有 production 数据时要配 migration）。

## 目录结构

```
data/memory/
├── conversations/       # 旧：对话摘要（Compactor 产出），保留
│   └── summaries/
│       └── YYYY-MM-DD_HHMMSS.md
├── notes/               # 旧：手动笔记（NoteStore 承接 9 memory 工具），保留
│   ├── <类别>/
│   └── ...
├── episodic/            # 新（Step 7）：情景层，事件级记忆
│   └── YYYY-MM-DD.md    # 按天切，内部按时间 section
└── semantic/            # 新（Step 7）：语义层，持久知识
    └── <category>.md    # 按分类切，内部按 fact section
```

## Episodic 层

### 文件名

`YYYY-MM-DD.md` — ISO 8601 日期，固定时区 `Asia/Taipei`。

- 一天多条事件全部写入同一个文件
- 日期推算来自 `occurred_at` 参数（缺省为 `datetime.now(Asia/Taipei)`）
- 文件不存在时首次写入时自动创建，带 `# YYYY-MM-DD 情景记录` 头

### 文件内容

```markdown
# 2026-04-17 情景记录

## 14:30 — Kevin 问道奇比赛

<!-- episode_id: ep_20260417_143000_a1b2c3 -->

Kevin 问了今天道奇的比赛结果。我用 research 工具查但网超时，
告诉他稍后再试。

## 18:45 — 论文讨论

<!-- episode_id: ep_20260417_184500_d4e5f6 -->

...
```

- section 标题格式：`## HH:MM — 标题`
- HH:MM 是事件发生时刻（`Asia/Taipei`）
- 标题是 LLM 提取器产出的第一句话（≤ 80 字，超长会被截断）
- `<!-- episode_id: ... -->` 是内部 id 注释，供人工回溯；LLM 读取时忽略
- body 自由文本，LLM 生成

### Episode ID

`ep_YYYYMMDD_HHMMSS_<6hex>` — `6hex` 是标题的 SHA-1 前 6 字节，防止同一秒多个事件 id 冲突。

## Semantic 层

### 文件名

`<category>.md` — category 是 slug（小写、`[a-z0-9_-]`）。Step 7 初始分类：

- `kevin.md`   — 关于 Kevin 的持久知识
- `lapwing.md` — 关于 Lapwing 自己的（自我模型）
- `world.md`   — 外部世界事实

category 可动态扩展：`SemanticDistiller` 允许 LLM 创造新 category，
`SemanticStore` 会自动 slugify 并创建对应文件。

### 文件内容

```markdown
# kevin — 语义记忆

## Kevin 每天早上喝手冲咖啡

<!-- fact_id: sem_20260418_030015_7c3a1e, created_at: 2026-04-18 03:00 -->

Kevin 每天早上喝手冲咖啡

> sources: ep_20260417_090000_a1b2, ep_20260418_090000_b3c4

## Kevin 喜欢看道奇的比赛

...
```

- section 标题：fact 的第一行（≤ 120 字截断）
- `<!-- fact_id: ..., created_at: ... -->` 注释行
- body 重复 fact 全文（支持多行）
- 如果有 `source_episodes`，写 blockquote `> sources: <ep_id>, ...`

### Fact ID

`sem_YYYYMMDD_HHMMSS_<6hex>` — `6hex` 是 fact content 的 SHA-1 前 6 字节。

## 去重

- **Episodic 不自动去重**。同一对话被提取两次 → 两条 episode，人工/运维清理
- **Semantic 写入时去重**。`SemanticStore.add_fact` 调 `vector_store.recall(..., where={"note_type": "semantic"})`
  probe top-3；任何现有 fact 与新 fact 的语义相似度 ≥ `dedup_threshold`（默认 0.85）即跳过

## 跨平台 / 编码

- 所有文件 UTF-8 编码
- 换行符 LF（Unix）
- 时区固定 `Asia/Taipei`；不在文件里存 timestamp 时区后缀，通过约定

## 文件膨胀护栏

- **Episodic**：一天一个文件。正常对话频率下单文件不会超过 100 KB
- **Semantic**：一个分类一个文件。Semantic 提炼有 dedup + daily 频率限制，
  单 category 预期不超过 200 条（~50 KB）。超过 200 条时考虑拆子目录（
  `semantic/kevin/preferences.md` + `semantic/kevin/schedule.md`）——
  这是未来 Step 的事，不在 Step 7 范畴

## 约定 vs 硬编码

本文档是**约定**，不是**契约**。源代码里：

- 路径模板在 `src/memory/episodic_store.py::EpisodicStore._day_path`
- 路径模板在 `src/memory/semantic_store.py::SemanticStore._category_path`
- 根目录 `data/memory/` 在 `config/settings.py::MEMORY_DIR`

修改约定时**同时改代码和本文档**，不能只改一边。
