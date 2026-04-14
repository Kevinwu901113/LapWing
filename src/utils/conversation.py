"""Shared conversation formatting for LLM consumption."""


def format_messages_for_llm(
    messages: list[dict],
    max_messages: int | None = None,
    speaker_labels: dict[str, str] | None = None,
    max_content_len: int | None = None,
) -> str:
    """Format conversation messages into a text block for LLM input.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        max_messages: If set, only take the last N messages.
        speaker_labels: Map role -> display label.
                       Defaults to {"user": "用户", "assistant": "Lapwing"}.
        max_content_len: If set, truncate individual message content.
    """
    labels = speaker_labels or {"user": "用户", "assistant": "Lapwing", "system": "系统"}
    if max_messages:
        messages = messages[-max_messages:]

    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        # Handle Anthropic multipart content
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "\n".join(text_parts) if text_parts else "[非文本内容]"
        if not content or role == "system":
            continue
        if max_content_len and len(content) > max_content_len:
            content = content[:max_content_len] + "..."
        label = labels.get(role, role)
        lines.append(f"{label}: {content}")
    return "\n".join(lines)
