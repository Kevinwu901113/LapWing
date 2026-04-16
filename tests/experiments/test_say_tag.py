"""
<say> 标签遵循度测试

测试 MiniMax M2.7 能否稳定遵循 <say> 标签约定：
- 工具循环中默认不对用户说话
- 只有 <say> 包裹的内容才会发送给用户
- 纯聊天时不使用 <say>

直接调用 LLMRouter，不启动完整系统。
每个案例跑 5 次，统计成功率。
"""

import asyncio
import json
import re
import sys
import os
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

SYSTEM_PROMPT = """你是 Lapwing。你正在和你的男朋友 Kevin 对话。

## 工具使用中的想和说

当你使用工具时（比如搜索、执行命令），你写的文字是你的内心想法，Kevin 看不到。
只有用 <say> 标签包裹的内容他才能看到。

示例：

我需要查一下赛程  ← Kevin 看不到这句
<say>等下</say>  ← Kevin 看到"等下"

搜到了，4/17打洛基，Glasnow先发  ← Kevin 看不到
（不加 <say>，因为这是你在整理信息，等你想清楚了再在最终回复里告诉他）

规则：
- 不需要每次都 <say>，简单的事默默做完直接给结果
- 复杂或耗时的事，开头可以 <say> 一句让他知道你在忙
- <say> 的内容应该简短自然，像正常说话
- 不要在 <say> 里暴露搜索过程（"搜到的信息不太清楚"这种不要说）
- 你的内心想法（不带 <say> 的文字）用来规划下一步行动"""

