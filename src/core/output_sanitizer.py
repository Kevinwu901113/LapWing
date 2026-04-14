"""
出口兜底过滤器。
在消息发送给用户之前，移除所有不应暴露的内部标记。
这是最后一道防线——各模块自己的过滤逻辑应该已经处理了大部分情况，
这里只处理漏网之鱼。
"""

import re

# 所有已知的内部标记 pattern
_PATTERNS: list[re.Pattern] = [
    # [SPLIT] 由分段逻辑处理，不在此处移除
    # <user_visible> 标签（开闭）
    re.compile(r"</?user_visible>"),
    # [NEXT: 数字+单位] / [TNEXT: 数字+单位] — 意识循环唤醒间隔
    re.compile(r"\[T?NEXT:\s*\d+\s*(?:s|m|min|h)\]", re.IGNORECASE),
    # [ENTER] — LLM 随意输出的标记
    re.compile(r"\[ENTER\]", re.IGNORECASE),
    # 模拟工具调用 — LLM 在文本中假装调用工具
    re.compile(r"\[调用\s+\w+[:\s].*?\]"),
    re.compile(r"\[tool_call:\s*.*?\]", re.IGNORECASE),
    # <think>...</think> 残留（MiniMax 内部思考块）
    re.compile(r"<think>.*?</think>", re.DOTALL),
    # 孤立的 <think> 或 </think> 标签
    re.compile(r"</?think>"),
]


def sanitize_outgoing(text: str) -> str:
    """移除所有内部标记。在发送给用户前调用。"""
    if not text:
        return text
    for pattern in _PATTERNS:
        text = pattern.sub("", text)
    # 清理多余空行（标记移除后可能留下连续空行）
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
