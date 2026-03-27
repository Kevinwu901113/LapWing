"""ShellRuntimePolicy 测试。"""

from unittest.mock import MagicMock

from src.core.shell_policy import (
    ExecutionSessionState,
    analyze_command,
    extract_execution_constraints,
    failure_reason_from_result,
    failure_type_from_result,
    infer_permission_denied_alternative,
    should_request_consent_for_command,
    should_validate_after_success,
)
from src.policy.shell_runtime_policy import ShellRuntimePolicy
from src.tools.shell_executor import ShellResult


def _make_policy(verify_constraints):
    return ShellRuntimePolicy(
        analyze_command=analyze_command,
        should_request_consent_for_command=should_request_consent_for_command,
        failure_type_from_result=failure_type_from_result,
        infer_permission_denied_alternative=infer_permission_denied_alternative,
        should_validate_after_success=should_validate_after_success,
        verify_constraints=verify_constraints,
        failure_reason_builder=failure_reason_from_result,
    )


def test_before_execute_returns_require_consent():
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    state = ExecutionSessionState(
        constraints=constraints,
        failure_reason="mkdir: cannot create directory '/home/Lapwing': Permission denied",
        failure_type="permission_denied",
    )
    intent = analyze_command(
        "mkdir -p /home/kevin/Lapwing && printf 'hello\\n' > /home/kevin/Lapwing/note.txt"
    )
    policy = _make_policy(MagicMock())

    decision = policy.before_execute(
        constraints=constraints,
        intent=intent,
        state=state,
    )

    assert decision.action == "require_consent"
    assert decision.alternative is not None
    assert decision.alternative.directory == "/home/kevin/Lapwing"


def test_after_execute_returns_block_on_permission_denied():
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    state = ExecutionSessionState(constraints=constraints)
    intent = analyze_command("mkdir -p /home/Lapwing")
    result = ShellResult(
        stdout="",
        stderr="mkdir: cannot create directory '/home/Lapwing': Permission denied\n",
        return_code=1,
        cwd="/tmp",
    )
    policy = _make_policy(MagicMock())

    decision = policy.after_execute(
        constraints=constraints,
        intent=intent,
        state=state,
        result=result,
        shell_allow_sudo=False,
    )

    assert decision.action == "block"
    assert decision.failure_type == "permission_denied"
    assert "Permission denied" in decision.reason
    assert decision.alternative is not None
    assert decision.alternative.directory.endswith("/Lapwing")


def test_after_execute_returns_should_verify_when_needed():
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    state = ExecutionSessionState(constraints=constraints)
    intent = analyze_command(
        "mkdir -p /home/Lapwing && printf 'hello\\n' > /home/Lapwing/note.txt"
    )
    result = ShellResult(
        stdout="",
        stderr="",
        return_code=0,
        cwd="/tmp",
    )
    policy = _make_policy(MagicMock())

    decision = policy.after_execute(
        constraints=constraints,
        intent=intent,
        state=state,
        result=result,
        shell_allow_sudo=True,
    )

    assert decision.action == "allow"
    assert decision.should_verify is True
