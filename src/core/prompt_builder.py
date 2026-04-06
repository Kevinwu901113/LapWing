"""System prompt assembly — extracted from brain.py for clarity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.prompt_loader import load_prompt
from config.settings import (
    CHAT_WEB_TOOLS_ENABLED,
    CONVERSATION_SUMMARIES_DIR,
    KEVIN_NOTES_PATH,
    RULES_PATH,
    SHELL_DEFAULT_CWD,
    SHELL_ENABLED,
)

if TYPE_CHECKING:
    from src.core.knowledge_manager import KnowledgeManager
    from src.core.skills import SkillManager
    from src.memory.conversation import ConversationMemory
    from src.memory.vector_store import VectorStore

_RELATED_MEMORY_LIMIT = 300

_PERSONA_ANCHOR = (
    "记住：你是 Lapwing，说话像发微信，短句为主，经常用表情符号。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，想撒娇就撒，想吐槽就吐槽。做事时保持人格，不切换成工具模式。"
)


async def build_system_prompt(
    *,
    system_prompt: str,
    chat_id: str,
    user_message: str,
    memory: "ConversationMemory",
    vector_store: "VectorStore | None",
    knowledge_manager: "KnowledgeManager | None",
    skill_manager: "SkillManager | None",
    memory_index: "Any | None" = None,
) -> str:
    """Assemble layered system prompt from all context sources."""
    from src.memory.file_memory import read_memory_file, read_recent_summaries

    sections: list[str] = []

    # Layer 0: 核心人格
    sections.append(system_prompt)

    # Layer 1: 行为规则（从经验中学到的）
    rules = await read_memory_file(RULES_PATH, max_chars=800)
    if rules and "暂无规则" not in rules:
        sections.append(f"## 你从经验中学到的规则\n\n{rules}")

    # Layer 0.5: 当前时间（增强版）
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    taipei_tz = timezone(timedelta(hours=8))
    now_taipei = now_utc.astimezone(taipei_tz)

    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[now_taipei.weekday()]
    yesterday = (now_taipei - timedelta(days=1)).strftime('%m月%d日')

    sections.append(
        f"## 现在\n\n"
        f"现在是 {now_taipei.strftime('%Y年%m月%d日 %H:%M')}，{weekday}。"
        f"昨天是{yesterday}。\n"
        f"当你提到时间时请基于这个时间判断，不要凭感觉推测。"
    )

    # Layer 2: 对 Kevin 的了解（文件化记忆）
    kevin_notes = await read_memory_file(KEVIN_NOTES_PATH, max_chars=1000)
    if kevin_notes:
        sections.append(f"## 你对他的了解\n\n{kevin_notes}")

    # Layer 2.5: SQLite facts 补充
    facts = await memory.get_user_facts(chat_id)
    if facts:
        regular_facts = _split_facts(facts)
        if regular_facts:
            facts_text = "\n".join(
                f"- {fact['fact_key']}: {fact['fact_value']}" for fact in regular_facts[:10]
            )
            sections.append(
                "## 补充信息（自动提取）\n\n"
                f"{facts_text}"
            )

    # Layer 2.7: 索引化记忆（按重要性排序）
    if memory_index is not None:
        top_entries = memory_index.ranked_entries(limit=20)
        if top_entries:
            memory_lines = []
            for entry in top_entries:
                memory_index.update_referenced(entry["id"])
                memory_lines.append(f"- [{entry['category']}] {entry['content_preview']}")
            sections.append(
                "## 记忆索引（按重要性排序）\n\n"
                + "\n".join(memory_lines)
            )

    # Layer 3: 文件化对话摘要
    recent_summaries = await read_recent_summaries(CONVERSATION_SUMMARIES_DIR)
    if recent_summaries:
        sections.append(f"## 最近的对话\n\n{recent_summaries}")

    # Layer 4: 语义检索
    if user_message and vector_store is not None:
        import logging
        logger = logging.getLogger("lapwing.core.prompt_builder")
        try:
            hits = await vector_store.search(chat_id, user_message, n_results=2)
        except Exception as exc:
            logger.warning("[%s] 检索相关历史记忆失败: %s", chat_id, exc)
        else:
            related_text = _format_related_history_hits(hits, set())
            if related_text:
                sections.append(
                    "## 相关历史记忆\n\n"
                    "以下是通过语义检索找到的相关历史片段。"
                    "仅当它确实能帮助当前回复时再自然引用。\n\n"
                    f"{related_text}"
                )

    # Layer 5: 知识笔记
    if knowledge_manager is not None:
        notes = knowledge_manager.get_relevant_notes()
        if notes:
            notes_text = "\n\n".join(
                f"### {note['topic']}\n{note['content']}"
                for note in notes
            )
            sections.append(
                "## 你积累的知识笔记\n\n"
                f"{notes_text}"
            )

    # Layer 6: 技能目录
    if skill_manager is not None and skill_manager.has_model_visible_skills():
        skills_catalog = skill_manager.render_catalog_for_prompt()
        if skills_catalog:
            sections.append(
                "## 可用技能目录\n\n"
                "以下是当前可用的技能，你可以在确实需要时调用 `activate_skill` 按需加载。\n\n"
                f"{skills_catalog}"
            )

    # Layer 7: 能力描述与工具状态
    sections.append(load_prompt("lapwing_capabilities"))

    if user_message:
        sections.append(_tool_runtime_instruction())

    return "\n\n".join(sections)


def inject_voice_reminder(messages: list[dict]) -> None:
    """深度注入 voice reminder（+ 对话较长时附加 persona anchor）。

    - 对话 >= 6 条：voice + anchor 合并注入在 depth-3
    - 对话 >= 4 条：仅 voice 注入在 depth-2
    - 对话更短：追加到 system prompt
    """
    voice_reminder = load_prompt("lapwing_voice")
    if len(messages) >= 6:
        content = f"[System Note]\n{voice_reminder}\n\n{_PERSONA_ANCHOR}\n[/System Note]"
        messages.insert(len(messages) - 2, {"role": "user", "content": content})
    elif len(messages) >= 4:
        voice_msg = {"role": "user", "content": f"[System Note]\n{voice_reminder}\n[/System Note]"}
        messages.insert(len(messages) - 2, voice_msg)
    else:
        messages[0]["content"] = messages[0]["content"] + "\n\n" + voice_reminder


def _split_facts(facts: list[dict]) -> list[dict]:
    return [
        fact for fact in facts
        if not str(fact.get("fact_key", "")).startswith("memory_summary_")
    ]


def _truncate_related_memory(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _RELATED_MEMORY_LIMIT:
        return stripped
    return stripped[: _RELATED_MEMORY_LIMIT - 3].rstrip() + "..."


def _format_related_history_hits(
    hits: list[dict],
    existing_dates: set[str],
) -> str:
    lines: list[str] = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        text = _truncate_related_memory(str(hit.get("text", "")))
        if not text:
            continue

        date_str = str(metadata.get("date", "")).strip()
        if date_str and date_str in existing_dates:
            continue

        if date_str:
            lines.append(f"- {date_str}: {text}")
        else:
            lines.append(f"- {text}")

    return "\n".join(lines)


def _tool_runtime_instruction() -> str:
    """返回动态运行时状态说明（工具开关、当前目录等）。"""
    sections: list[str] = []

    if SHELL_ENABLED:
        sections.append(
            "## 本地执行状态\n\n"
            f"Shell 工具已启用（execute_shell、read_file、write_file）。\n"
            f"当前工作目录：{SHELL_DEFAULT_CWD}"
        )
    else:
        sections.append(
            "## 本地执行状态\n\n"
            "Shell 工具当前已禁用。如果被要求执行命令或修改本地文件，必须明确说明执行功能已关闭，不能编造结果。"
        )

    if CHAT_WEB_TOOLS_ENABLED:
        sections.append(
            "## 联网状态\n\n"
            "联网工具已启用（web_search、web_fetch）。"
        )
    else:
        sections.append(
            "## 联网状态\n\n"
            "联网工具当前已禁用。若被要求查询最新网页信息，需明确说明无法联网检索。"
        )

    return "\n\n".join(sections)
