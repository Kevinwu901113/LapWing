# Prompts 目录

所有 Prompt 用 Markdown 格式管理，通过 `src/core/prompt_loader.py` 在运行时加载。

## 文件命名约定

| 文件 | 用途 |
|------|------|
| `lapwing.md` | Lapwing 主人格 |
| `agent_researcher.md` | Researcher Agent 人格（Phase 2） |
| `agent_coder.md` | Coder Agent 人格（Phase 2） |
| `agent_writer.md` | Writer Agent 人格（Phase 2） |
| `agent_scheduler.md` | Scheduler Agent 人格（Phase 2） |

## 为什么用 Markdown

- **可读性好**：Markdown 自带格式，阅读和编辑都方便
- **版本管理**：可以用 git 追踪每次修改
- **解耦**：修改人格不需要改代码，重启即可生效
- **扩展性**：新增 Agent 只需添加新的 .md 文件

## 编写指南

- 用二级标题 `##` 分隔不同维度（性格、行为准则、说话风格等）
- 提供正面和反面的说话示例
- 保持简洁，避免冗余描述
- 修改后重启 Lapwing 即可生效（热加载在后续版本支持）
