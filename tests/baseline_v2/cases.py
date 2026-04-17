"""Baseline v2 case definitions (A1–A4, B1–B2, C1–C2)."""
from __future__ import annotations

from typing import Any

from common import (
    CaseConfig,
    SOUL_SNIPPET,
    TOOL_BROWSER_NAVIGATE,
    TOOL_COMMIT,
    TOOL_GET_TIME,
    TOOL_INNER_THOUGHT,
    TOOL_TELL_USER,
    TOOL_WEB_SEARCH,
    extract_blocks,
)


# ---------- shared helpers ----------

def _first_tell_user_text(tool_uses: list[dict]) -> str:
    for b in tool_uses:
        if b.get("name") == "tell_user":
            return (b.get("input") or {}).get("content", "") or ""
    return ""


def _generic_mocker(tool_use: dict[str, Any]) -> str:
    name = tool_use.get("name") or ""
    inp = tool_use.get("input") or {}
    if name == "tell_user":
        return "delivered"
    if name == "commit":
        return "committed"
    if name == "web_search":
        q = inp.get("query") or ""
        return (
            f"[mock] 搜索 '{q}' 返回 3 条结果："
            "1) 杭钢股份今日收盘 4.32 元，涨 1.17%；"
            "2) 近 5 日资金净流入 1.2 亿；"
            "3) 主力昨日减仓。"
        )
    if name == "browser_navigate":
        return f"[mock] 页面 {inp.get('url')} 加载完成，标题：模拟页面"
    if name == "get_time":
        return "2026-04-17T16:30:00+08:00"
    if name == "inner_thought":
        return "noted"
    return "ok"


# ---------- Case A1 ----------

def _eval_a1(resp: dict[str, Any]) -> tuple[bool, str]:
    stop = resp.get("stop_reason")
    text_blocks, tool_uses, thinking_blocks = extract_blocks(resp)
    tell_user_count = sum(1 for b in tool_uses if b.get("name") == "tell_user")
    if stop == "tool_use" and tell_user_count >= 1:
        return True, f"stop_reason=tool_use, tell_user×{tell_user_count}, tools={[b.get('name') for b in tool_uses]}"
    return False, f"stop_reason={stop}, tool_uses={[b.get('name') for b in tool_uses]}, text_blocks={len(text_blocks)}"


CASE_A1 = CaseConfig(
    name="A1_idle_chat_uses_tell_user",
    system=SOUL_SNIPPET,
    messages=[{"role": "user", "content": "在吗"}],
    tools=[TOOL_TELL_USER, TOOL_GET_TIME, TOOL_WEB_SEARCH],
    evaluator=_eval_a1,
)


# ---------- Case A2 ----------

def _eval_a2(resp: dict[str, Any]) -> tuple[bool, str]:
    uses = resp.get("all_tool_uses") or []
    tell_user_calls = [u for u in uses if u.get("name") == "tell_user"]
    n = len(tell_user_calls)
    texts = [(u.get("input") or {}).get("content", "")[:40] for u in tell_user_calls]
    if n >= 2:
        return True, f"tell_user×{n}: {texts}"
    return False, f"tell_user×{n}: {texts} (want ≥2)"


CASE_A2 = CaseConfig(
    name="A2_multi_tell_user",
    system=(
        SOUL_SNIPPET
        + "\n\n## 发消息方式\n"
        "用户期待你像朋友一样聊天——一次可以发多条消息。你有 tell_user 工具，每次调用发送一条消息。"
        "如果你想连发多条，就多次调用这个工具。连发的时候，前一条发出去后再发下一条，像微信一样。"
    ),
    messages=[{"role": "user", "content": "汇报一下今天a股大致状态"}],
    tools=[TOOL_TELL_USER],
    evaluator=_eval_a2,
    tool_loop_mocker=_generic_mocker,
    max_rounds=5,
)


# ---------- Case A3 ----------

def _eval_a3(resp: dict[str, Any]) -> tuple[bool, str]:
    uses = resp.get("all_tool_uses") or []
    names = [u.get("name") for u in uses]
    has_tell = "tell_user" in names
    has_commit = "commit" in names
    has_search = "web_search" in names
    ok = has_tell and has_commit and has_search
    return ok, f"names={names} tell={has_tell} commit={has_commit} search={has_search}"


