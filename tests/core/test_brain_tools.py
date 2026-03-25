"""brain.py 的 tool loop 集成测试。

新的闭环架构：错误以 JSON 形式返回给 LLM，由 LLM 自主决定下一步。
不再有 consent/约束中断机制。
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.shell_executor import ShellResult


def _tool_turn(
    text: str = "",
    tool_calls: list | None = None,
    continuation_message: dict | None = None,
):
    return SimpleNamespace(
        text=text,
        tool_calls=tool_calls or [],
        continuation_message=continuation_message,
    )


@pytest.fixture(autouse=True)
def reset_module_cache():
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


@pytest.mark.asyncio
class TestBrainTools:
    async def test_normal_chat_without_tool_calls_returns_text(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.router.complete_with_tools = AsyncMock(
                return_value=_tool_turn(text="LLM 回复")
            )
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            result = await brain.think("chat1", "你好")

            assert result == "LLM 回复"
            brain.router.complete_with_tools.assert_called_once()
            brain.memory.append.assert_any_call("chat1", "assistant", "LLM 回复")

    async def test_openai_style_tool_loop_executes_shell_and_returns_final_text(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.return_value = ShellResult(
                stdout="/home/kevin/lapwing\n",
                stderr="",
                return_code=0,
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                name="execute_shell",
                                arguments={"command": "pwd"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_1"}],
                        },
                    ),
                    _tool_turn(text="当前目录在项目根目录。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": "{}",
                }
            )

            result = await brain.think("chat1", "看看当前目录")

            assert result == "当前目录在项目根目录。"
            mock_execute.assert_awaited_once_with("pwd")
            brain.router.build_tool_result_message.assert_called_once()
            tool_results = brain.router.build_tool_result_message.call_args.kwargs["tool_results"]
            assert tool_results[0][0].name == "execute_shell"
            assert '"command": "pwd"' in tool_results[0][1]
            brain.memory.append.assert_any_call("chat1", "assistant", "当前目录在项目根目录。")

    async def test_anthropic_style_tool_loop_uses_provider_continuation_message(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.return_value = ShellResult(
                stdout="README.md\n",
                stderr="",
                return_code=0,
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="toolu_1",
                                name="execute_shell",
                                arguments={"command": "ls"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": [{"type": "tool_use", "id": "toolu_1"}],
                        },
                    ),
                    _tool_turn(text="目录里有 README.md。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "{}",
                        }
                    ],
                }
            )

            result = await brain.think("chat1", "列一下当前目录")

            assert result == "目录里有 README.md。"
            mock_execute.assert_awaited_once_with("ls")
            brain.router.build_tool_result_message.assert_called_once()

    async def test_shell_disabled_falls_back_to_plain_complete(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ENABLED", False):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.router.complete = AsyncMock(return_value="普通回复")
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            result = await brain.think("chat1", "请执行 pwd")

            assert result == "普通回复"
            brain.router.complete.assert_called_once()
            messages = brain.router.complete.call_args.args[0]
            assert "本地 shell 执行当前已禁用" in messages[0]["content"]

    async def test_blocked_shell_result_forwarded_to_llm(self):
        # 被安全系统拦截的命令以 JSON 形式返回给 LLM，由 LLM 生成最终回复
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.return_value = ShellResult(
                stdout="",
                stderr="",
                return_code=-1,
                blocked=True,
                reason="检测到危险命令，已拒绝执行。",
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                name="execute_shell",
                                arguments={"command": "rm -rf /"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_1"}],
                        },
                    ),
                    _tool_turn(text="命令被安全系统拒绝了，无法执行。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": "{}",
                }
            )

            result = await brain.think("chat1", "把根目录删掉")

            # LLM 见到 blocked 结果后自行生成了回复
            assert result == "命令被安全系统拒绝了，无法执行。"
            # shell_executor 被实际调用了（不是在 brain 层拦截）
            mock_execute.assert_awaited_once_with("rm -rf /")
            # blocked 结果以 JSON 形式传给了 LLM
            tool_results = brain.router.build_tool_result_message.call_args.kwargs["tool_results"]
            assert '"blocked": true' in tool_results[0][1]
            assert "检测到危险命令" in tool_results[0][1]

    async def test_tool_loop_limit_returns_stop_message(self):
        # 循环超过上限时返回统一的兜底提示
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.return_value = ShellResult(
                stdout="",
                stderr="",
                return_code=0,
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id=f"call_{i}",
                                name="execute_shell",
                                arguments={"command": "pwd"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": f"call_{i}"}],
                        },
                    )
                    for i in range(1, 9)
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": "{}",
                }
            )

            result = await brain.think("chat1", "一直执行 pwd")

            assert result == "操作步骤太多了，我先暂停一下。"
            assert brain.router.complete_with_tools.await_count == 8

    async def test_permission_denied_error_returned_to_llm_for_autonomous_recovery(self):
        # 核心闭环能力验证：权限拒绝错误返回给 LLM，LLM 自主尝试替代路径，最终成功
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            # 第一条命令失败（权限拒绝），第二条成功（LLM 自己换了路径）
            mock_execute.side_effect = [
                ShellResult(
                    stdout="",
                    stderr="mkdir: cannot create directory '/home/Lapwing': Permission denied\n",
                    return_code=1,
                    cwd="/home/kevin/lapwing",
                ),
                ShellResult(
                    stdout="",
                    stderr="",
                    return_code=0,
                    cwd="/home/kevin/lapwing",
                ),
            ]
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    # LLM 第一次尝试 /home/Lapwing
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                name="execute_shell",
                                arguments={"command": "mkdir -p /home/Lapwing"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_1"}],
                        },
                    ),
                    # LLM 看到 Permission denied 后自主换路径
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_2",
                                name="execute_shell",
                                arguments={"command": "mkdir -p /home/kevin/Lapwing"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_2"}],
                        },
                    ),
                    # LLM 确认成功，生成最终回复
                    _tool_turn(text="已经在 /home/kevin/Lapwing 成功创建了文件夹。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                side_effect=[
                    {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
                    {"role": "tool", "tool_call_id": "call_2", "content": "{}"},
                ]
            )

            result = await brain.think(
                "chat1",
                "在/home下新建一个Lapwing文件夹",
            )

            # 两条命令都执行了，没有中断循环去问用户
            assert mock_execute.await_count == 2
            mock_execute.assert_any_await("mkdir -p /home/Lapwing")
            mock_execute.assert_any_await("mkdir -p /home/kevin/Lapwing")
            # LLM 自主生成了最终回复，不是 consent 消息
            assert "就行" not in result  # 没有 "你回复可以就行"
            assert result == "已经在 /home/kevin/Lapwing 成功创建了文件夹。"
            # 权限拒绝的错误以 JSON 形式传给了 LLM
            first_tool_result = brain.router.build_tool_result_message.call_args_list[0]
            passed_text = first_tool_result.kwargs["tool_results"][0][1]
            assert "Permission denied" in passed_text

    async def test_error_json_contains_stderr_for_llm_context(self):
        # tool result JSON 中包含 stderr，让 LLM 能看到真实错误信息
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.return_value = ShellResult(
                stdout="",
                stderr="mkdir: cannot create directory '/home/Lapwing': Permission denied\n",
                return_code=1,
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                name="execute_shell",
                                arguments={"command": "mkdir /home/Lapwing"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_1"}],
                        },
                    ),
                    _tool_turn(text="权限不足，无法在 /home 下创建目录。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={"role": "tool", "tool_call_id": "call_1", "content": "{}"}
            )

            await brain.think(
                "chat1",
                "在/home下新建一个Lapwing文件夹",
            )

            # 返回给 LLM 的 JSON 中包含 stderr
            tool_results = brain.router.build_tool_result_message.call_args.kwargs["tool_results"]
            result_json = tool_results[0][1]
            assert "Permission denied" in result_json
            assert '"return_code": 1' in result_json
