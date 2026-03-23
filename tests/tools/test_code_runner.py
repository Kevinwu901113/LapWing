"""code_runner 集成测试（真实子进程执行）。"""

import pytest
from src.tools.code_runner import run_python


@pytest.mark.asyncio
async def test_simple_print():
    """正常代码执行并返回 stdout。"""
    result = await run_python('print("hello")')
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_arithmetic():
    """数值计算正确性。"""
    result = await run_python("print(2 + 2)")
    assert result.exit_code == 0
    assert result.stdout.strip() == "4"


@pytest.mark.asyncio
async def test_syntax_error_returns_stderr():
    """语法错误时 exit_code != 0，stderr 非空。"""
    result = await run_python("def foo(:\n    pass")
    assert result.exit_code != 0
    assert result.stderr != ""
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_runtime_error_returns_stderr():
    """运行时错误时 exit_code != 0，stderr 包含错误信息。"""
    result = await run_python("1 / 0")
    assert result.exit_code != 0
    assert "ZeroDivisionError" in result.stderr


@pytest.mark.asyncio
async def test_timeout():
    """超时代码被中止，timed_out=True。"""
    result = await run_python("import time; time.sleep(999)", timeout=1)
    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_stdout_truncated():
    """超过 2000 字符的 stdout 被截断。"""
    result = await run_python("print('A' * 3000)")
    assert result.exit_code == 0
    assert len(result.stdout) <= 2000


@pytest.mark.asyncio
async def test_multiline_output():
    """多行输出正确拼接。"""
    code = "for i in range(3):\n    print(i)"
    result = await run_python(code)
    assert result.exit_code == 0
    assert result.stdout.strip() == "0\n1\n2"


@pytest.mark.asyncio
async def test_tmp_dir_isolated():
    """临时目录隔离：脚本在独立目录运行，执行后目录被清理。"""
    import os
    result = await run_python("import os; print(os.getcwd())")
    assert result.exit_code == 0
    cwd = result.stdout.strip()
    # 临时目录应该不再存在（已清理）
    assert not os.path.exists(cwd)