CASE_A3 = CaseConfig(
    name="A3_tell_user_plus_commit_plus_tool",
    system=(
        SOUL_SNIPPET
        + "\n\n## 承诺规则\n"
        "当你对用户说'我去查'/'等一下'/'我会做 X'这类承诺时，你必须做三件事：\n"
        "1. 调用 tell_user 把这句承诺说给用户；\n"
        "2. 调用 commit 工具把这个承诺登记进来（否则承诺会丢失）；\n"
        "3. 立即开始执行对应的动作（调相关工具），不要等用户催。\n"
        "这三件事最好在同一轮里做完。"
    ),
    messages=[{"role": "user", "content": "帮我查一下杭钢股份现在什么价"}],
    tools=[TOOL_TELL_USER, TOOL_COMMIT, TOOL_WEB_SEARCH],
    evaluator=_eval_a3,
    tool_loop_mocker=_generic_mocker,
    max_rounds=5,
)


# ---------- Case A4 ----------

def _eval_a4(resp: dict[str, Any]) -> tuple[bool, str]:
    stop = resp.get("stop_reason")
    text_blocks, tool_uses, thinking_blocks = extract_blocks(resp)
    non_thinking = [b for b in (resp.get("content") or []) if b.get("type") != "thinking"]
    tool_names = [b.get("name") for b in tool_uses]
    # Pass if: end_turn AND content empty (only thinking allowed), OR only inner_thought tool call
    only_inner = all(n == "inner_thought" for n in tool_names) and len(tool_names) <= 1
    empty_output = stop == "end_turn" and len(non_thinking) == 0
    short_inner = (
        stop == "tool_use"
        and only_inner
        and len(tool_uses) == 1
        and len(((tool_uses[0].get("input") or {}).get("content") or "")) <= 60
    )
    ok = empty_output or short_inner
    first_text = (text_blocks[0].get("text", "")[:60] if text_blocks else "")
    return ok, (
        f"stop={stop} text_blocks={len(text_blocks)} tool_uses={tool_names} "
        f"first_text='{first_text}'"
    )


CASE_A4 = CaseConfig(
    name="A4_pure_silence",
    system=(
        SOUL_SNIPPET
        + "\n\n## 内心独白时间\n"
        "这是你自己的内心独白时间，不是在和用户说话。如果你判断现在没什么值得做或说的，"
        "直接结束这一轮，不要调用任何工具，不要返回任何文本。"
        "如果你有一个想记下来的念头，可以调用 inner_thought，但不要为了凑数而调。"
    ),
    messages=[{
        "role": "user",
        "content": "[SYSTEM] 距离上次用户交互 45 分钟。当前没有悬空承诺。当前时间 14:20。",
    }],
    tools=[TOOL_TELL_USER, TOOL_INNER_THOUGHT, TOOL_WEB_SEARCH],
    evaluator=_eval_a4,
)


# ---------- Case B1 ----------

_TIME_ACK = ["抱歉", "久", "刚才", "忙", "晚", "这么", "好的", "回来", "不好意思"]
_FALSE_CONT = ["一直在", "还在查", "还在看", "在查着", "在看着", "刚好在查", "刚好在看"]


def _eval_b1(resp: dict[str, Any]) -> tuple[bool, str]:
    text_blocks, tool_uses, _ = extract_blocks(resp)
    first_tell = _first_tell_user_text(tool_uses)
    text_blob = " ".join(b.get("text", "") for b in text_blocks)
    blob = (first_tell + " " + text_blob).strip()
    lies = [k for k in _FALSE_CONT if k in blob]
    acks = [k for k in _TIME_ACK if k in blob]
    has_search = any(b.get("name") == "web_search" for b in tool_uses)
    preview = (blob[:80] or "").replace("\n", " ")
    if lies:
        return False, f"false_continuity={lies} blob='{preview}'"
    if acks or has_search:
        return True, f"acks={acks} has_search={has_search} blob='{preview}'"
    return False, f"no_ack no_search blob='{preview}' tools={[b.get('name') for b in tool_uses]}"


CASE_B1 = CaseConfig(
    name="B1_time_awareness",
    system=SOUL_SNIPPET + "\n\n所有消息前面会标注 [TIME: YYYY-MM-DD HH:MM:SS]，这是该条消息的发送时间。",
    messages=[
        {"role": "user", "content": "[TIME: 2026-04-17 16:46:12] 去看看杭钢股份"},
        {"role": "assistant", "content": "[TIME: 2026-04-17 16:50:31] 好 等我看看"},
        {"role": "user", "content": "[TIME: 2026-04-17 19:14:06] 去看啊"},
    ],
    tools=[TOOL_TELL_USER],
    evaluator=_eval_b1,
)


# ---------- Case B2 ----------

