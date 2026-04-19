"""AgentSpec / AgentMessage / AgentResult 数据模型测试。"""

from datetime import datetime

from src.agents.types import AgentMessage, AgentResult, AgentSpec


class TestAgentSpec:
    def test_required_fields(self):
        spec = AgentSpec(
            name="test",
            description="A test agent",
            system_prompt="You are a test agent.",
            model_slot="agent_execution",
            tools=["web_search"],
        )
        assert spec.name == "test"
        assert spec.model_slot == "agent_execution"
        assert spec.tools == ["web_search"]
        assert spec.runtime_profile is None

    def test_defaults(self):
        spec = AgentSpec(
            name="t", description="t", system_prompt="t",
            model_slot="agent_execution",
        )
        assert spec.tools == []
        assert spec.runtime_profile is None
        assert spec.max_rounds == 15
        assert spec.max_tokens == 30000
        assert spec.timeout_seconds == 180

    def test_runtime_profile_field(self):
        """Step 6 对齐：runtime_profile 取代 tools 列表。"""
        from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE

        spec = AgentSpec(
            name="r", description="r", system_prompt="r",
            model_slot="agent_execution",
            runtime_profile=AGENT_RESEARCHER_PROFILE,
        )
        assert spec.runtime_profile is AGENT_RESEARCHER_PROFILE


class TestAgentMessage:
    def test_fields(self):
        msg = AgentMessage(
            from_agent="lapwing",
            to_agent="team_lead",
            task_id="task_001",
            content="查一下天气",
            message_type="request",
        )
        assert msg.from_agent == "lapwing"
        assert msg.message_type == "request"
        assert isinstance(msg.timestamp, datetime)


class TestAgentResult:
    def test_done(self):
        r = AgentResult(task_id="t1", status="done", result="ok")
        assert r.status == "done"
        assert r.artifacts == []
        assert r.evidence == []
        assert r.attempted_actions == []

    def test_failed(self):
        r = AgentResult(
            task_id="t1", status="failed", result="",
            reason="timeout", attempted_actions=["search", "retry"],
        )
        assert r.reason == "timeout"
        assert len(r.attempted_actions) == 2
