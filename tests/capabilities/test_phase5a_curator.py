"""Phase 5A: ExperienceCurator tests."""

from __future__ import annotations

import pytest
from src.capabilities.curator import CuratedExperience, CuratorDecision, ExperienceCurator
from src.capabilities.trace_summary import TraceSummary


def _make_trace(**overrides) -> TraceSummary:
    defaults = {
        "trace_id": "trace-1",
        "user_request": "Run the test suite and fix failures",
        "final_result": "All tests pass",
        "task_type": "testing",
        "context": "Python project under src/ and tests/",
        "tools_used": [],
        "files_touched": [],
        "commands_run": [],
        "errors_seen": [],
        "failed_attempts": [],
        "successful_steps": [],
        "verification": [],
        "user_feedback": None,
        "existing_capability_id": None,
        "created_at": "2026-05-01T10:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return TraceSummary.from_dict(defaults)


@pytest.fixture
def curator():
    return ExperienceCurator()


# ── no_action cases ─────────────────────────────────────────────────────


def test_simple_chat_no_action(curator):
    trace = _make_trace(user_request="Hello, how are you?")
    decision = curator.should_reflect(trace)
    assert decision.should_create is False
    assert decision.recommended_action == "no_action"
    assert "simple chat" in decision.reasons[0].lower()


def test_no_reusable_procedure(curator):
    trace = _make_trace(
        user_request="tell me about Python",
        tools_used=["web_search"],
        context="general inquiry",
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is False
    assert "no reusable" in decision.reasons[0].lower()


def test_contains_secrets_no_action(curator):
    trace = _make_trace(
        user_request="Use sk-abcdefghijklmnopqrstuvwxyz123456 to call API",
        tools_used=["execute_shell"],
        commands_run=["curl api.example.com"],
        successful_steps=["called API"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is False


# ── Create signals ──────────────────────────────────────────────────────


def test_many_tools_creates(curator):
    trace = _make_trace(
        tools_used=["shell", "read_file", "write_file", "web_search", "python"],
        successful_steps=["step1", "step2"],
        commands_run=["cmd1", "cmd2"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "tools_used >= 5" in decision.reasons[0]


def test_failed_then_succeeded_creates(curator):
    trace = _make_trace(
        tools_used=["execute_shell", "read_file"],
        commands_run=["pytest tests/", "git diff"],
        errors_seen=["AssertionError"],
        failed_attempts=["initial fix didn't work"],
        successful_steps=["found root cause", "applied correct fix", "tests pass"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "failed_then_succeeded" in decision.reasons[0]


def test_failed_then_succeeded_shell_is_workflow(curator):
    trace = _make_trace(
        tools_used=["execute_shell"],
        commands_run=["pytest tests/", "git diff", "git add -p"],
        errors_seen=["AssertionError"],
        failed_attempts=["wrong fix"],
        successful_steps=["correct fix"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "workflow" in decision.recommended_action


def test_user_correction_detected(curator):
    trace = _make_trace(
        user_feedback="No, that's wrong — fix it instead by updating the config",
        tools_used=["read_file", "write_file"],
        successful_steps=["updated config"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "correction" in decision.reasons[0].lower()


def test_repeated_task_pattern(curator):
    trace = _make_trace(
        tools_used=["shell", "python"],
        successful_steps=["done"],
        commands_run=["cmd1", "cmd2"],
        context="",
        metadata={"repetition_count": 3},
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "repeated" in decision.reasons[0].lower()


def test_repeated_task_pattern_by_task_type(curator):
    trace = _make_trace(
        task_type="weekly-deploy",
        tools_used=["shell"],
        successful_steps=["deployed"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True


def test_contains_file_patch(curator):
    trace = _make_trace(
        tools_used=["execute_shell", "read_file"],
        commands_run=["sed -i 's/old/new/' src/app.py", "git diff"],
        files_touched=["src/app.py"],
        successful_steps=["patch applied"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True


def test_contains_shell_workflow(curator):
    trace = _make_trace(
        commands_run=["cd src/", "grep -r TODO .", "pytest -x && echo done", "git status"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "shell workflow" in decision.reasons[0].lower()


def test_user_requested_reuse(curator):
    trace = _make_trace(
        user_request="Create a skill to automate this deployment workflow",
        tools_used=["shell"],
        successful_steps=["deployed"],
        commands_run=["kubectl apply"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert "reuse" in decision.reasons[0].lower()


def test_existing_capability_failed(curator):
    trace = _make_trace(
        existing_capability_id="workspace_abc123",
        tools_used=["shell"],
        errors_seen=["capability produced wrong output"],
        successful_steps=["manual fix worked"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert decision.recommended_action == "patch_existing_proposal"


def test_project_specific_workflow(curator):
    trace = _make_trace(
        context="Working on src/ directory and tests/ suite for this project",
        tools_used=["shell", "python"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True
    assert decision.recommended_action == "create_project_playbook_draft"


def test_non_obvious_env_setup(curator):
    trace = _make_trace(
        commands_run=["source venv/bin/activate", "pip install -r requirements.txt", "pytest"],
        successful_steps=["setup complete"],
    )
    decision = curator.should_reflect(trace)
    assert decision.should_create is True


def test_stable_verification_boosts_confidence(curator):
    trace = _make_trace(
        tools_used=["shell", "read_file", "write_file"],
        commands_run=["pytest", "git diff"],
        successful_steps=["step1", "step2", "step3"],
        verification=["all checks pass", "coverage >= 80%"],
    )
    decision = curator.should_reflect(trace)
    assert decision.confidence >= 0.6  # base 0.5 + verification boost 0.1


# ── Risk level detection ────────────────────────────────────────────────


def test_dangerous_command_high_risk(curator):
    trace = _make_trace(
        commands_run=["rm -rf /tmp/build"],
        tools_used=["shell"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "high"


def test_permission_denied_high_risk(curator):
    trace = _make_trace(
        errors_seen=["permission denied: /etc/config"],
        tools_used=["shell", "python"],
        successful_steps=["alternative approach worked"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "high"


def test_system_paths_high_risk(curator):
    trace = _make_trace(
        files_touched=["/etc/nginx/nginx.conf"],
        tools_used=["shell"],
        successful_steps=["config updated"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "high"


def test_multi_command_medium_risk(curator):
    trace = _make_trace(
        commands_run=["cmd1", "cmd2", "cmd3"],
        tools_used=["execute_shell"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "medium"


def test_files_touched_medium_risk(curator):
    trace = _make_trace(
        files_touched=["src/main.py"],
        tools_used=["read_file", "write_file"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "medium"


def test_minimal_trace_low_risk(curator):
    trace = _make_trace(
        tools_used=["web_search", "read_file", "recall", "write_note", "list_notes"],
        successful_steps=["found the answer"],
    )
    decision = curator.should_reflect(trace)
    assert decision.risk_level == "low"


# ── Determinism ─────────────────────────────────────────────────────────


def test_same_input_same_output(curator):
    trace = _make_trace(
        tools_used=["shell", "read_file", "write_file", "python", "web_search"],
        commands_run=["cmd1", "cmd2", "cmd3"],
        successful_steps=["s1", "s2"],
        verification=["done"],
    )
    decisions = [curator.should_reflect(trace) for _ in range(5)]
    for d in decisions:
        assert d.should_create == decisions[0].should_create
        assert d.recommended_action == decisions[0].recommended_action
        assert d.confidence == decisions[0].confidence
        assert d.risk_level == decisions[0].risk_level


# ── summarize ───────────────────────────────────────────────────────────


def test_summarize_produces_all_fields(curator):
    trace = _make_trace(
        user_request="Deploy the app to production",
        context="Kubernetes cluster, production namespace",
        tools_used=["execute_shell", "read_file"],
        files_touched=["k8s/deploy.yaml", "k8s/service.yaml"],
        commands_run=["kubectl apply -f k8s/", "kubectl get pods"],
        successful_steps=["Applied manifests", "Verified pods running"],
        verification=["All pods healthy"],
        errors_seen=["ImagePullBackOff, fixed by updating tag"],
        failed_attempts=["First deploy used wrong image tag"],
    )
    experience = curator.summarize(trace)
    assert experience.problem
    assert experience.context
    assert experience.successful_steps
    assert experience.failed_attempts
    assert experience.key_commands
    assert experience.key_files
    assert experience.required_tools
    assert experience.verification
    assert experience.pitfalls
    assert experience.generalization_boundary
    assert experience.recommended_capability_type in ("skill", "workflow", "project_playbook")
    assert experience.source_trace_id == "trace-1"


def test_summarize_handles_empty_trace(curator):
    trace = _make_trace(user_request="minimal")
    experience = curator.summarize(trace)
    assert isinstance(experience, CuratedExperience)
    assert experience.successful_steps == []


# ── propose_capability ──────────────────────────────────────────────────


def test_propose_capability_generates_valid_proposal(curator):
    trace = _make_trace(
        user_request="Fix and deploy the auth service",
        context="Kubernetes cluster",
        tools_used=["execute_shell", "read_file", "write_file"],
        commands_run=["kubectl apply", "pytest"],
        successful_steps=["Fixed auth bug", "Tests pass", "Deployed"],
        verification=["Auth endpoint returns 200"],
    )
    experience = curator.summarize(trace)
    proposal = curator.propose_capability(experience, scope="workspace")
    assert proposal.proposal_id.startswith("prop_")
    assert proposal.proposed_capability_id.startswith("workspace_")
    assert proposal.name
    assert proposal.type in ("skill", "workflow", "project_playbook")
    assert proposal.scope == "workspace"
    assert proposal.maturity == "draft"
    assert proposal.status == "active"
    assert proposal.body_markdown
    assert "## When to use" in proposal.body_markdown
    assert "## Verification" in proposal.body_markdown
    assert "## Failure handling" in proposal.body_markdown
    assert "## Generalization boundary" in proposal.body_markdown
    assert "## Source trace" in proposal.body_markdown


def test_propose_capability_high_risk_requires_approval(curator):
    trace = _make_trace(
        user_request="Clean up old Docker images",
        commands_run=["sudo rm -rf /var/lib/docker", "docker system prune", "echo done"],
        tools_used=["execute_shell", "docker"],
        successful_steps=["done"],
    )
    decision = curator.should_reflect(trace)
    experience = curator.summarize(trace)
    proposal = curator.propose_capability(experience, scope="workspace", risk_level=decision.risk_level)
    assert decision.risk_level == "high"
    assert proposal.risk_level == "high"
    assert proposal.required_approval is True


def test_propose_capability_custom_name(curator):
    trace = _make_trace(user_request="do stuff")
    experience = curator.summarize(trace)
    proposal = curator.propose_capability(experience, scope="user", name="My Custom Capability")
    assert proposal.name == "My Custom Capability"


def test_propose_capability_custom_id(curator):
    trace = _make_trace(
        user_request="test",
        tools_used=["shell", "read_file", "write_file", "python", "web_search"],
        commands_run=["cmd1", "cmd2"],
        successful_steps=["done"],
    )
    experience = curator.summarize(trace)
    proposal = curator.propose_capability(experience, scope="workspace", proposed_id="prop_custom01")
    assert proposal.proposal_id == "prop_custom01"


def test_propose_capability_rejects_path_traversal_id(curator):
    trace = _make_trace(
        user_request="test",
        tools_used=["shell", "read_file", "write_file", "python", "web_search"],
        commands_run=["cmd1", "cmd2"],
        successful_steps=["done"],
    )
    experience = curator.summarize(trace)
    with pytest.raises(ValueError, match="unsafe"):
        curator.propose_capability(experience, scope="workspace", proposed_id="../etc/malicious")


def test_propose_capability_rejects_slash_in_id(curator):
    trace = _make_trace(
        user_request="test",
        tools_used=["shell", "read_file"],
        successful_steps=["done"],
    )
    experience = curator.summarize(trace)
    with pytest.raises(ValueError, match="unsafe"):
        curator.propose_capability(experience, scope="workspace", proposed_id="sub/dir/prop")
