# Prompts 目录

所有 Prompt 用 Markdown 格式管理，通过 `src/core/prompt_loader.py` 在运行时加载。

## 人格与身份

| 文件 | 用途 |
|------|------|
| `lapwing_soul.md` | Lapwing 核心人格（性格、关系、兴趣、成长） |
| `lapwing_voice.md` | Lapwing 说话风格与表达方式 |
| `lapwing_capabilities.md` | Lapwing 能力边界声明 |

## 自进化系统

| 文件 | 用途 |
|------|------|
| `constitution_check.md` | 宪法校验器 Prompt — 验证进化变更是否合规 |
| `correction_analysis.md` | 纠正分析 Prompt — 判断用户消息是否为纠正并提取规则 |
| `evolution_diff.md` | 进化 Diff Prompt — 基于日记和规则生成 diff 变更 |
| `self_reflection.md` | 每日自省 Prompt — 回顾对话提取学习日志 |

## 记忆系统

| 文件 | 用途 |
|------|------|
| `memory_extract.md` | 记忆提取 Prompt — 从对话中提取关键事实 |
| `heartbeat_consolidation.md` | 记忆整合 Prompt — 合并长期记忆 |
| `compaction.md` | 对话 Compaction Prompt — 压缩过长对话历史 |

## Agents

| 文件 | 用途 |
|------|------|
| `agent_dispatcher.md` | Agent 分发器决策 Prompt |
| `agent_file.md` | 文件 Agent Prompt |
| `agent_researcher.md` | Researcher Agent 人格 |
| `agent_todo.md` | Todo Agent Prompt |
| `agent_weather.md` | 天气 Agent Prompt |
| `browser_analyze.md` | 浏览器 Agent 分析 Prompt |
| `coder_generate.md` | Coder Agent 代码生成 |
| `coder_fix.md` | Coder Agent 代码修复 |
| `coder_workspace_plan.md` | Coder Agent 工作区规划 |
| `coder_workspace_fix.md` | Coder Agent 工作区修复 |
| `researcher_extract_query.md` | Researcher 查询提取 |
| `researcher_summarize.md` | Researcher 摘要 |

## Heartbeat

| 文件 | 用途 |
|------|------|
| `heartbeat_decision.md` | Heartbeat 主动行为决策 |
| `heartbeat_proactive.md` | 主动发消息 Prompt |
| `heartbeat_interest_proactive.md` | 兴趣驱动主动推送 Prompt |
| `heartbeat_autonomous_browsing.md` | 自主浏览决策 Prompt |
| `interest_extract.md` | 兴趣提取 Prompt |