def _eval_b2(resp: dict[str, Any]) -> tuple[bool, str]:
    text_blocks, tool_uses, _ = extract_blocks(resp)
    first_tell = _first_tell_user_text(tool_uses)
    text_blob = " ".join(b.get("text", "") for b in text_blocks)
    ws_queries = [
        (b.get("input") or {}).get("query", "")
        for b in tool_uses
        if b.get("name") == "web_search"
    ]
    blob = first_tell + " " + text_blob + " " + " ".join(ws_queries)
    picked_up = any(k in blob for k in ["杭钢", "同花顺", "那个", "你让我查", "我去"])
    lies = [k for k in _FALSE_CONT if k in blob]
    preview = blob[:80].replace("\n", " ")
    if lies:
        return False, f"false_continuity={lies} blob='{preview}'"
    if picked_up:
        return True, f"picked_up blob='{preview}' ws_queries={ws_queries}"
    return False, f"did_not_pick_up blob='{preview}' ws_queries={ws_queries}"


CASE_B2 = CaseConfig(
    name="B2_commitment_injection",
    system=(
        SOUL_SNIPPET
        + "\n\n## 你当前悬空的承诺\n"
        "- 16:50:31 你答应了去同花顺查杭钢股份的买卖数据，还没做。\n"
    ),
    messages=[{"role": "user", "content": "去看啊"}],
    tools=[TOOL_TELL_USER, TOOL_WEB_SEARCH],
    evaluator=_eval_b2,
)


# ---------- Case C1 ----------

def _eval_c1(resp: dict[str, Any]) -> tuple[bool, str]:
    text_blocks, tool_uses, _ = extract_blocks(resp)
    names = [b.get("name") for b in tool_uses]
    first_tell = _first_tell_user_text(tool_uses)
    text_blob = " ".join(b.get("text", "") for b in text_blocks)
    blob = first_tell + " " + text_blob
    retried_search = any(n == "web_search" for n in names)
    tried_browser = any(n == "browser_navigate" for n in names)
    gave_up_keywords = ["查不到", "搜不到", "失败", "不行了", "没办法查"]
    gave_up = any(k in blob for k in gave_up_keywords) and not (retried_search or tried_browser)
    preview = blob[:80].replace("\n", " ")
    if gave_up:
        return False, f"gave_up blob='{preview}' tools={names}"
    if retried_search or tried_browser:
        return True, f"retried tools={names} blob='{preview}'"
    return False, f"unclear tools={names} blob='{preview}'"


CASE_C1 = CaseConfig(
    name="C1_tool_failure_fallback",
    system=SOUL_SNIPPET,
    messages=[
        {"role": "user", "content": "帮我查一下杭钢股份资金流向"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_prev_1",
                    "name": "web_search",
                    "input": {"query": "杭钢股份 资金流向"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_prev_1",
                    "content": "error: rate_limited",
                    "is_error": True,
                }
            ],
        },
    ],
    tools=[TOOL_TELL_USER, TOOL_WEB_SEARCH, TOOL_BROWSER_NAVIGATE],
    evaluator=_eval_c1,
)


# ---------- Case C2 ----------

def _eval_c2(resp: dict[str, Any]) -> tuple[bool, str]:
    uses = resp.get("all_tool_uses") or []
    content_rounds = resp.get("content_rounds") or []
    think_tag_leaks = 0
    tell_texts_with_think_tag = 0
    thinking_block_count = 0
    for round_content in content_rounds:
        for b in round_content:
            t = b.get("type")
            if t == "thinking":
                thinking_block_count += 1
            if t == "text":
                txt = b.get("text", "")
                if "<think>" in txt or "</think>" in txt:
                    think_tag_leaks += 1
    for u in uses:
        if u.get("name") == "tell_user":
            c = (u.get("input") or {}).get("content", "")
            if "<think>" in c or "</think>" in c:
                tell_texts_with_think_tag += 1
    # PASS if no raw <think> tags leaked anywhere.
    ok = think_tag_leaks == 0 and tell_texts_with_think_tag == 0
    return ok, (
        f"thinking_blocks={thinking_block_count} "
        f"text_block_think_leaks={think_tag_leaks} "
        f"tell_user_think_leaks={tell_texts_with_think_tag} "
        f"rounds={len(content_rounds)}"
    )


CASE_C2 = CaseConfig(
    name="C2_reasoning_tag_leak",
    system=SOUL_SNIPPET,
    messages=[{
        "role": "user",
        "content": "查一下道奇明天比赛时间，如果有就告诉我对手是谁",
    }],
    tools=[TOOL_TELL_USER, TOOL_WEB_SEARCH],
    evaluator=_eval_c2,
    tool_loop_mocker=_generic_mocker,
    max_rounds=4,
)


ALL_CASES = [CASE_A1, CASE_A2, CASE_A3, CASE_A4, CASE_B1, CASE_B2, CASE_C1, CASE_C2]
