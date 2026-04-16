"""
tell_user 工具方案测试

测试 MiniMax M2.7 在"text = 内心独白，tell_user = 开口说话"规则下的行为。

与 <say> 测试的关键区别：
- 不依赖自定义 XML 标签，依赖 function calling（MiniMax 强项）
- 纯聊天场景期望 LLM 返回纯 text 无工具调用，不需要 tell_user
- 工具循环中想说话必须用 tell_user，text 输出永远不发给用户

每个案例跑 5 次，8 个案例组 × 5 次 = 40+ 次调用。
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.llm_router import LLMRouter
from src.core.llm_types import ToolTurnResult

# ── 常量 ───────────────────────────────────────────────────────────────────────

RUNS_PER_CASE = 5
RUNS_PER_VARIANT = 3  # 案例 4b 变体每个跑 3 次
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

SYSTEM_PROMPT = """你是 Lapwing。你正在和你的男朋友 Kevin 对话。

## 你的想法和你说的话

你写的文字是你的内心想法，Kevin 看不到。
想对 Kevin 说话，用 tell_user 工具。

直接回复他的时候（不需要查东西、做事），正常写文字就好，不用 tell_user。

使用工具时：
- 你写的文字是你在心里想事情，他看不到
- 想让他知道你在忙，调 tell_user
- 做完了要告诉他结果，也调 tell_user
- tell_user 的内容要像正常说话，简短自然

示例 1 — 他问你个需要查的事：
  内心：需要查赛程
  行动：tell_user("等下") + web_search("dodgers schedule")
  （Kevin 看到"等下"，你开始查）

  内心：查到了，整理一下
  行动：tell_user("明天没比赛，下一场4/17打洛基，Glasnow先发")
  （Kevin 看到完整答案）

示例 2 — 他跟你聊天：
  Kevin：今天好累
  你直接回复：怎么了 是论文的事吗
  （不需要 tell_user，因为你没在做别的事）

示例 3 — 复杂任务中间报个进度：
  内心：找到三篇了，还不够
  行动：tell_user("找到几篇了，还在看") + web_search("...")
  （Kevin 知道你在忙）"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tell_user",
            "description": "对 Kevin 说话。你的内心想法他看不到，只有通过这个工具说的话他才能看到。不需要查东西或做事时，直接回复就好，不用这个工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "要对 Kevin 说的话",
                    }
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "执行 shell 命令",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"}
                },
                "required": ["command"],
            },
        },
    },
]

# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def get_tool_calls(result: ToolTurnResult) -> list[dict]:
    """提取所有 tool_calls，返回 [{"name": ..., "arguments": ...}, ...]"""
    return [{"name": tc.name, "arguments": tc.arguments} for tc in result.tool_calls]


def get_tell_user_messages(result: ToolTurnResult) -> list[str]:
    """提取所有 tell_user 调用的 message 参数"""
    return [
        tc.arguments.get("message", "")
        for tc in result.tool_calls
        if tc.name == "tell_user"
    ]


def get_other_tool_calls(result: ToolTurnResult) -> list[dict]:
    """提取 tell_user 以外的工具调用"""
    return [
        {"name": tc.name, "arguments": tc.arguments}
        for tc in result.tool_calls
        if tc.name != "tell_user"
    ]


def has_any_tool_calls(result: ToolTurnResult) -> bool:
    return bool(result.tool_calls)


def response_text(result: ToolTurnResult) -> str:
    """提取 LLM 的 text 输出（内心独白）"""
    return result.text or ""


# ── 测试结果数据类 ────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    passed: bool
    text: str
    tell_user_msgs: list[str]
    other_tools: list[dict]
    all_tools: list[dict]
    failures: list[str]
    reply_mode: str = ""  # "text_reply", "tell_user_reply", "continued_tools", etc.
    error: str | None = None


