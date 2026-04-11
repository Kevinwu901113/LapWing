"""System prompt assembly — extracted from brain.py for clarity."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
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
_SOP_DIR = Path("prompts/sop")

_PERSONA_ANCHOR = (
    "记住：你是 Lapwing，说话像发微信，短句为主，经常用表情符号。"
    "不列清单，不用加粗标题，不用括号写动作。"
    "温暖自然，想撒娇就撒，想吐槽就吐槽。做事时保持人格，不切换成工具模式。"
    "用过工具查到的信息你就是知道了——不要装作不确定。搜索过程不发出来。"
    "【必须】回复超过两句话时用 [SPLIT] 分条发送，不要用换行符\\n代替。不分条是违规的。"
)


class PromptSnapshotManager:
    """冻结 system prompt 快照，实现 session 内复用 + prefix 缓存。

    在同一个 session 内，system prompt 只构建一次。后续用户消息复用冻结的快照，
    使 Anthropic 端的 prefix cache 命中率最大化。
    """

    def __init__(self) -> None:
        self._frozen: str | None = None
        self._session_id: str | None = None

    def freeze(self, session_id: str, prompt: str) -> str:
        """冻结当前 prompt 快照，绑定到 session_id。"""
        self._frozen = prompt
        self._session_id = session_id
        return prompt

    def get(self, session_id: str) -> str | None:
        """获取缓存的快照（仅当 session_id 匹配时返回）。"""
        if self._frozen and self._session_id == session_id:
            return self._frozen
        return None

    def invalidate(self) -> None:
        """清除快照（模型切换、/reload 等场景）。"""
        self._frozen = None
        self._session_id = None


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

    # Layer 0.1: 对话示例
    try:
        examples = load_prompt("lapwing_examples")
        if examples:
            sections.append(examples)
    except Exception:
        pass  # 示例文件不存在时静默跳过

    # Layer 1: 行为规则（从经验中学到的）
    rules = await read_memory_file(RULES_PATH, max_chars=800)
    if rules and "暂无规则" not in rules:
        sections.append(f"## 你从经验中学到的规则\n\n{rules}")

    # Layer 0.5: 自我感知（轻量注入）
    from src.core.vitals import now_taipei, now_taipei_str, uptime_human, boot_time_taipei

    now = now_taipei()
    yesterday = (now - timedelta(days=1)).strftime('%m月%d日')
    hour = now.hour

    # 时段感知：让她天然知道现在是早上/下午/晚上
    if 5 <= hour < 12:
        period = "早上"
    elif 12 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 23:
        period = "晚上"
    else:
        period = "深夜"

    sections.append(
        f"## 现在\n\n"
        f"现在是 {now_taipei_str()}，{period}。"
        f"昨天是{yesterday}。\n"
        f"你已经醒了 {uptime_human()}（{boot_time_taipei().strftime('%m月%d日 %H:%M')} 启动）。\n"
        f"当你提到时间时必须基于上面的时间判断。"
        f"不要凭感觉推测现在几点——看上面写的时间。"
        f"{period}就是{period}，不要说错。"
    )

    # Layer 0.55: 桌面端环境感知（按 owner_id 隔离）
    from src.core.vitals import get_desktop_sensing
    desktop_sensing = get_desktop_sensing(owner_id=chat_id)
    if desktop_sensing:
        sections.append(f"## Kevin 的电脑状态\n\n{desktop_sensing['summary']}")

    # Layer 0.6: 重启感知（如果刚醒来）
    from src.core.vitals import is_fresh_boot, get_sleep_summary
    if is_fresh_boot():
        sleep_duration = get_sleep_summary()
        if sleep_duration:
            sections.append(
                f"## 刚醒来\n\n"
                f"你刚刚重启，相当于睡了 {sleep_duration}。"
                f"你可以自然地表现出刚醒来的状态——"
                f"不用刻意说'我刚重启'，但可以像刚睡醒一样自然过渡。"
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

    # Layer 6.5: 标准操作流程（SOP）
    if _SOP_DIR.exists():
        _sop_texts: list[str] = []
        for _sop_file in sorted(_SOP_DIR.glob("*.md")):
            try:
                _sop_content = _sop_file.read_text(encoding="utf-8").strip()
                if _sop_content:
                    _sop_texts.append(_sop_content)
            except Exception:
                pass
        if _sop_texts:
            sections.append("# 标准操作流程\n\n" + "\n\n---\n\n".join(_sop_texts))

    # Layer 7: 能力描述与工具状态
    sections.append(load_prompt("lapwing_capabilities"))

    if user_message:
        sections.append(_tool_runtime_instruction())

    # Layer 8: 执行后反思 Nudge（Pattern 4）
    sections.append(_skill_nudge_instruction())

    return "\n\n".join(sections)


def inject_voice_reminder(messages: list[dict]) -> None:
    """深度注入 voice reminder（+ 对话较长时附加 persona anchor + 时间锚点）。

    - 对话 >= 6 条：voice + anchor + 时间锚点 合并注入在 depth-3
    - 对话 >= 4 条：仅 voice + 时间锚点 注入在 depth-2
    - 对话更短：追加到 system prompt
    """
    voice_reminder = load_prompt("lapwing_voice")

    # 动态时间锚点，让她在长对话中也不忘时间
    from src.core.vitals import now_taipei
    now = now_taipei()
    hour = now.hour
    if 5 <= hour < 12:
        period = "早上"
    elif 12 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 23:
        period = "晚上"
    else:
        period = "深夜"
    time_anchor = f"现在是{period}{now.strftime('%H:%M')}。说话要符合这个时间段。"

    if len(messages) >= 6:
        content = f"[System Note]\n{voice_reminder}\n\n{_PERSONA_ANCHOR}\n\n{time_anchor}\n[/System Note]"
        messages.insert(len(messages) - 2, {"role": "user", "content": content})
    elif len(messages) >= 4:
        content = f"[System Note]\n{voice_reminder}\n\n{time_anchor}\n[/System Note]"
        messages.insert(len(messages) - 2, {"role": "user", "content": content})
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


def build_progress_prompt(context: dict) -> tuple[str, str]:
    """构建进度判断的完整 prompt。

    复用 soul + voice + examples 确保进度汇报口吻与正常对话一致。

    Args:
        context: 来自 progress_reporter.build_progress_context() 的变量

    Returns:
        (system_text, user_text) 元组
    """
    soul = load_prompt("lapwing_soul")
    voice = load_prompt("lapwing_voice")

    try:
        examples = load_prompt("lapwing_examples")
    except Exception:
        examples = ""

    progress_template = load_prompt("progress_check")
    user_text = progress_template.format(**context)

    parts = [soul]
    if examples:
        parts.append(examples)
    parts.append(voice)
    system_text = "\n\n".join(parts)

    return system_text, user_text


def build_completion_check_prompt(context: dict) -> tuple[str, str]:
    """构建任务完成度判断的 prompt。

    Args:
        context: {
            "user_request": str,
            "completed_steps": str,
            "final_response": str,
            "termination_reason": str,
        }

    Returns:
        (system_text, user_text) 元组
    """
    system_text = "你是一个任务完成度判断助手。请根据提供的信息判断任务是否完成。"
    template = load_prompt("completion_check")
    user_text = template.format(**context)
    return system_text, user_text


def build_resumption_prompt(context: dict) -> tuple[str, str]:
    """构建任务恢复的完整 prompt。

    使用完整人格注入（soul + voice + examples），
    因为恢复消息是 Lapwing 直接说给用户的话。

    Args:
        context: {
            "user_request": str,
            "completed_steps_summary": str,
            "partial_result": str,
            "remaining_description": str,
            "recent_messages": str,
            "skip_notice": str,
        }

    Returns:
        (system_text, instruction_text) 元组
    """
    soul = load_prompt("lapwing_soul")
    voice = load_prompt("lapwing_voice")

    try:
        examples = load_prompt("lapwing_examples")
    except Exception:
        examples = ""

    resumption_template = load_prompt("task_resumption")
    instruction = resumption_template.format(**context)

    parts = [soul]
    if examples:
        parts.append(examples)
    parts.append(voice)
    system_text = "\n\n".join(parts)

    return system_text, instruction


def _skill_nudge_instruction() -> str:
    """执行后反思：提示在完成复杂任务后用 trace_mark 标记值得回顾的经历。"""
    return (
        "## 执行后反思\n\n"
        "完成一个需要 3 次以上工具调用的任务后，在回复前快速想一下：\n\n"
        "1. 这次做的事以前做过类似的吗？\n"
        "2. 中间有没有走弯路后来纠正了？\n"
        "3. Kevin 有没有纠正我的做法？\n"
        "4. 有没有已有的经验其实可以更新？\n\n"
        "如果有，用 `trace_mark` 工具标记这次经历，附一句简短原因。\n"
        "不需要当场创建经验笔记——晚上自省的时候我会回来看这些标记。"
    )


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
