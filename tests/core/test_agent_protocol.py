"""AgentProtocol 数据类型单元测试。"""

from __future__ import annotations

import time

from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentCommandPriority,
    AgentEmit,
    AgentEmitState,
    AgentGuidance,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    GuidanceOption,
)


# ---------- Enum 值测试 ----------

def test_enum_values_match_strings():
    assert AgentUrgency.IMMEDIATE == "immediate"
    assert AgentUrgency.SOON == "soon"
    assert AgentUrgency.LATER == "later"

    assert AgentNotifyKind.RESULT == "result"
    assert AgentNotifyKind.PROGRESS == "progress"
    assert AgentNotifyKind.ERROR == "error"
    assert AgentNotifyKind.QUESTION == "question"

    assert AgentCommandIntent.EXECUTE == "execute"
    assert AgentCommandIntent.PAUSE == "pause"
    assert AgentCommandIntent.RESUME == "resume"
    assert AgentCommandIntent.CANCEL == "cancel"
    assert AgentCommandIntent.CONTEXT == "context"

    assert AgentCommandPriority.CRITICAL == "critical"
    assert AgentCommandPriority.HIGH == "high"
    assert AgentCommandPriority.NORMAL == "normal"
    assert AgentCommandPriority.LOW == "low"

    assert AgentEmitState.QUEUED == "queued"
    assert AgentEmitState.WORKING == "working"
    assert AgentEmitState.DONE == "done"
    assert AgentEmitState.FAILED == "failed"
    assert AgentEmitState.BLOCKED == "blocked"
    assert AgentEmitState.CANCELLED == "cancelled"


# ---------- AgentNotify 测试 ----------

def test_agent_notify_defaults():
    notify = AgentNotify(
        agent_name="test_agent",
        kind=AgentNotifyKind.RESULT,
        urgency=AgentUrgency.SOON,
        headline="任务完成",
    )
    assert notify.agent_name == "test_agent"
    assert notify.kind == AgentNotifyKind.RESULT
    assert notify.urgency == AgentUrgency.SOON
    assert notify.headline == "任务完成"
    # 默认值断言
    assert notify.detail is None
    assert notify.payload is None
    assert notify.ref_command_id is None
    assert notify.id is not None
    assert len(notify.id) > 0
    assert notify.created_at > 0


def test_agent_notify_all_fields():
    before = time.time()
    notify = AgentNotify(
        agent_name="search_agent",
        kind=AgentNotifyKind.ERROR,
        urgency=AgentUrgency.IMMEDIATE,
        headline="搜索失败",
        detail="网络超时",
        payload={"error_code": 504, "url": "https://example.com"},
        id="abc12345",
        ref_command_id="cmd-xyz",
    )
    after = time.time()

    assert notify.detail == "网络超时"
    assert notify.payload == {"error_code": 504, "url": "https://example.com"}
    assert notify.id == "abc12345"
    assert notify.ref_command_id == "cmd-xyz"
    assert before <= notify.created_at <= after


def test_agent_notify_id_is_unique():
    n1 = AgentNotify(agent_name="a", kind=AgentNotifyKind.PROGRESS, urgency=AgentUrgency.LATER, headline="h1")
    n2 = AgentNotify(agent_name="a", kind=AgentNotifyKind.PROGRESS, urgency=AgentUrgency.LATER, headline="h2")
    assert n1.id != n2.id


# ---------- AgentCommand 测试 ----------

def test_agent_command_defaults():
    cmd = AgentCommand(
        target_agent="browser_agent",
        intent=AgentCommandIntent.EXECUTE,
        task_description="搜索最新新闻",
    )
    assert cmd.target_agent == "browser_agent"
    assert cmd.intent == AgentCommandIntent.EXECUTE
    assert cmd.task_description == "搜索最新新闻"
    # 默认值断言
    assert cmd.priority == AgentCommandPriority.NORMAL
    assert cmd.interrupt is False
    assert cmd.guidance is None
    assert cmd.context is None
    assert cmd.max_steps == 20
    assert cmd.timeout_seconds == 300
    assert cmd.id is not None
    assert cmd.created_at > 0


def test_agent_command_with_guidance():
    opt1 = GuidanceOption(
        label="快速搜索",
        steps=["打开浏览器", "输入关键词", "返回结果"],
        rationale="速度优先",
        risk="low",
    )
    opt2 = GuidanceOption(
        label="深度分析",
        steps=["多页面搜索", "交叉验证", "汇总"],
        risk="medium",
    )
    guidance = AgentGuidance(
        options=[opt1, opt2],
        persona_hints={"tone": "concise"},
    )
    cmd = AgentCommand(
        target_agent="research_agent",
        intent=AgentCommandIntent.EXECUTE,
        task_description="研究量子计算",
        priority=AgentCommandPriority.HIGH,
        interrupt=True,
        guidance=guidance,
        context={"user_id": "kevin", "depth": 3},
        max_steps=50,
        timeout_seconds=600,
    )
    assert cmd.priority == AgentCommandPriority.HIGH
    assert cmd.interrupt is True
    assert cmd.guidance is guidance
    assert len(cmd.guidance.options) == 2
    assert cmd.guidance.options[0].label == "快速搜索"
    assert cmd.guidance.options[1].risk == "medium"
    assert cmd.guidance.options[1].rationale is None
    assert cmd.guidance.persona_hints == {"tone": "concise"}
    assert cmd.context == {"user_id": "kevin", "depth": 3}
    assert cmd.max_steps == 50
    assert cmd.timeout_seconds == 600


def test_guidance_option_default_rationale():
    opt = GuidanceOption(label="简单方案", steps=["step1"])
    assert opt.rationale is None
    assert opt.risk == "low"


# ---------- AgentEmit 测试 ----------

def test_agent_emit_without_progress():
    emit = AgentEmit(
        agent_name="shell_agent",
        ref_id="cmd-001",
        state=AgentEmitState.WORKING,
    )
    assert emit.agent_name == "shell_agent"
    assert emit.ref_id == "cmd-001"
    assert emit.state == AgentEmitState.WORKING
    assert emit.progress is None
    assert emit.note is None
    assert emit.payload is None
    assert emit.id is not None
    assert emit.created_at > 0


def test_agent_emit_with_progress():
    emit = AgentEmit(
        agent_name="shell_agent",
        ref_id="cmd-002",
        state=AgentEmitState.WORKING,
        progress=0.75,
        note="已完成 3/4 步骤",
        payload={"current_step": 3, "total_steps": 4},
    )
    assert emit.progress == 0.75
    assert emit.note == "已完成 3/4 步骤"
    assert emit.payload == {"current_step": 3, "total_steps": 4}


def test_agent_emit_done_state():
    emit = AgentEmit(
        agent_name="shell_agent",
        ref_id="cmd-003",
        state=AgentEmitState.DONE,
        progress=1.0,
    )
    assert emit.state == AgentEmitState.DONE
    assert emit.progress == 1.0


def test_agent_emit_failed_state():
    emit = AgentEmit(
        agent_name="shell_agent",
        ref_id="cmd-004",
        state=AgentEmitState.FAILED,
        note="命令执行超时",
    )
    assert emit.state == AgentEmitState.FAILED
    assert emit.note == "命令执行超时"