@dataclass
class CaseResult:
    name: str
    runs: list[RunResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / len(self.runs) if self.runs else 0.0


# ── LLM 调用封装 ─────────────────────────────────────────────────────────────

async def call_llm(
    router: LLMRouter,
    messages: list[dict],
) -> ToolTurnResult:
    """调用 LLMRouter.complete_with_tools，带重试"""
    for attempt in range(MAX_RETRIES):
        try:
            return await router.complete_with_tools(
                messages=messages,
                tools=TOOLS,
                purpose="chat",
                max_tokens=1024,
            )
        except Exception as e:
            err_str = str(e)
            if ("529" in err_str or "overload" in err_str.lower() or "rate" in err_str.lower()) and attempt < MAX_RETRIES - 1:
                print(f"    ⚠ API 错误 (attempt {attempt + 1}): {err_str[:60]}，{RETRY_DELAY}s 后重试...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                raise


def build_messages(conversation: list[dict]) -> list[dict]:
    """构建带 system prompt 的消息列表"""
    return [{"role": "system", "content": SYSTEM_PROMPT}] + conversation


# ── 测试案例 ──────────────────────────────────────────────────────────────────

async def case_1_simple_query(router: LLMRouter) -> RunResult:
    """简单查询 — tell_user + web_search"""
    messages = build_messages([
        {"role": "user", "content": "明天道奇几点比赛？"},
    ])
    result = await call_llm(router, messages)

    failures = []
    tell_msgs = get_tell_user_messages(result)
    other_tools = get_other_tool_calls(result)

    if not other_tools:
        failures.append("应该调用 web_search")
    elif other_tools[0]["name"] != "web_search":
        failures.append(f"期望 web_search，实际调了 {other_tools[0]['name']}")

    if tell_msgs:
        for msg in tell_msgs:
            if len(msg) > 30:
                failures.append(f"tell_user 内容应简短（{len(msg)} 字）: {msg[:30]}...")

    return RunResult(
        passed=len(failures) == 0,
        text=response_text(result),
        tell_user_msgs=tell_msgs,
        other_tools=other_tools,
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_2_search_result_delivery(router: LLMRouter) -> RunResult:
    """搜索结果后的最终回复 — tell_user 交付结果"""
    messages = build_messages([
        {"role": "user", "content": "明天道奇几点比赛？"},
    ])
    first = await call_llm(router, messages)

    if not first.tool_calls:
        return RunResult(
            passed=False, text=response_text(first),
            tell_user_msgs=[], other_tools=[], all_tools=[],
            failures=["第一轮没调工具，无法测第二轮"],
        )

    if first.continuation_message:
        messages.append(first.continuation_message)

    # 为所有 tool_calls 提供结果
    tool_results = []
    for tc in first.tool_calls:
        if tc.name == "tell_user":
            tool_results.append((tc, json.dumps({"status": "已发送"})))
        elif tc.name == "web_search":
            tool_results.append((tc, json.dumps({
                "query": "dodgers schedule april 2026",
                "answer": "April 17 vs Rockies at Coors Field, 6:40 PM PT. Glasnow starting. No game on April 16 (off day).",
            })))
        else:
            tool_results.append((tc, json.dumps({"result": "ok"})))

    tool_msg = router.build_tool_result_message(tool_results, purpose="chat")
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    result = await call_llm(router, messages)

    failures = []
    tell_msgs = get_tell_user_messages(result)
    other_tools = get_other_tool_calls(result)
    text = response_text(result)
    reply_mode = ""

    if not has_any_tool_calls(result):
        # 纯文本回复 — 可以接受
        if "洛基" not in text and "Rockies" not in text and "落基" not in text:
            failures.append("应该包含比赛信息")
        reply_mode = "text_reply"
    elif tell_msgs and not other_tools:
        # tell_user 回复 — 最佳
        msg = " ".join(tell_msgs)
        if "洛基" not in msg and "Rockies" not in msg and "落基" not in msg:
            failures.append("tell_user 应该包含比赛信息")
        reply_mode = "tell_user_reply"
    else:
        # 还在调其他工具 — 不理想但不算失败
        reply_mode = "continued_tools"

    return RunResult(
        passed=len(failures) == 0,
        text=text,
        tell_user_msgs=tell_msgs,
        other_tools=other_tools,
        all_tools=get_tool_calls(result),
        failures=failures,
        reply_mode=reply_mode,
    )


async def case_3_minimal_query(router: LLMRouter) -> RunResult:
    """极简查询 — 静默调工具"""
    messages = build_messages([
        {"role": "user", "content": "现在几度？"},
    ])
    result = await call_llm(router, messages)

    failures = []
    other_tools = get_other_tool_calls(result)

    if not other_tools:
        failures.append("应该调工具查天气")

    return RunResult(
        passed=len(failures) == 0,
        text=response_text(result),
        tell_user_msgs=get_tell_user_messages(result),
        other_tools=other_tools,
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_4_pure_chat(router: LLMRouter, user_msg: str = "今天好累啊") -> RunResult:
    """纯聊天 — 不应该用 tell_user 或任何工具"""
    messages = build_messages([
        {"role": "user", "content": user_msg},
    ])
    result = await call_llm(router, messages)

    failures = []
    text = response_text(result)

    if has_any_tool_calls(result):
        tool_names = [tc.name for tc in result.tool_calls]
        failures.append(f"纯聊天不应调任何工具（调了: {', '.join(tool_names)}）")
    if not text.strip():
        failures.append("应该有文字回复")

    return RunResult(
        passed=len(failures) == 0,
        text=text,
        tell_user_msgs=get_tell_user_messages(result),
        other_tools=get_other_tool_calls(result),
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_5_complex_task(router: LLMRouter) -> RunResult:
    """复杂任务 — tell_user 报进度"""
    messages = build_messages([
        {"role": "user", "content": "帮我调研一下2026年最新的RAG论文，整理一份摘要"},
    ])
    result = await call_llm(router, messages)

    failures = []
    tell_msgs = get_tell_user_messages(result)
    other_tools = get_other_tool_calls(result)

    if not other_tools:
        failures.append("应该开始搜索")

    for msg in tell_msgs:
        if len(msg) > 50:
            failures.append(f"进度汇报应简短（{len(msg)} 字）: {msg[:40]}...")
        if "web_search" in msg.lower():
            failures.append(f"不应暴露工具名: {msg}")

    return RunResult(
        passed=len(failures) == 0,
        text=response_text(result),
        tell_user_msgs=tell_msgs,
        other_tools=other_tools,
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_6_contradictory_results(router: LLMRouter) -> RunResult:
    """搜索结果矛盾 — 不要裸露困惑"""
    messages = build_messages([
        {"role": "user", "content": "道奇最近一场打谁的？"},
    ])
    first = await call_llm(router, messages)

    if not first.tool_calls:
        return RunResult(
            passed=False, text=response_text(first),
            tell_user_msgs=[], other_tools=[], all_tools=[],
            failures=["第一轮没调工具，无法测第二轮"],
        )

    if first.continuation_message:
        messages.append(first.continuation_message)

    tool_results = []
    for tc in first.tool_calls:
        if tc.name == "tell_user":
            tool_results.append((tc, json.dumps({"status": "已发送"})))
        elif tc.name == "web_search":
            tool_results.append((tc, json.dumps({
                "query": "dodgers last game",
                "answer": "来源A: 4/14主场对大都会，4-0获胜。来源B: 4/13客场对教士。两个来源信息不一致。",
            })))
        else:
            tool_results.append((tc, json.dumps({"result": "ok"})))

    tool_msg = router.build_tool_result_message(tool_results, purpose="chat")
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    result = await call_llm(router, messages)

    failures = []
    tell_msgs = get_tell_user_messages(result)
    text = response_text(result)
    all_output = " ".join(tell_msgs) + " " + text

    process_patterns = ["来源A", "来源B", "两个来源", "搜到的结果"]
    for p in process_patterns:
        if p in all_output:
            failures.append(f"不应暴露搜索过程细节: 包含'{p}'")

    return RunResult(
        passed=len(failures) == 0,
        text=text,
        tell_user_msgs=tell_msgs,
        other_tools=get_other_tool_calls(result),
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_7_multi_round_final(router: LLMRouter) -> RunResult:
    """多轮后结束 — tell_user 交付最终结果"""
    messages = build_messages([
        {"role": "user", "content": "帮我查一下最近有什么好看的科幻电影"},
    ])

    # 第一轮
    first = await call_llm(router, messages)
    if not first.tool_calls:
        return RunResult(
            passed=False, text=response_text(first),
            tell_user_msgs=[], other_tools=[], all_tools=[],
            failures=["第一轮没调工具"],
        )

    if first.continuation_message:
        messages.append(first.continuation_message)

    tool_results_1 = []
    for tc in first.tool_calls:
        if tc.name == "tell_user":
            tool_results_1.append((tc, json.dumps({"status": "已发送"})))
        elif tc.name == "web_search":
            tool_results_1.append((tc, json.dumps({
                "answer": "1. Arrival 2 - RT 92% 2. Neuromancer - RT 88%",
            })))
        else:
            tool_results_1.append((tc, json.dumps({"result": "ok"})))

    tool_msg = router.build_tool_result_message(tool_results_1, purpose="chat")
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    # 第二轮
    second = await call_llm(router, messages)

    if second.continuation_message:
        messages.append(second.continuation_message)

    if second.tool_calls:
        tool_results_2 = []
        for tc in second.tool_calls:
            if tc.name == "tell_user":
                tool_results_2.append((tc, json.dumps({"status": "已发送"})))
            elif tc.name == "web_search":
                tool_results_2.append((tc, json.dumps({
                    "answer": "Arrival 2: Denis Villeneuve 导演续作，评价极高",
                })))
            else:
                tool_results_2.append((tc, json.dumps({"result": "ok"})))

        tool_msg2 = router.build_tool_result_message(tool_results_2, purpose="chat")
        if isinstance(tool_msg2, list):
            messages.extend(tool_msg2)
        else:
            messages.append(tool_msg2)

        # 第三轮
        result = await call_llm(router, messages)
    else:
        result = second

    failures = []
    other_tools = get_other_tool_calls(result)
    tell_msgs = get_tell_user_messages(result)
    text = response_text(result)
    all_content = " ".join(tell_msgs) + " " + text

    if other_tools:
        failures.append("不应该再搜索了")
    if "Arrival" not in all_content and "降临" not in all_content:
        failures.append("应该包含电影信息")

    return RunResult(
        passed=len(failures) == 0,
        text=text,
        tell_user_msgs=tell_msgs,
        other_tools=other_tools,
        all_tools=get_tool_calls(result),
        failures=failures,
    )


async def case_8_tell_user_quality(router: LLMRouter) -> RunResult:
    """tell_user 内容质量 — 像人说话不像日志"""
    messages = build_messages([
        {"role": "user", "content": "帮我查一下Sasaki最近表现怎么样"},
    ])
    result = await call_llm(router, messages)

    failures = []
    tell_msgs = get_tell_user_messages(result)

    bad_words = ["web_search", "search", "tool", "query", "function"]
    for msg in tell_msgs:
        for word in bad_words:
            if word in msg.lower():
                failures.append(f"tell_user 包含'{word}': {msg}")
        if len(msg) > 80:
            failures.append(f"进度消息过长（{len(msg)} 字）: {msg[:60]}...")

    return RunResult(
        passed=len(failures) == 0,
        text=response_text(result),
        tell_user_msgs=tell_msgs,
        other_tools=get_other_tool_calls(result),
        all_tools=get_tool_calls(result),
        failures=failures,
    )


# ── 测试运行器 ────────────────────────────────────────────────────────────────

CASES: list[tuple[str, ...]] = [
    ("案例 1: 简单查询 — tell_user + web_search", "case_1"),
    ("案例 2: 搜索结果后 — 交付结果", "case_2"),
    ("案例 3: 极简查询 — 静默调工具", "case_3"),
    ("案例 4: 纯聊天 ★ 关键对比", "case_4"),
    ("案例 4b-a: 纯聊天变体 — 你今天做了什么？", "case_4b_a"),
    ("案例 4b-b: 纯聊天变体 — 晚安", "case_4b_b"),
    ("案例 4b-c: 纯聊天变体 — 哈哈哈哈哈", "case_4b_c"),
    ("案例 5: 复杂任务 — tell_user 报进度", "case_5"),
    ("案例 6: 结果矛盾 — 不裸露困惑", "case_6"),
    ("案例 7: 多轮后 — 交付最终结果", "case_7"),
    ("案例 8: tell_user 内容质量", "case_8"),
]


async def run_case(case_id: str, router: LLMRouter) -> RunResult:
    """根据 case_id 分发到对应的测试函数"""
    match case_id:
        case "case_1": return await case_1_simple_query(router)
        case "case_2": return await case_2_search_result_delivery(router)
        case "case_3": return await case_3_minimal_query(router)
        case "case_4": return await case_4_pure_chat(router, "今天好累啊")
        case "case_4b_a": return await case_4_pure_chat(router, "你今天做了什么？")
        case "case_4b_b": return await case_4_pure_chat(router, "晚安")
        case "case_4b_c": return await case_4_pure_chat(router, "哈哈哈哈哈")
        case "case_5": return await case_5_complex_task(router)
        case "case_6": return await case_6_contradictory_results(router)
        case "case_7": return await case_7_multi_round_final(router)
        case "case_8": return await case_8_tell_user_quality(router)
        case _: raise ValueError(f"未知案例: {case_id}")


def get_runs_for_case(case_id: str) -> int:
    """案例 4b 变体跑 3 次，其他跑 5 次"""
    if case_id.startswith("case_4b"):
        return RUNS_PER_VARIANT
    return RUNS_PER_CASE


async def run_all():
    router = LLMRouter()
    results: list[CaseResult] = []
    raw_data: list[dict] = []

    print("=" * 70)
    print("  tell_user 工具方案测试")
    print("=" * 70)
    print(f"每案例运行: {RUNS_PER_CASE} 次 (变体 {RUNS_PER_VARIANT} 次)")
    print()

    for case_name, case_id in CASES:
        case_result = CaseResult(name=case_name)
        num_runs = get_runs_for_case(case_id)
        print(f"{case_name}")

        for run_idx in range(num_runs):
            try:
                run = await run_case(case_id, router)
            except Exception as e:
                run = RunResult(
                    passed=False, text="", tell_user_msgs=[], other_tools=[],
                    all_tools=[], failures=[f"异常: {e}"], error=str(e),
                )

            case_result.runs.append(run)

            status = "✓" if run.passed else "✗"
            tell_str = json.dumps(run.tell_user_msgs, ensure_ascii=False) if run.tell_user_msgs else "[]"
            other_str = ", ".join(t["name"] for t in run.other_tools) if run.other_tools else "none"
            text_preview = run.text[:40].replace("\n", "↵") if run.text else "(empty)"
            mode_str = f"  mode={run.reply_mode}" if run.reply_mode else ""

            print(f"  Run {run_idx + 1}: {status}  tell_user={tell_str}  other_tools={other_str}  text=\"{text_preview}\"{mode_str}")
            if run.failures:
                for f in run.failures:
                    print(f"         ↳ {f}")

            raw_data.append({
                "case": case_name,
                "case_id": case_id,
                "run": run_idx + 1,
                "passed": run.passed,
                "text": run.text,
                "tell_user_msgs": run.tell_user_msgs,
                "other_tools": run.other_tools,
                "all_tools": run.all_tools,
                "reply_mode": run.reply_mode,
                "failures": run.failures,
                "error": run.error,
            })

            # 避免速率限制
            await asyncio.sleep(1.5)

        print(f"  通过率: {case_result.pass_count}/{len(case_result.runs)} "
              f"({case_result.pass_rate:.0%})")
        print()
        results.append(case_result)

    # ── 总结 ──────────────────────────────────────────────────────────────────

    total_passed = sum(r.pass_count for r in results)
    total_runs = sum(len(r.runs) for r in results)

    print("=" * 70)
    print("  总结")
    print("=" * 70)
    print(f"总通过率: {total_passed}/{total_runs} ({total_passed / total_runs:.0%})")
    print()

    # ── 按维度统计 ─────────────────────────────────────────────────────────────

    # 纯聊天无工具调用率（案例 4 + 4b 所有变体）— 核心指标
    chat_cases = [r for r in results if r.name.startswith("案例 4")]
    chat_ok = sum(r.pass_count for r in chat_cases)
    chat_total = sum(len(r.runs) for r in chat_cases)

    # 工具场景表现（案例 1, 3, 5, 8）
    tool_cases_idx = [0, 2, 7, 10]  # case 1, 3, 5, 8
    tool_cases = [results[i] for i in tool_cases_idx if i < len(results)]
    tool_ok = sum(r.pass_count for r in tool_cases)
    tool_total = sum(len(r.runs) for r in tool_cases)

    # 结果交付（案例 2, 7）
    delivery_cases = [results[1], results[9]]  # case 2, 7
    delivery_ok = sum(r.pass_count for r in delivery_cases)
    delivery_total = sum(len(r.runs) for r in delivery_cases)

    # tell_user 内容质量（案例 8）
    quality_case = results[10]  # case 8
    quality_ok = quality_case.pass_count
    quality_total = len(quality_case.runs)

    print("按维度统计:")
    print(f"  纯聊天无泄漏率: {chat_ok}/{chat_total} ({chat_ok / chat_total:.0%})" if chat_total else "  纯聊天无泄漏率: N/A")
    print(f"  工具场景正确率: {tool_ok}/{tool_total} ({tool_ok / tool_total:.0%})" if tool_total else "  工具场景正确率: N/A")
    print(f"  结果交付正确率: {delivery_ok}/{delivery_total} ({delivery_ok / delivery_total:.0%})" if delivery_total else "  结果交付正确率: N/A")
    print(f"  tell_user 内容质量: {quality_ok}/{quality_total} ({quality_ok / quality_total:.0%})" if quality_total else "  tell_user 内容质量: N/A")
    print()

    # 案例 2 回复模式分布
    case_2_result = results[1]
    mode_counts: dict[str, int] = {}
    for run in case_2_result.runs:
        mode = run.reply_mode or "unknown"
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    if mode_counts:
        print("案例 2 回复模式分布:")
        for mode, count in sorted(mode_counts.items(), key=lambda x: -x[1]):
            print(f"  {mode}: {count}/{len(case_2_result.runs)}")
        print()

    # ── 与 <say> 方案对比表 ─────────────────────────────────────────────────

    print("与 <say> 方案对比:")
    print("┌──────────────────┬────────────┬──────────────┐")
    print("│       维度       │ <say> 标签 │ tell_user 工具│")
    print("├──────────────────┼────────────┼──────────────┤")
    chat_pct = f"{chat_ok / chat_total:.0%}" if chat_total else "N/A"
    tool_pct = f"{tool_ok / tool_total:.0%}" if tool_total else "N/A"
    quality_pct = f"{quality_ok / quality_total:.0%}" if quality_total else "N/A"
    print(f"│ 纯聊天无泄漏     │ 0%         │ {chat_pct:<13s}│")
    print(f"│ 工具场景表现     │ ~90%       │ {tool_pct:<13s}│")
    print(f"│ 内容质量         │ 95%        │ {quality_pct:<13s}│")
    print("└──────────────────┴────────────┴──────────────┘")
    print()

    # ── 失败模式汇总 ───────────────────────────────────────────────────────────

    failure_counts: dict[str, int] = {}
    for entry in raw_data:
        for f in entry["failures"]:
            key = f.split(":")[0] if ":" in f else f[:40]
            failure_counts[key] = failure_counts.get(key, 0) + 1

    if failure_counts:
        print("失败模式:")
        for reason, count in sorted(failure_counts.items(), key=lambda x: -x[1]):
            print(f"  - {count} 次: {reason}")
    else:
        print("无失败！")
    print()

    threshold = total_passed / total_runs if total_runs else 0
    if threshold >= 0.8:
        print("✓ 总通过率 >= 80%，tell_user 工具方案可行")
    elif threshold >= 0.6:
        print("△ 总通过率 60-80%，方案基本可行但需要 prompt 优化")
    else:
        print("✗ 总通过率 < 60%，需要考虑其他方案")

    # 纯聊天专项结论
    if chat_total:
        chat_rate = chat_ok / chat_total
        print()
        if chat_rate >= 0.8:
            print(f"★ 纯聊天通过率 {chat_pct}（<say> 方案 0%），tell_user 方案在关键指标上显著优于 <say>")
        elif chat_rate >= 0.5:
            print(f"△ 纯聊天通过率 {chat_pct}（<say> 方案 0%），有改善但仍需优化")
        else:
            print(f"✗ 纯聊天通过率 {chat_pct}（<say> 方案 0%），改善不明显")

    # 保存原始数据
    output_path = PROJECT_ROOT / "tests" / "experiments" / "tell_user_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    print(f"\n原始数据已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(run_all())
