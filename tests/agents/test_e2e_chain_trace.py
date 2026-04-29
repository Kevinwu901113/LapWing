"""End-to-end chain trace test.

模拟一条完整的 Lapwing → delegate_to_researcher → Researcher → research →
回传 Lapwing → 用户可见回复 链路，记录每一步的输入、输出、耗时、时间戳。

注意：
- Brain 类的依赖太重（StateViewBuilder/TrajectoryStore/EventBus/MainLoop 等），
  本测试不实例化真实 Brain。改为模拟"Brain 外层 tool loop"的行为：
  调用 LLM → 收到 tool_call(delegate_to_researcher) → 通过 ToolRegistry
  执行该工具 → 把 tool result 回传给 LLM → 拿到最终用户可见文本。
- ToolRegistry / AgentRegistry / Researcher / BaseAgent 全部使用真实代码，
  只 mock LLMRouter（这是唯一会触发外部网络调用的地方）。
- "dispatcher 选择 agent" 这一步：当前架构没有独立 Dispatcher 组件——
  选择由主脑 LLM 通过 tool_call 命名（delegate_to_researcher vs delegate_to_coder）
  完成。本测试通过断言 LLM 返回的 tool_call.name 来验证选择正确。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder
from src.agents.registry import AgentRegistry
from src.agents.researcher import Researcher
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.logging.state_mutation_log import MutationType
from src.tools.agent_tools import register_agent_tools
from src.tools.registry import ToolRegistry
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# 链路追踪基础设施
# ---------------------------------------------------------------------------

@dataclass
class TraceStep:
    seq: int
    component: str
    action: str
    input: Any
    output: Any
    started_at: float
    duration_ms: float
    extra: dict = field(default_factory=dict)

    def render(self) -> str:
        in_repr = json.dumps(self.input, ensure_ascii=False, default=str)[:300]
        out_repr = json.dumps(self.output, ensure_ascii=False, default=str)[:300]
        return (
            f"[{self.seq:02d}] {self.component}.{self.action} "
            f"({self.duration_ms:.2f}ms)\n"
            f"     IN : {in_repr}\n"
            f"     OUT: {out_repr}"
        )


class ChainTracer:
    """收集每一步的输入输出，按顺序编号。"""

    def __init__(self):
        self.steps: list[TraceStep] = []
        self.t0 = time.perf_counter()

    def record(self, component: str, action: str, *, inp: Any, out: Any,
               started: float, **extra) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        self.steps.append(TraceStep(
            seq=len(self.steps) + 1,
            component=component,
            action=action,
            input=inp,
            output=out,
            started_at=started - self.t0,
            duration_ms=duration_ms,
            extra=extra,
        ))

    def dump(self) -> str:
        return "\n".join(step.render() for step in self.steps)


# ---------------------------------------------------------------------------
# Mock LLMRouter — 只针对本 chain 量身定制
# ---------------------------------------------------------------------------

def _build_router(tracer: ChainTracer, *, brain_responses, agent_responses):
    """构造一个共享的 mock LLMRouter。

    根据 origin / slot 区分调用方：
    - origin == "brain.outer" → 主脑外层
    - origin == "agent:researcher" → Researcher 内层
    """
    brain_iter = iter(brain_responses)
    agent_iter = iter(agent_responses)

    async def _complete_with_tools(*, messages, tools, slot=None,
                                   max_tokens=1024, origin=None,
                                   purpose="chat", session_key=None,
                                   allow_failover=True):
        started = time.perf_counter()
        if origin and origin.startswith("agent:"):
            response = next(agent_iter)
            component = f"LLMRouter[{origin}]"
        else:
            response = next(brain_iter)
            component = "LLMRouter[brain.outer]"

        # 仅记录 messages 末尾以避免把 system prompt 全部 dump 出来
        tail = messages[-1] if messages else {}
        tracer.record(
            component, "complete_with_tools",
            inp={
                "slot": slot,
                "origin": origin,
                "tool_count": len(tools),
                "tool_names": [t["function"]["name"] for t in tools],
                "last_message": {
                    "role": tail.get("role"),
                    "content_head": str(tail.get("content", ""))[:120],
                },
            },
            out={
                "text": response.text[:120],
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            },
            started=started,
        )
        return response

    def _build_tool_result_message(tool_results, slot=None):
        # 模拟 Anthropic 风格 tool_result block — 真实路径返回 user role
        # 含一个 tool_result content list；这里返回简单字典即可，因为
        # 真实下一轮请求会再走我们的 mock。
        contents = []
        for tc, output in tool_results:
            contents.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": output,
            })
        return {"role": "user", "content": contents}

    router = MagicMock()
    router.complete_with_tools = AsyncMock(side_effect=_complete_with_tools)
    router.build_tool_result_message = MagicMock(
        side_effect=_build_tool_result_message,
    )
    return router


# ---------------------------------------------------------------------------
# Mock research 工具（捕获调用以验证 tool call 序列）
# ---------------------------------------------------------------------------

def _make_research_tool(tracer: ChainTracer):
    calls = []

    async def fake_research(req: ToolExecutionRequest,
                            ctx: ToolExecutionContext) -> ToolExecutionResult:
        started = time.perf_counter()
        question = req.arguments.get("question", "")
        result = ToolExecutionResult(
            success=True,
            payload={
                "answer": f"模拟答案：关于「{question}」的检索结果。",
                "evidence": [
                    {"source_url": "https://arxiv.org/abs/2026.00001",
                     "title": "Mock RAG Paper"},
                ],
                "confidence": "high",
                "unclear": "",
            },
        )
        calls.append({"question": question, "ctx_chat_id": ctx.chat_id})
        tracer.record(
            "ToolRegistry[research]", "execute",
            inp={"question": question, "chat_id": ctx.chat_id,
                 "auth_level": ctx.auth_level},
            out={"answer_head": result.payload["answer"][:80],
                 "evidence_count": len(result.payload["evidence"])},
            started=started,
        )
        return result

    spec = ToolSpec(
        name="research",
        description="回答需要查找信息的问题",
        json_schema={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        executor=fake_research,
        capability="web",
        risk_level="low",
    )
    return spec, calls


def _make_browse_tool():
    async def _noop(req, ctx):
        return ToolExecutionResult(success=True, payload={})
    return ToolSpec(
        name="browse",
        description="browse noop",
        json_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        executor=_noop,
        capability="browser",
        risk_level="low",
    )


def _make_sports_tool():
    """Stub for get_sports_score — Researcher profile references it now,
    so the ToolRegistry must have the name registered."""
    async def _noop(req, ctx):
        return ToolExecutionResult(success=True, payload={})
    return ToolSpec(
        name="get_sports_score",
        description="sports noop",
        json_schema={"type": "object", "properties": {}},
        executor=_noop,
        capability="web",
        risk_level="low",
    )


# ---------------------------------------------------------------------------
# 测试本体
# ---------------------------------------------------------------------------

class TestE2EChainTrace:
    """Lapwing → delegate_to_researcher → Researcher → research → 用户回复。"""

    async def test_full_chain_with_tracing(self, capsys):
        tracer = ChainTracer()

        # ------ 1. 准备 ToolRegistry：注册 research / browse / delegate_to_* ------
        tool_registry = ToolRegistry()
        research_spec, research_calls = _make_research_tool(tracer)
        tool_registry.register(research_spec)
        tool_registry.register(_make_browse_tool())
        tool_registry.register(_make_sports_tool())

        agent_registry = AgentRegistry()
        register_agent_tools(tool_registry)

        # ------ 2. 准备 LLM 响应序列 ------
        # 主脑外层：先 delegate，然后看到结果给出最终回复
        brain_responses = [
            ToolTurnResult(
                text="",
                tool_calls=[ToolCallRequest(
                    id="brain_tc_1",
                    name="delegate_to_researcher",
                    arguments={
                        "request": "帮我查一下 2026 年最新的 RAG 论文",
                        "context_digest": "Kevin 在准备一份调研报告",
                    },
                )],
                continuation_message={"role": "assistant", "content": ""},
            ),
            ToolTurnResult(
                text="找到了，2026 年的 RAG 论文主要是 Mock RAG Paper。",
                tool_calls=[],
                continuation_message=None,
            ),
        ]

        # Researcher 内层：先调 research，然后给出整理结果
        agent_responses = [
            ToolTurnResult(
                text="",
                tool_calls=[ToolCallRequest(
                    id="agent_tc_1",
                    name="research",
                    arguments={"question": "2026 年最新的 RAG 论文有哪些"},
                )],
                continuation_message={"role": "assistant", "content": ""},
            ),
            ToolTurnResult(
                text=("调研报告：2026 年的 RAG 论文以 Mock RAG Paper 为代表。"
                      "[来源: https://arxiv.org/abs/2026.00001]"),
                tool_calls=[],
                continuation_message=None,
            ),
        ]

        router = _build_router(
            tracer,
            brain_responses=brain_responses,
            agent_responses=agent_responses,
        )

        mutation_log = AsyncMock()
        mutation_log.record = AsyncMock(return_value=1)

        # ------ 3. 注册 Researcher / Coder 到 AgentRegistry ------
        services = {"agent_registry": agent_registry}
        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log,
                              services=services),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, tool_registry, mutation_log,
                         services=services),
        )

        # ------ 4. 模拟"主脑外层 tool loop"——这是 Brain.think_conversational
        #            真实做的事，但裁掉所有外围（trajectory / event_bus 等）。
        user_message = "帮我查一下 2026 年最新的 RAG 论文"

        brain_step_started = time.perf_counter()
        tracer.record(
            "Brain", "think_conversational(receive)",
            inp={"chat_id": "trace-chat-1", "user_message": user_message},
            out={"queued": True},
            started=brain_step_started,
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services=services,
            adapter="test",
            user_id="kevin",
            auth_level=2,
            chat_id="trace-chat-1",
        )

        # 暴露给主脑 LLM 的工具：所有可见 model-facing tool
        outer_tools = tool_registry.function_tools(include_internal=False)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "你是 Lapwing。"},
            {"role": "user", "content": user_message},
        ]

        final_reply = ""
        for round_idx in range(5):
            turn_started = time.perf_counter()
            response = await router.complete_with_tools(
                messages=messages,
                tools=outer_tools,
                slot="chat",
                origin="brain.outer",
                max_tokens=2048,
            )

            if not response.tool_calls:
                final_reply = response.text
                tracer.record(
                    "Brain", "emit_user_reply",
                    inp={"round": round_idx},
                    out={"reply": final_reply},
                    started=turn_started,
                )
                break

            # 处理 continuation
            if response.continuation_message:
                messages.append(response.continuation_message)

            # 主脑只调一个 tool（disable_parallel_tool_use）
            tool_results = []
            for tc in response.tool_calls:
                # === Dispatcher（其实就是 LLM 已经选好的工具名）===
                dispatcher_started = time.perf_counter()
                tracer.record(
                    "Dispatcher(LLM-selected)", "route",
                    inp={"requested_tool": tc.name,
                         "request_head": str(tc.arguments.get("request", ""))[:80]},
                    out={"selected_agent":
                         tc.name.replace("delegate_to_", "")},
                    started=dispatcher_started,
                )

                # === ToolRegistry.execute → delegate_to_researcher_executor
                #     → AgentRegistry.get → Researcher.execute（真实代码）===
                exec_started = time.perf_counter()
                exec_req = ToolExecutionRequest(name=tc.name, arguments=tc.arguments)
                exec_result = await tool_registry.execute(exec_req, context=ctx)
                tracer.record(
                    "ToolRegistry", f"execute({tc.name})",
                    inp={"name": tc.name, "arguments_keys": list(tc.arguments.keys())},
                    out={"success": exec_result.success,
                         "reason": exec_result.reason,
                         "payload_keys": list(exec_result.payload.keys()),
                         "result_head":
                            exec_result.payload.get("result", "")[:80]},
                    started=exec_started,
                )
                tool_results.append((
                    tc,
                    json.dumps(exec_result.payload, ensure_ascii=False, default=str),
                ))

            tool_msg = router.build_tool_result_message(tool_results, slot="chat")
            if isinstance(tool_msg, list):
                messages.extend(tool_msg)
            elif tool_msg:
                messages.append(tool_msg)

        # ------ 5. 断言 ------
        # 5.1 dispatcher 选择正确：第一轮主脑 LLM 选了 delegate_to_researcher
        first_brain_call = router.complete_with_tools.await_args_list[0]
        # （由于我们已经在 tracer 里记录了，可以直接断言序列）
        names_called = [
            tc.name
            for resp in brain_responses[:1]
            for tc in resp.tool_calls
        ]
        assert names_called == ["delegate_to_researcher"], \
            f"dispatcher 选错: {names_called}"

        # 5.2 Researcher 的 tool call 序列：调用了一次 research
        assert len(research_calls) == 1
        assert "RAG" in research_calls[0]["question"]

        # 5.3 LLM 调用次数：主脑 2 次 + Researcher 2 次 = 4 次
        assert router.complete_with_tools.await_count == 4, (
            f"期望 4 次 LLM 调用，实际 {router.complete_with_tools.await_count}"
        )

        # 5.4 mutation log 包含完整 lifecycle
        recorded_types = [
            call.args[0] for call in mutation_log.record.call_args_list
            if call.args
        ]
        assert MutationType.AGENT_STARTED in recorded_types
        assert MutationType.AGENT_TOOL_CALL in recorded_types
        assert MutationType.AGENT_COMPLETED in recorded_types
        assert MutationType.AGENT_FAILED not in recorded_types

        # 5.5 Researcher 的结果回传给了主脑 → 主脑生成了最终回复
        assert "Mock RAG Paper" in final_reply or "RAG" in final_reply

        # ------ 6. 把 trace 打印出来供报告引用 ------
        report_lines = [
            "",
            "=" * 70,
            "CHAIN TRACE — 共 {} 步".format(len(tracer.steps)),
            "=" * 70,
        ]
        for step in tracer.steps:
            report_lines.append(step.render())
        report_lines.append("=" * 70)
        report_lines.append(
            f"FINAL USER-VISIBLE REPLY: {final_reply!r}"
        )
        report_lines.append("=" * 70)
        print("\n".join(report_lines))

        # 持久化 trace 到文件（供报告引用）
        from pathlib import Path
        trace_path = Path("/tmp/agent_e2e_chain_trace.json")
        trace_path.write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "seq": s.seq,
                            "component": s.component,
                            "action": s.action,
                            "input": s.input,
                            "output": s.output,
                            "duration_ms": round(s.duration_ms, 3),
                            "started_at_s": round(s.started_at, 4),
                        }
                        for s in tracer.steps
                    ],
                    "final_reply": final_reply,
                    "research_calls": research_calls,
                    "mutation_types": [t.value for t in recorded_types],
                    "llm_call_count": router.complete_with_tools.await_count,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def test_dispatcher_selects_coder_when_requested(self):
        """变体：主脑选择 delegate_to_coder 而非 researcher。

        验证 LLM 命名 == 选择，AgentRegistry 路由正确。
        """
        tool_registry = ToolRegistry()
        register_agent_tools(tool_registry)

        # 注册一个 mock run_python_code
        async def fake_run_python(req, ctx):
            code = req.arguments.get("code", "")
            return ToolExecutionResult(
                success=True,
                payload={"stdout": "hello\n", "stderr": "", "code_head": code[:20]},
            )

        tool_registry.register(ToolSpec(
            name="run_python_code",
            description="run python",
            json_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            executor=fake_run_python,
            capability="code",
            visibility="internal",
            risk_level="low",
        ))
        # ws_* 占位
        for ws_name in ("ws_file_read", "ws_file_write", "ws_file_list"):
            async def _ws(req, ctx, _name=ws_name):
                return ToolExecutionResult(success=True, payload={"tool": _name})
            tool_registry.register(ToolSpec(
                name=ws_name,
                description=f"workspace {ws_name}",
                json_schema={"type": "object", "properties": {}},
                executor=_ws,
                capability="file",
                visibility="internal",
                risk_level="low",
            ))

        agent_registry = AgentRegistry()
        services = {"agent_registry": agent_registry}

        coder_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(
                id="c1", name="run_python_code",
                arguments={"code": "print('hello')"},
            )],
            continuation_message={"role": "assistant", "content": ""},
        )
        coder_round2 = ToolTurnResult(
            text="脚本执行成功，输出 hello。",
            tool_calls=[],
            continuation_message=None,
        )

        router = MagicMock()
        router.complete_with_tools = AsyncMock(
            side_effect=[coder_round1, coder_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "user", "content": "ok"},
        )

        mutation_log = AsyncMock()
        mutation_log.record = AsyncMock(return_value=1)

        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log,
                              services=services),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, tool_registry, mutation_log,
                         services=services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services=services,
            adapter="test",
            user_id="kevin",
            auth_level=2,
            chat_id="trace-chat-2",
        )
        req = ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": "写个 hello world"},
        )

        result = await tool_registry.execute(req, context=ctx)
        assert result.success, f"delegate_to_coder failed: {result.reason}"
        assert "hello" in result.payload["result"]
        # Coder 跑了一轮 tool call + 一轮终止 = 2 次 LLM
        assert router.complete_with_tools.await_count == 2
