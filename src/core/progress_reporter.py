"""进度汇报器 —— 在工具循环中让 LLM 自主判断是否需要向用户汇报进度。

设计理念：
- 零模板：所有用户可见文字均由 LLM + 完整人格生成
- 单次调用：判断 + 生成合一，减少延迟
- 写入上下文：汇报后注入 [系统提醒] 消息，防止最终回复重复
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("lapwing.core.progress_reporter")

# ── 配置常量 ──

# 最少完成 N 步工具调用后才考虑触发进度判断
MIN_STEPS_BEFORE_CHECK = 2

# 两次汇报之间最少间隔 N 秒（防止频繁打扰）
MIN_INTERVAL_BETWEEN_REPORTS = 15

# 每次任务最多汇报 N 次（防止话痨）
MAX_REPORTS_PER_TASK = 3

# 不触发进度判断的标记（LLM 返回此标记表示不需要汇报）
NO_REPORT_MARKER = "[NO_REPORT]"


@dataclass
class ProgressState:
    """追踪单次工具循环的进度汇报状态。"""

    # 已完成的工具调用步骤摘要
    completed_steps: list[dict] = field(default_factory=list)

    # 已发送给用户的中间汇报消息
    sent_reports: list[str] = field(default_factory=list)

    # 上次汇报的时间戳
    last_report_time: float = 0.0

    # 工具循环开始时间
    loop_start_time: float = field(default_factory=time.time)

    # 用户的原始请求文本
    user_request: str = ""

    def record_step(self, tool_name: str, arguments: dict, result_summary: str) -> None:
        """记录一步工具调用完成。"""
        self.completed_steps.append({
            "tool": tool_name,
            "args_brief": _brief_args(arguments),
            "result_brief": result_summary[:300],
            "timestamp": time.time(),
        })

    def should_check(self) -> bool:
        """前置条件检查：是否值得做一次 LLM 判断。"""
        if len(self.completed_steps) < MIN_STEPS_BEFORE_CHECK:
            return False
        if len(self.sent_reports) >= MAX_REPORTS_PER_TASK:
            return False
        if self.last_report_time > 0:
            elapsed = time.time() - self.last_report_time
            if elapsed < MIN_INTERVAL_BETWEEN_REPORTS:
                return False
        return True

    def record_report(self, message: str) -> None:
        """记录一次成功的汇报。"""
        self.sent_reports.append(message)
        self.last_report_time = time.time()


def _brief_args(arguments: dict) -> str:
    """将工具参数压缩为简短描述。"""
    parts = []
    for k, v in arguments.items():
        v_str = str(v)
        if len(v_str) > 80:
            v_str = v_str[:77] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


def build_progress_context(state: ProgressState) -> dict:
    """构建传给进度判断 prompt 的上下文变量。"""
    steps_text = ""
    for i, step in enumerate(state.completed_steps, 1):
        steps_text += f"第{i}步：调用 {step['tool']}（{step['args_brief']}）\n"
        steps_text += f"  结果：{step['result_brief']}\n\n"

    latest = state.completed_steps[-1] if state.completed_steps else {}
    latest_text = f"工具：{latest.get('tool', 'N/A')}\n结果：{latest.get('result_brief', 'N/A')}"

    if state.sent_reports:
        sent_text = "\n".join(f"- {msg}" for msg in state.sent_reports)
    else:
        sent_text = "（还没有跟用户说过任何进度）"

    return {
        "user_request": state.user_request,
        "completed_steps": steps_text,
        "latest_result": latest_text,
        "sent_messages": sent_text,
    }


async def check_and_report(
    *,
    state: ProgressState,
    llm_router: Any,
    on_interim_text: Callable[[str], Awaitable[None]] | None,
    messages: list[dict],
) -> bool:
    """核心函数：判断是否需要汇报，如果需要则生成并发送。

    Args:
        state: 当前进度追踪状态
        llm_router: LLMRouter 实例
        on_interim_text: 发送中间文本的回调（与 task_runtime 的 on_interim_text 相同）
        messages: 当前工具循环的消息列表（会就地插入提醒消息）

    Returns:
        True 如果发送了汇报，False 如果跳过
    """
    if not state.should_check():
        return False

    if on_interim_text is None:
        return False

    context = build_progress_context(state)

    # 构建 prompt：soul + voice + examples 作为 system，progress_check 作为 user
    from src.core.prompt_builder import build_progress_prompt
    system_text, user_text = build_progress_prompt(context)

    try:
        result = await llm_router.query_lightweight(
            system=system_text,
            user=user_text,
            slot="main_conversation",
        )

        response_text = result.strip()

        if NO_REPORT_MARKER in response_text:
            logger.debug("Progress check: LLM decided no report needed")
            return False

        # LLM 决定要汇报
        report_message = response_text.replace(NO_REPORT_MARKER, "").strip()
        if not report_message:
            return False

        logger.info("Progress report: %s", report_message[:100])

        # 发送给用户（bypass monologue filter，进度汇报是专门生成的，不应被拦截）
        await on_interim_text(report_message, bypass_monologue_filter=True)

        # 注入到消息列表，让后续 LLM 调用知道自己说过什么
        # 使用 user 角色 + [系统提醒] 前缀（已有模式，兼容所有 API）
        messages.append({
            "role": "user",
            "content": (
                f"[系统提醒] 你刚才向用户发送了以下进度消息：\n"
                f"「{report_message}」\n"
                f"继续工作，最终回复时不要重复已经说过的内容。"
            ),
        })

        state.record_report(report_message)
        return True

    except Exception as e:
        logger.warning("Progress check failed (non-fatal): %s", e)
        return False
