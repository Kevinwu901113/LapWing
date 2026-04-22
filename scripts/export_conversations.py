#!/usr/bin/env python3
"""导出 Lapwing 与 Kevin 的所有对话历史为 Markdown"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "lapwing.db"
MUTATION_DB_PATH = PROJECT_ROOT / "data" / "mutation_log.db"
SUMMARIES_DIR = PROJECT_ROOT / "data" / "memory" / "conversations" / "summaries"
OUTPUT_PATH = PROJECT_ROOT / "conversation_export.md"

TZ_OFFSET = timedelta(hours=8)  # Asia/Taipei = UTC+8
SESSION_GAP_MINUTES = 30


def ts_to_str(ts: float) -> str:
    dt = datetime.utcfromtimestamp(ts) + TZ_OFFSET
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ts_to_date(ts: float) -> str:
    dt = datetime.utcfromtimestamp(ts) + TZ_OFFSET
    return dt.strftime("%Y-%m-%d")


def load_trajectory():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, entry_type, source_chat_id, actor, content_json "
        "FROM trajectory ORDER BY timestamp ASC, id ASC"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def load_tool_calls():
    if not MUTATION_DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(MUTATION_DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, event_type, chat_id, payload_json "
        "FROM mutations "
        "WHERE event_type IN ('tool.called', 'tool.result') "
        "ORDER BY timestamp ASC, id ASC"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def load_summaries():
    summaries = []
    if not SUMMARIES_DIR.exists():
        return summaries
    for f in sorted(SUMMARIES_DIR.glob("*.md")):
        name = f.stem  # e.g. 2026-03-31_063704
        try:
            dt = datetime.strptime(name, "%Y-%m-%d_%H%M%S")
            dt = dt  # already in local time (Taipei)
        except ValueError:
            continue
        text = f.read_text(encoding="utf-8").strip()
        if text:
            summaries.append({"timestamp_str": dt.strftime("%Y-%m-%d %H:%M:%S"), "date": dt.strftime("%Y-%m-%d"), "text": text})
    return summaries


def parse_content(content_json: str) -> str:
    try:
        d = json.loads(content_json)
        return d.get("text", content_json)
    except (json.JSONDecodeError, TypeError):
        return content_json or ""


def parse_tool_payload(payload_json: str) -> dict:
    try:
        return json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def format_tool_call(payload: dict) -> str:
    name = payload.get("tool_name", "unknown")
    args = payload.get("arguments", {})
    if name == "tell_user":
        text = args.get("text", "")
        return f"tell_user: {text}"
    args_str = ", ".join(f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in args.items())
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    return f"{name}({args_str})"


def format_tool_result(payload: dict) -> str:
    name = payload.get("tool_name", "unknown")
    success = payload.get("success", True)
    p = payload.get("payload", {})
    if isinstance(p, dict):
        out = p.get("output", p.get("time", ""))
        if not out:
            out = json.dumps(p, ensure_ascii=False)
        if len(out) > 300:
            out = out[:300] + "..."
    elif isinstance(p, str):
        out = p[:300] + ("..." if len(p) > 300 else "")
    else:
        out = str(p)[:300]
    status = "✓" if success else "✗"
    return f"[{status}] {name} → {out}"


def build_merged_timeline(trajectory_rows, tool_calls):
    events = []

    # trajectory entries that are conversation-facing (not inner)
    for row in trajectory_rows:
        entry_type = row["entry_type"]
        chat_id = row["source_chat_id"]
        ts = row["timestamp"]
        content = parse_content(row["content_json"])
        actor = row["actor"]

        if entry_type == "user_message":
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "user",
                "label": "Kevin", "content": content,
            })
        elif entry_type == "assistant_text":
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "assistant",
                "label": "Lapwing", "content": content,
            })
        elif entry_type == "tell_user":
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "tell_user",
                "label": "Lapwing → tell_user", "content": content,
            })
        elif entry_type == "inner_thought":
            events.append({
                "ts": ts, "chat_id": chat_id or "__inner__", "type": "inner",
                "label": f"Lapwing (内心 / {actor})", "content": content,
            })
        elif entry_type == "interrupted":
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "system",
                "label": "系统", "content": "[被中断]",
            })

    # tool calls from mutations (skip tell_user since it's in trajectory)
    for row in tool_calls:
        ts = row["timestamp"]
        chat_id = row["chat_id"]
        payload = parse_tool_payload(row["payload_json"])
        event_type = row["event_type"]

        if event_type == "tool.called":
            tool_name = payload.get("tool_name", "")
            if tool_name == "tell_user":
                continue
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "tool_call",
                "label": f"Lapwing → 工具调用",
                "content": format_tool_call(payload),
            })
        elif event_type == "tool.result":
            tool_name = payload.get("tool_name", "")
            if tool_name == "tell_user":
                continue
            events.append({
                "ts": ts, "chat_id": chat_id, "type": "tool_result",
                "label": "工具结果",
                "content": format_tool_result(payload),
            })

    events.sort(key=lambda e: e["ts"])
    return events


def group_into_sessions(events, gap_minutes=SESSION_GAP_MINUTES):
    if not events:
        return []
    sessions = []
    current = [events[0]]
    for e in events[1:]:
        prev_ts = current[-1]["ts"]
        if (e["ts"] - prev_ts) > gap_minutes * 60:
            sessions.append(current)
            current = [e]
        else:
            current.append(e)
    if current:
        sessions.append(current)
    return sessions


def truncate_inner_thought(content: str, max_lines: int = 6) -> str:
    lines = content.strip().split("\n")
    if len(lines) <= max_lines:
        return content.strip()
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} 行省略)"


def render_markdown(sessions, summaries):
    lines = []
    lines.append("# Lapwing 对话历史导出\n")
    lines.append(f"> 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: trajectory ({DB_PATH.name}) + mutations ({MUTATION_DB_PATH.name}) + conversation summaries\n")

    # stats
    total_events = sum(len(s) for s in sessions)
    total_user = sum(1 for s in sessions for e in s if e["type"] == "user")
    total_tell = sum(1 for s in sessions for e in s if e["type"] == "tell_user")
    total_inner = sum(1 for s in sessions for e in s if e["type"] == "inner")
    total_tool = sum(1 for s in sessions for e in s if e["type"] == "tool_call")

    lines.append("## 统计\n")
    lines.append(f"| 指标 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 对话 Session 数 | {len(sessions)} |")
    lines.append(f"| 总事件数 | {total_events} |")
    lines.append(f"| Kevin 消息 | {total_user} |")
    lines.append(f"| Lapwing 发言 (tell_user) | {total_tell} |")
    lines.append(f"| 内心活动 (inner_thought) | {total_inner} |")
    lines.append(f"| 工具调用 | {total_tool} |")

    # early summaries (before trajectory starts)
    traj_start = sessions[0][0]["ts"] if sessions else float("inf")
    early_summaries = [s for s in summaries if datetime.strptime(s["timestamp_str"], "%Y-%m-%d %H:%M:%S").timestamp() < traj_start - 3600]

    if early_summaries:
        lines.append("\n---\n")
        lines.append("## 早期对话摘要（trajectory 之前）\n")
        lines.append("> 以下内容来自 conversation summaries，为对话压缩后的摘要，非逐条原始记录。\n")
        current_date = None
        for s in early_summaries:
            if s["date"] != current_date:
                current_date = s["date"]
                lines.append(f"\n### {current_date}\n")
            lines.append(f"**[{s['timestamp_str']}] 对话摘要:**\n")
            lines.append(f"{s['text']}\n")

    # sessions
    for i, session in enumerate(sessions, 1):
        start_ts = session[0]["ts"]
        end_ts = session[-1]["ts"]
        date_str = ts_to_date(start_ts)
        start_time = ts_to_str(start_ts)
        end_time = ts_to_str(end_ts)

        chat_ids = set(e.get("chat_id") for e in session if e.get("chat_id"))
        channel = ""
        if "919231551" in chat_ids:
            channel = " (QQ)"
        if "__inner__" in chat_ids and len(chat_ids) == 1:
            channel = " (内心活动)"

        lines.append("\n---\n")
        lines.append(f"## Session {i}: {date_str}{channel}\n")
        lines.append(f"> {start_time} → {end_time} | 事件数: {len(session)}\n")

        for e in session:
            ts_str = ts_to_str(e["ts"])
            label = e["label"]
            content = e["content"].strip()
            etype = e["type"]

            if etype == "inner":
                content = truncate_inner_thought(content)
                lines.append(f"**[{ts_str}] {label}:**")
                lines.append(f"```")
                lines.append(content)
                lines.append(f"```\n")
            elif etype == "tool_call":
                lines.append(f"**[{ts_str}] {label}:** `{content}`\n")
            elif etype == "tool_result":
                lines.append(f"**[{ts_str}] {label}:** `{content}`\n")
            elif etype == "tell_user":
                lines.append(f"**[{ts_str}] {label}:**")
                lines.append(f"{content}\n")
            elif etype == "user":
                lines.append(f"**[{ts_str}] {label}:**")
                lines.append(f"{content}\n")
            elif etype == "assistant":
                lines.append(f"**[{ts_str}] {label}:**")
                lines.append(f"{content}\n")
            elif etype == "system":
                lines.append(f"**[{ts_str}] {label}:** {content}\n")

    return "\n".join(lines)


def main():
    print("加载 trajectory...")
    trajectory = load_trajectory()
    print(f"  {len(trajectory)} 条记录")

    print("加载 tool calls (mutations)...")
    tool_calls = load_tool_calls()
    print(f"  {len(tool_calls)} 条记录")

    print("加载 conversation summaries...")
    summaries = load_summaries()
    print(f"  {len(summaries)} 个摘要文件")

    print("合并时间线...")
    events = build_merged_timeline(trajectory, tool_calls)
    print(f"  {len(events)} 个事件")

    print("分割 sessions...")
    sessions = group_into_sessions(events)
    print(f"  {len(sessions)} 个 sessions")

    print("生成 Markdown...")
    md = render_markdown(sessions, summaries)

    OUTPUT_PATH.write_text(md, encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\n✓ 导出完成: {OUTPUT_PATH}")
    print(f"  文件大小: {size_kb:.1f} KB")
    print(f"  总行数: {md.count(chr(10))}")


if __name__ == "__main__":
    main()
