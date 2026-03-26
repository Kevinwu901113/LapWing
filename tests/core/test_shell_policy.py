"""shell_policy.py 的约束提取与命令分析测试。"""

from src.core.shell_policy import (
    ExecutionSessionState,
    analyze_command,
    extract_execution_constraints,
    infer_permission_denied_alternative,
    should_request_consent_for_command,
)


def test_extract_execution_constraints_binds_directory_and_txt_file():
    constraints = extract_execution_constraints(
        "在 /home 下新建一个 Lapwing 文件夹，然后在文件夹里面新建一个 txt 文件"
    )

    assert constraints.target_directory == "/home/Lapwing"
    assert constraints.required_file_parent == "/home/Lapwing"
    assert constraints.required_extension == ".txt"


def test_analyze_command_distinguishes_diagnostic_and_write():
    diagnostic = analyze_command("ls -la /home")
    write = analyze_command(
        "mkdir -p /home/Lapwing && printf 'hello\\n' > /home/Lapwing/note.txt"
    )

    assert diagnostic.kind == "diagnostic"
    assert diagnostic.is_diagnostic_only is True
    assert write.is_write is True
    assert "/home/Lapwing" in write.write_paths
    assert "/home/Lapwing/note.txt" in write.write_paths


def test_write_to_unapproved_directory_requires_consent():
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

    proposal = should_request_consent_for_command(constraints, intent, state)

    assert proposal is not None
    assert proposal.directory == "/home/kevin/Lapwing"


def test_infer_permission_denied_alternative_maps_home_path():
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    assert constraints.target_directory == "/home/Lapwing"

    alt = infer_permission_denied_alternative(constraints)

    import getpass
    from pathlib import Path
    expected = str(Path.home() / "Lapwing")
    assert alt == expected


def test_infer_permission_denied_alternative_returns_none_for_current_user_home():
    import getpass
    from pathlib import Path
    user_dir = str(Path.home() / "Lapwing")
    constraints = extract_execution_constraints(
        f"在{user_dir}下新建一个txt文件"
    )
    # 直接设置 target_directory 为当前用户 home 下的路径
    constraints.target_directory = user_dir

    alt = infer_permission_denied_alternative(constraints)

    assert alt is None


def test_infer_permission_denied_alternative_returns_none_for_non_home_path():
    constraints = extract_execution_constraints("新建一个 txt 文件")
    constraints.target_directory = "/tmp/Lapwing"

    alt = infer_permission_denied_alternative(constraints)

    assert alt is None