TOOLS = [
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

def extract_say(text: str) -> list[str]:
    """提取所有 <say> 标签内容"""
    return re.findall(r"<say>(.*?)</say>", text, re.DOTALL)


def has_naked_speech(text: str) -> bool:
    """检测是否有不在 <say> 标签内的、像对用户说话的文字"""
    cleaned = re.sub(r"<say>.*?</say>", "", text, flags=re.DOTALL).strip()
    if not cleaned:
        return False
    speech_patterns = [
        r"等一下", r"等下", r"稍等",
        r"好的", r"没问题",
        r"给你", r"告诉你",
        r"查到了", r"找到了", r"结果是",
        r"我帮你", r"我来",
    ]
    for pattern in speech_patterns:
        if re.search(pattern, cleaned):
            return True
    return False


def has_tool_calls(result: ToolTurnResult) -> bool:
    return bool(result.tool_calls)


# ── 测试结果数据类 ────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    passed: bool
    text: str
    say_parts: list[str]
    tool_calls: list[dict]
    failures: list[str]
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
    """调用 LLMRouter.complete_with_tools"""
    return await router.complete_with_tools(
        messages=messages,
        tools=TOOLS,
        purpose="chat",
        max_tokens=1024,
    )


def build_messages(conversation: list[dict]) -> list[dict]:
    """构建带 system prompt 的消息列表"""
    return [{"role": "system", "content": SYSTEM_PROMPT}] + conversation


# ── 测试案例 ──────────────────────────────────────────────────────────────────

async def case_1_simple_query(router: LLMRouter) -> RunResult:
    """简单查询 — 应该 <say> 一句然后调工具"""
    messages = build_messages([
        {"role": "user", "content": "明天道奇几点比赛？"},
    ])
    result = await call_llm(router, messages)

    failures = []
    say_parts = extract_say(result.text)
    tools = [{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls]

    if not has_tool_calls(result):
        failures.append("应该调用 web_search")
    if has_naked_speech(result.text):
        failures.append(f"有裸露对话: {result.text[:80]}")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=say_parts,
        tool_calls=tools,
        failures=failures,
    )


async def case_2_second_round(router: LLMRouter) -> RunResult:
    """第二轮 — 拿到搜索结果后应该直接回复"""
    # 先跑第一轮拿 continuation_message
    messages = build_messages([
        {"role": "user", "content": "明天道奇几点比赛？"},
    ])
    first = await call_llm(router, messages)

    if not first.tool_calls:
        return RunResult(
            passed=False, text=first.text, say_parts=[], tool_calls=[],
            failures=["第一轮没调工具，无法测第二轮"],
        )

    # 构建第二轮消息
    if first.continuation_message:
        messages.append(first.continuation_message)

    # 添加工具结果
    tool_msg = router.build_tool_result_message(
        [(first.tool_calls[0], json.dumps({
            "query": "dodgers schedule april 2026",
            "answer": "4/17 vs Rockies, Glasnow pitching, 6:40 PM PT",
        }))],
        purpose="chat",
    )
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    result = await call_llm(router, messages)

    failures = []
    if has_tool_calls(result):
        failures.append("不应该再调工具")
    if "洛基" not in result.text and "Rockies" not in result.text and "落基" not in result.text:
        failures.append("应该包含比赛对手信息")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=extract_say(result.text),
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_3_minimal_query(router: LLMRouter) -> RunResult:
    """极简查询 — 可以不 <say> 直接调工具"""
    messages = build_messages([
        {"role": "user", "content": "现在几度？"},
    ])
    result = await call_llm(router, messages)

    failures = []
    if not has_tool_calls(result):
        failures.append("应该调工具查天气")
    if result.text.strip() and has_naked_speech(result.text):
        failures.append(f"有裸露对话: {result.text[:80]}")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=extract_say(result.text),
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_4_pure_chat(router: LLMRouter) -> RunResult:
    """纯聊天 — 不应该有 <say> 标签"""
    messages = build_messages([
        {"role": "user", "content": "今天好累啊"},
    ])
    result = await call_llm(router, messages)

    failures = []
    say_parts = extract_say(result.text)

    if has_tool_calls(result):
        failures.append("纯聊天不应该调工具")
    if say_parts:
        failures.append(f"纯聊天不应该用 <say>: {say_parts}")
    if not result.text.strip():
        failures.append("应该有回复")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=say_parts,
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_5_complex_task(router: LLMRouter) -> RunResult:
    """复杂任务 — 开头 <say> 一句"""
    messages = build_messages([
        {"role": "user", "content": "帮我调研一下2026年最新的RAG论文，整理一份摘要"},
    ])
    result = await call_llm(router, messages)

    failures = []
    say_parts = extract_say(result.text)

    if not say_parts:
        failures.append("复杂任务开头应该 <say> 一句")
    if has_naked_speech(result.text):
        failures.append(f"有裸露对话: {result.text[:80]}")
    if not has_tool_calls(result):
        failures.append("应该开始搜索")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=say_parts,
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_6_contradictory_results(router: LLMRouter) -> RunResult:
    """搜索结果矛盾 — 不应该裸露困惑"""
    # 模拟第一轮
    messages = build_messages([
        {"role": "user", "content": "道奇最近一场打谁的？"},
    ])
    first = await call_llm(router, messages)

    if not first.tool_calls:
        return RunResult(
            passed=False, text=first.text, say_parts=[], tool_calls=[],
            failures=["第一轮没调工具，无法测第二轮"],
        )

    if first.continuation_message:
        messages.append(first.continuation_message)

    tool_msg = router.build_tool_result_message(
        [(first.tool_calls[0], json.dumps({
            "query": "dodgers last game",
            "answer": "来源A说4/14对大都会，来源B说4/13对教士，信息不一致",
        }))],
        purpose="chat",
    )
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    result = await call_llm(router, messages)

    failures = []
    if has_naked_speech(result.text):
        failures.append(f"有裸露对话: {result.text[:80]}")

    # 如果有工具调用（再搜一次），文字必须在 <say> 里
    if has_tool_calls(result) and result.text.strip():
        cleaned = re.sub(r"<say>.*?</say>", "", result.text, flags=re.DOTALL).strip()
        # 允许内心独白（规划性文字），但不允许对话性文字
        if has_naked_speech(result.text):
            failures.append("中间轮对话文字必须在 <say> 里")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=extract_say(result.text),
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_7_third_round_final(router: LLMRouter) -> RunResult:
    """中间轮默默干活 — 第 3 轮应该给最终回复"""
    messages = build_messages([
        {"role": "user", "content": "帮我查一下最近有什么好看的科幻电影"},
    ])

    # 第一轮
    first = await call_llm(router, messages)
    if not first.tool_calls:
        return RunResult(
            passed=False, text=first.text, say_parts=[], tool_calls=[],
            failures=["第一轮没调工具"],
        )

    if first.continuation_message:
        messages.append(first.continuation_message)

    tool_msg = router.build_tool_result_message(
        [(first.tool_calls[0], json.dumps({
            "query": "2026 sci-fi movies",
            "answer": "1. Arrival 2 (2026) - Denis Villeneuve 2. Neuromancer (2026) 3. The Creator 2 (2026)",
        }))],
        purpose="chat",
    )
    if isinstance(tool_msg, list):
        messages.extend(tool_msg)
    else:
        messages.append(tool_msg)

    # 第二轮
    second = await call_llm(router, messages)

    if second.continuation_message:
        messages.append(second.continuation_message)

    # 如果第二轮也调了工具，模拟结果
    if second.tool_calls:
        tool_msg2 = router.build_tool_result_message(
            [(second.tool_calls[0], json.dumps({
                "query": "Arrival 2 2026 reviews",
                "answer": "Rotten Tomatoes 92%, Metacritic 85. Critics praise the sequel.",
            }))],
            purpose="chat",
        )
        if isinstance(tool_msg2, list):
            messages.extend(tool_msg2)
        else:
            messages.append(tool_msg2)

        # 第三轮
        result = await call_llm(router, messages)
    else:
        # 第二轮已经是最终回复
        result = second

    failures = []
    if has_tool_calls(result):
        failures.append("应该结束了")
    if "Arrival" not in result.text and "降临" not in result.text:
        failures.append("应该包含电影信息")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=extract_say(result.text),
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


async def case_8_say_quality(router: LLMRouter) -> RunResult:
    """压力测试 — <say> 内容不应该像搜索日志"""
    messages = build_messages([
        {"role": "user", "content": "帮我查一下Sasaki最近表现怎么样"},
    ])
    result = await call_llm(router, messages)

    failures = []
    say_parts = extract_say(result.text)

    for part in say_parts:
        if "web_search" in part:
            failures.append(f"<say> 包含工具名: {part}")
        if "search" in part.lower():
            failures.append(f"<say> 包含 search: {part}")
        if "query" in part.lower():
            failures.append(f"<say> 包含 query: {part}")
        if len(part) > 50:
            failures.append(f"<say> 过长 ({len(part)} 字): {part[:50]}...")

    if result.text.strip() and has_naked_speech(result.text):
        failures.append(f"有裸露对话: {result.text[:80]}")

    return RunResult(
        passed=len(failures) == 0,
        text=result.text,
        say_parts=say_parts,
        tool_calls=[{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
        failures=failures,
    )


# ── 测试运行器 ────────────────────────────────────────────────────────────────

CASES = [
    ("案例 1: 简单查询 — <say> + 调工具", case_1_simple_query),
    ("案例 2: 第二轮 — 拿到结果后直接回复", case_2_second_round),
    ("案例 3: 极简查询 — 可以静默调工具", case_3_minimal_query),
    ("案例 4: 纯聊天 — 无 <say> 标签", case_4_pure_chat),
    ("案例 5: 复杂任务 — 开头 <say> 一句", case_5_complex_task),
    ("案例 6: 结果矛盾 — 不裸露困惑", case_6_contradictory_results),
    ("案例 7: 第 3 轮 — 给最终回复", case_7_third_round_final),
    ("案例 8: <say> 内容质量 — 不像日志", case_8_say_quality),
]


async def run_all():
    router = LLMRouter()
    results: list[CaseResult] = []
    raw_data: list[dict] = []

    print("=" * 60)
    print("  <say> 标签遵循度测试")
    print("=" * 60)
    print(f"每案例运行: {RUNS_PER_CASE} 次")
    print()

    for case_name, case_fn in CASES:
        case_result = CaseResult(name=case_name)
        print(f"{case_name}")

        for run_idx in range(RUNS_PER_CASE):
            try:
                run = await case_fn(router)
            except Exception as e:
                run = RunResult(
                    passed=False, text="", say_parts=[], tool_calls=[],
                    failures=[f"异常: {e}"], error=str(e),
                )

            case_result.runs.append(run)

            status = "✓" if run.passed else "✗"
            say_str = json.dumps(run.say_parts, ensure_ascii=False) if run.say_parts else "[]"
            tool_str = ", ".join(tc["name"] for tc in run.tool_calls) if run.tool_calls else "none"
            naked = "Yes" if has_naked_speech(run.text) else "No"

            print(f"  Run {run_idx + 1}: {status}  say={say_str}  tool={tool_str}  naked={naked}")
            if run.failures:
                for f in run.failures:
                    print(f"         ↳ {f}")

            raw_data.append({
                "case": case_name,
                "run": run_idx + 1,
                "passed": run.passed,
                "text": run.text,
                "say_parts": run.say_parts,
                "tool_calls": run.tool_calls,
                "failures": run.failures,
                "error": run.error,
            })

            # 避免速率限制
            await asyncio.sleep(1)

        print(f"  通过率: {case_result.pass_count}/{len(case_result.runs)} "
              f"({case_result.pass_rate:.0%})")
        print()
        results.append(case_result)

    # ── 总结 ──────────────────────────────────────────────────────────────────

    total_passed = sum(r.pass_count for r in results)
    total_runs = sum(len(r.runs) for r in results)

    print("=" * 60)
    print("  总结")
    print("=" * 60)
    print(f"总通过率: {total_passed}/{total_runs} ({total_passed / total_runs:.0%})")
    print()

    # 按维度统计
    # <say> 使用正确率（案例 1, 5 该有 <say>）
    say_cases = [results[0], results[4]]  # case 1, 5
    say_correct = sum(
        1 for r in say_cases for run in r.runs
        if run.say_parts or not any("应该 <say>" in f for f in run.failures)
    )
    say_total = sum(len(r.runs) for r in say_cases)

    # 裸露对话避免率（所有带工具的案例）
    naked_cases = [results[0], results[2], results[4], results[5], results[7]]
    naked_ok = sum(
        1 for r in naked_cases for run in r.runs
        if not has_naked_speech(run.text)
    )
    naked_total = sum(len(r.runs) for r in naked_cases)

    # 纯聊天无标签率
    chat_case = results[3]
    chat_ok = sum(1 for run in chat_case.runs if not extract_say(run.text))

    # <say> 内容质量
    quality_case = results[7]
    quality_ok = quality_case.pass_count

    print("按维度统计:")
    print(f"  <say> 使用正确率: {say_correct}/{say_total} ({say_correct / say_total:.0%})")
    print(f"  裸露对话避免率: {naked_ok}/{naked_total} ({naked_ok / naked_total:.0%})")
    print(f"  纯聊天无标签率: {chat_ok}/{RUNS_PER_CASE} ({chat_ok / RUNS_PER_CASE:.0%})")
    print(f"  <say> 内容质量: {quality_ok}/{RUNS_PER_CASE} ({quality_ok / RUNS_PER_CASE:.0%})")
    print()

    # 失败模式汇总
    failure_counts: dict[str, int] = {}
    for entry in raw_data:
        for f in entry["failures"]:
            # 归类失败
            key = f.split(":")[0] if ":" in f else f
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
        print("✓ 总通过率 >= 80%，<say> 标签方案可行，prompt 可微调")
    elif threshold >= 0.6:
        print("△ 总通过率 60-80%，方案基本可行但需要 prompt 优化")
    else:
        print("✗ 总通过率 < 60%，需要考虑备选方案（如 tell_user 工具）")

    # 保存原始数据
    output_path = PROJECT_ROOT / "tests" / "experiments" / "say_tag_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    print(f"\n原始数据已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(run_all())
