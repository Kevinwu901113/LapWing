"""brain.py 的 tool loop 集成测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.shell_policy import PendingShellConfirmation, VerificationStatus
from src.tools.shell_executor import ShellResult
from src.tools.web_fetcher import FetchResult


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
    import config.settings as _settings
    _orig_budget = _settings.TASK_NO_ACTION_BUDGET
    _orig_burst = _settings.TASK_ERROR_BURST_THRESHOLD
    _settings.TASK_NO_ACTION_BUDGET = 0  # 禁用 NoActionBudget，避免测试需要多轮响应
    _settings.TASK_ERROR_BURST_THRESHOLD = 99  # 禁用 ErrorBurstGuard
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod or "shell_policy" in mod or "task_runtime" in mod:
            del sys.modules[mod]
    yield
    _settings.TASK_NO_ACTION_BUDGET = _orig_budget
    _settings.TASK_ERROR_BURST_THRESHOLD = _orig_burst
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod or "shell_policy" in mod or "task_runtime" in mod:
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

    async def test_think_strips_internal_thinking_tags_before_store_and_return(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.router.complete_with_tools = AsyncMock(
                return_value=_tool_turn(text="可见<think>内部思考</think>内容")
            )

            result = await brain.think("chat1", "你好")

            assert "<think>" not in result.lower()
            assert "内部思考" not in result
            assert result == "可见内容"
            brain.memory.append.assert_any_call("chat1", "assistant", "可见内容")

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

    async def test_shell_disabled_no_shell_tools_in_prompt(self):
        """shell 和 web 禁用时，system prompt 中包含禁用状态说明，工具循环仍可用于 memory_note。"""
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ENABLED", False), \
             patch("src.core.brain.CHAT_WEB_TOOLS_ENABLED", False):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.compactor.try_compact = AsyncMock()
            brain.task_runtime.complete_chat = AsyncMock(return_value="普通回复")

            result = await brain.think("chat1", "请执行 pwd")

            assert result == "普通回复"

    async def test_web_tool_loop_search_then_fetch_returns_final_reply(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ENABLED", False), \
             patch("src.core.brain.CHAT_WEB_TOOLS_ENABLED", True), \
             patch("src.tools.web_search.search", new_callable=AsyncMock) as mock_search, \
             patch("src.tools.web_fetcher.fetch", new_callable=AsyncMock) as mock_fetch:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            # Phase 4: 注册个人工具（web_search/web_fetch 现在由 personal_tools 注册）
            from src.tools.personal_tools import register_personal_tools
            register_personal_tools(brain.tool_registry, {})
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_search.return_value = [
                {
                    "title": "A股收盘快讯",
                    "url": "https://finance.example/a-share-close",
                    "snippet": "上证指数今日收盘上涨。",
                }
            ]
            mock_fetch.return_value = FetchResult(
                url="https://finance.example/a-share-close",
                title="A股收盘快讯",
                text="上证指数收于 3200 点，沪深两市成交额放大。",
                success=True,
                error="",
            )
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_web_1",
                                name="web_search",
                                arguments={"query": "今天 A股 收盘", "max_results": 3},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_web_1"}],
                        },
                    ),
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_web_2",
                                name="web_fetch",
                                arguments={"url": "https://finance.example/a-share-close"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_web_2"}],
                        },
                    ),
                    _tool_turn(text="今天A股已收盘，来源：https://finance.example/a-share-close"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                side_effect=[
                    {
                        "role": "tool",
                        "tool_call_id": "call_web_1",
                        "name": "web_search",
                        "content": "{}",
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_web_2",
                        "name": "web_fetch",
                        "content": "{}",
                    },
                ]
            )

            result = await brain.think("chat1", "查一下今天A股收盘信息")

            assert "https://finance.example/a-share-close" in result
            mock_search.assert_awaited_once_with("今天 A股 收盘", max_results=5)
            mock_fetch.assert_awaited_once_with("https://finance.example/a-share-close")
            assert brain.router.complete_with_tools.await_count == 3

    async def test_blocked_shell_result_returns_non_fabricated_fallback(self):
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
                    _tool_turn(text="本地命令没有执行。检测到危险命令，已拒绝执行。"),
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

            assert "本地命令没有执行" in result
            # VitalGuard 或 shell_executor 任意一层拦截都合法
            assert "检测到危险命令" in result or "VitalGuard" in result or "伤害" in result

    async def test_tool_loop_limit_returns_fallback_reply(self):
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
                reason="命令一直被要求重复执行。",
                cwd="/home/kevin/lapwing",
            )
            max_rounds = brain.task_runtime._max_tool_rounds
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id=f"call_{index}",
                                name="execute_shell",
                                arguments={"command": "pwd"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": f"call_{index}"}],
                        },
                    )
                    for index in range(1, max_rounds + 1)
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

            assert "本地命令没有执行" in result
            assert "命令一直被要求重复执行" in result
            assert brain.router.complete_with_tools.await_count == max_rounds

    async def test_failed_absolute_path_triggers_proactive_consent_after_first_command(self):
        # 权限拒绝后，主动恢复流程在第一条命令失败后立即触发，不需要 LLM 再尝试替代路径
        # （仅在 SHELL_ALLOW_SUDO=False 时生效）
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ALLOW_SUDO", False), \
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
                return_value=_tool_turn(
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
                )
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": "{}",
                }
            )

            msg = "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
            # LLM 通过工具参数指定目录，不依赖正则推断
            with patch("src.core.brain.extract_execution_constraints") as mock_constraints:
                from src.core.shell_policy import ExecutionConstraints
                mock_constraints.return_value = ExecutionConstraints(
                    original_user_message=msg,
                    target_directory="/home/Lapwing",
                    is_write_request=True,
                )
                result = await brain.think("chat1", msg)

            # 只执行了第一条命令，立即触发 consent
            assert mock_execute.await_count == 1
            mock_execute.assert_any_await("mkdir -p /home/Lapwing")
            assert "原请求还没有完成" in result
            assert "Permission denied" in result
            assert "/home/kevin/Lapwing" in result
            assert "就行" in result
            assert "chat1" in brain.task_runtime._pending_shell_confirmations


    async def test_confirmation_reply_resumes_with_approved_directory_and_verifies_success(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute, \
             patch("src.core.brain.verify_constraints") as mock_verify:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.task_runtime._pending_shell_confirmations["chat1"] = PendingShellConfirmation(
                original_user_message="在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件",
                alternative_directory="/home/kevin/Lapwing",
                reason="mkdir: cannot create directory '/home/Lapwing': Permission denied",
            )

            mock_execute.return_value = ShellResult(
                stdout="",
                stderr="",
                return_code=0,
                cwd="/home/kevin/lapwing",
            )
            mock_verify.return_value = VerificationStatus(
                completed=True,
                directory_path="/home/kevin/Lapwing",
                file_path="/home/kevin/Lapwing/note.txt",
                file_content="hello",
            )
            brain.router.complete_with_tools = AsyncMock(
                return_value=_tool_turn(
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            name="execute_shell",
                            arguments={
                                "command": (
                                    "mkdir -p /home/kevin/Lapwing && "
                                    "printf 'hello\\n' > /home/kevin/Lapwing/note.txt"
                                )
                            },
                        )
                    ],
                    continuation_message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "call_1"}],
                    },
                )
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": "{}",
                }
            )

            # 确认消息包含目标路径（关键词匹配已禁用，通过路径匹配触发）
            # extract_execution_constraints 提供写入意图，模拟 LLM 工具参数
            with patch("src.core.brain.extract_execution_constraints") as mock_constraints:
                from src.core.shell_policy import ExecutionConstraints
                mock_constraints.return_value = ExecutionConstraints(
                    original_user_message="在/home下新建一个Lapwing文件夹",
                    target_directory="/home/kevin/Lapwing",
                    is_write_request=True,
                    approved_directory="/home/kevin/Lapwing",
                )
                result = await brain.think("chat1", "/home/kevin/Lapwing 可以")

            mock_execute.assert_awaited_once_with(
                "mkdir -p /home/kevin/Lapwing && "
                "printf 'hello\\n' > /home/kevin/Lapwing/note.txt"
            )
            assert "原请求已经完成了" in result
            assert "/home/kevin/Lapwing/note.txt" in result
            assert "hello" in result
            assert "chat1" not in brain.task_runtime._pending_shell_confirmations

            routed_messages = brain.router.complete_with_tools.call_args.args[0]
            assert "用户已经同意改到 `/home/kevin/Lapwing`" in routed_messages[-1]["content"]

    async def test_shell_state_context_merges_into_single_system_message(self):
        with patch("src.core.brain.load_prompt", return_value="基础人格prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SOUL_PATH", Path("/nonexistent/soul.md")):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.router.complete_with_tools = AsyncMock(
                return_value=_tool_turn(text="好的")
            )

            await brain.think("chat1", "在/home下新建一个Lapwing文件夹")

            routed_messages = brain.router.complete_with_tools.call_args.args[0]
            system_messages = [m for m in routed_messages if m.get("role") == "system"]
            assert len(system_messages) == 1
            assert "基础人格prompt" in system_messages[0]["content"]
            assert "## 当前 Shell 任务状态" in system_messages[0]["content"]

    async def test_write_task_does_not_return_last_stdout_when_unfinished(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute, \
             patch("src.core.brain.extract_execution_constraints") as mock_constraints:
            from src.core.brain import LapwingBrain
            from src.core.shell_policy import ExecutionConstraints

            _msg = "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
            mock_constraints.return_value = ExecutionConstraints(
                original_user_message=_msg,
                target_directory="/home/Lapwing",
                is_write_request=True,
            )

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            mock_execute.side_effect = [
                ShellResult(
                    stdout="",
                    stderr="mkdir: cannot create directory '/home/Lapwing': Permission denied\n",
                    return_code=1,
                    cwd="/home/kevin/lapwing",
                ),
                ShellResult(
                    stdout="lapwing-core\n",
                    stderr="",
                    return_code=0,
                    cwd="/home/kevin/lapwing",
                ),
            ]
            brain.router.complete_with_tools = AsyncMock(
                side_effect=[
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
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_2",
                                name="execute_shell",
                                arguments={"command": "cat /etc/hostname"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_2"}],
                        },
                    ),
                    _tool_turn(text="原请求还没有完成"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                side_effect=[
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "name": "execute_shell",
                        "content": "{}",
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_2",
                        "name": "execute_shell",
                        "content": "{}",
                    },
                ]
            )

            result = await brain.think(
                "chat1",
                "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件",
            )

            assert "原请求还没有完成" in result
            assert "lapwing-core" not in result

    async def test_permission_denied_consent_message_contains_real_stderr_error(self):
        # 权限拒绝时，consent 消息应该包含真实的 stderr 错误，而不是通用的"退出码 1"
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute, \
             patch("src.core.brain.extract_execution_constraints") as mock_constraints:
            from src.core.brain import LapwingBrain
            from src.core.shell_policy import ExecutionConstraints

            _msg = "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
            mock_constraints.return_value = ExecutionConstraints(
                original_user_message=_msg,
                target_directory="/home/Lapwing",
                is_write_request=True,
            )

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
                return_value=_tool_turn(
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
                )
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={"role": "tool", "tool_call_id": "call_1", "content": "{}"}
            )

            result = await brain.think(
                "chat1",
                _msg,
            )

            # 错误原因应该是真实 stderr，不是通用"退出码 1"
            assert "Permission denied" in result
            assert "退出码 1" not in result

    async def test_shell_allow_sudo_skips_proactive_consent_on_permission_denied(self):
        # SHELL_ALLOW_SUDO=True 时，权限拒绝不触发 consent，让 LLM 自己决定是否 sudo
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ALLOW_SUDO", True), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute:
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

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
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                name="execute_shell",
                                arguments={"command": "mkdir /home/Lapwing"},
                            )
                        ],
                        continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
                    ),
                    _tool_turn(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_2",
                                name="execute_shell",
                                arguments={"command": "sudo mkdir /home/Lapwing"},
                            )
                        ],
                        continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_2"}]},
                    ),
                    _tool_turn(text="搞定了，目录已创建。"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(return_value={"role": "tool", "content": "{}"})

            result = await brain.think(
                "chat1",
                "在/home下新建一个Lapwing文件夹",
            )

            # consent 没有被触发，任务继续执行直到完成
            assert "chat1" not in brain.task_runtime._pending_shell_confirmations
            assert mock_execute.await_count == 2
            # 最终结果是完成消息（LLM 文本或 verify_constraints 成功消息）
            assert "完成" in result or "搞定了" in result

    async def test_permission_denied_under_current_user_home_does_not_trigger_proactive_consent(self):
        # 当目标已在当前用户 home 下时，不触发主动 consent
        import getpass
        from pathlib import Path as _Path

        current_home = str(_Path.home())
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

            target = f"{current_home}/projects/test"
            mock_execute.return_value = ShellResult(
                stdout="",
                stderr=f"mkdir: cannot create directory '{target}': Permission denied\n",
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
                                arguments={"command": f"mkdir {target}"},
                            )
                        ],
                        continuation_message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "call_1"}],
                        },
                    ),
                    _tool_turn(text="文件夹已创建"),
                ]
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={"role": "tool", "tool_call_id": "call_1", "content": "{}"}
            )

            # 构造一个指向当前用户 home 下目录的写请求
            msg = f"在{current_home}下新建一个projects/test文件夹"
            # 直接 patch extract_execution_constraints 使 target_directory 在 home 下
            with patch("src.core.brain.extract_execution_constraints") as mock_constraints:
                from src.core.shell_policy import ExecutionConstraints
                mock_constraints.return_value = ExecutionConstraints(
                    original_user_message=msg,
                    target_directory=target,
                    is_write_request=True,
                )
                result = await brain.think("chat1", msg)

            # 目标已在当前用户 home 下，不应触发主动 consent
            assert "chat1" not in brain.task_runtime._pending_shell_confirmations

    async def test_task_events_emitted_for_successful_tool_loop(self):
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
            brain.event_bus = MagicMock()
            brain.event_bus.publish = AsyncMock()

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

            event_calls = brain.event_bus.publish.await_args_list
            event_types = [call.args[0] for call in event_calls]
            assert event_types[0] == "task.started"
            assert event_types[1] == "task.planning"
            assert event_types[2] == "task.executing"
            assert "task.completed" in event_types

            for call in event_calls:
                payload = call.args[1]
                assert "task_id" in payload
                assert payload["chat_id"] == "chat1"
                assert "phase" in payload
                assert "text" in payload

    async def test_task_blocked_event_emitted_when_consent_required(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.SHELL_ALLOW_SUDO", False), \
             patch("src.core.brain.execute_shell", new_callable=AsyncMock) as mock_execute, \
             patch("src.core.brain.extract_execution_constraints") as mock_constraints:
            from src.core.brain import LapwingBrain
            from src.core.shell_policy import ExecutionConstraints

            _msg = "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
            mock_constraints.return_value = ExecutionConstraints(
                original_user_message=_msg,
                target_directory="/home/Lapwing",
                is_write_request=True,
            )

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.event_bus = MagicMock()
            brain.event_bus.publish = AsyncMock()

            mock_execute.return_value = ShellResult(
                stdout="",
                stderr="mkdir: cannot create directory '/home/Lapwing': Permission denied\n",
                return_code=1,
                cwd="/home/kevin/lapwing",
            )
            brain.router.complete_with_tools = AsyncMock(
                return_value=_tool_turn(
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
                )
            )
            brain.router.build_tool_result_message = MagicMock(
                return_value={"role": "tool", "tool_call_id": "call_1", "content": "{}"}
            )

            result = await brain.think("chat1", _msg)

            assert "原请求还没有完成" in result
            event_calls = brain.event_bus.publish.await_args_list
            event_types = [call.args[0] for call in event_calls]
            assert event_types[0] == "task.started"
            assert event_types[1] == "task.planning"
            assert "task.executing" in event_types
            assert "task.tool_execution_start" in event_types
            assert "task.tool_execution_update" in event_types
            assert "task.tool_execution_end" in event_types
            assert event_types[-1] == "task.blocked"

            blocked_payload = event_calls[-1].args[1]
            assert blocked_payload["chat_id"] == "chat1"
            assert blocked_payload["phase"] == "blocked"
            assert "reason" in blocked_payload
            assert "Permission denied" in blocked_payload["reason"]

    async def test_task_failed_event_emitted_when_write_objective_unfinished(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"), \
             patch("src.core.brain.extract_execution_constraints") as mock_constraints:
            from src.core.brain import LapwingBrain
            from src.core.shell_policy import ExecutionConstraints

            _msg = "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
            mock_constraints.return_value = ExecutionConstraints(
                original_user_message=_msg,
                target_directory="/home/Lapwing",
                is_write_request=True,
            )

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.event_bus = MagicMock()
            brain.event_bus.publish = AsyncMock()
            brain.router.complete_with_tools = AsyncMock(return_value=_tool_turn(text="原请求还没有完成"))

            result = await brain.think("chat1", _msg)

            assert "原请求还没有完成" in result
            event_calls = brain.event_bus.publish.await_args_list
            event_types = [call.args[0] for call in event_calls]
            assert event_types == ["task.started", "task.planning", "task.failed"]

            failed_payload = event_calls[2].args[1]
            assert failed_payload["chat_id"] == "chat1"
            assert failed_payload["phase"] == "failed"
            assert "text" in failed_payload

    async def test_think_enables_activate_skill_tool_when_skills_available(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.router.complete = AsyncMock(return_value="ok")
            brain.task_runtime.chat_tools = MagicMock(return_value=[])
            brain.skill_manager = MagicMock()
            brain.skill_manager.has_model_visible_skills.return_value = True
            brain.skill_manager.render_catalog_for_prompt.return_value = "<available_skills/>"

            await brain.think("chat1", "你好")

            brain.task_runtime.chat_tools.assert_called_once_with(
                shell_enabled=True,
                web_enabled=True,
                skill_activation_enabled=True,
            )

    async def test_run_skill_command_blocks_non_user_invocable_skill(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            skill = SimpleNamespace(
                name="private-skill",
                user_invocable=False,
                command_dispatch=None,
            )
            brain.skill_manager = MagicMock()
            brain.skill_manager.enabled = True
            brain.skill_manager.get.return_value = skill

            reply = await brain.run_skill_command(
                chat_id="chat1",
                raw_user_message="/skill private-skill",
                skill_name="private-skill",
                user_input="",
            )

            assert "不允许用户直接调用" in reply

    async def test_run_skill_command_sanitizes_dialogue_reply(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            skill = SimpleNamespace(
                name="demo",
                user_invocable=True,
                command_dispatch=None,
            )
            brain.skill_manager = MagicMock()
            brain.skill_manager.enabled = True
            brain.skill_manager.get.return_value = skill
            brain._run_skill_dialogue = AsyncMock(return_value="<think>隐藏</think>展示")

            reply = await brain.run_skill_command(
                chat_id="chat1",
                raw_user_message="/skill demo",
                skill_name="demo",
                user_input="",
            )

            assert reply == "展示"
            brain.memory.append.assert_any_call("chat1", "assistant", "展示")

    async def test_run_skill_command_keeps_raw_direct_dispatch_output(self):
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            skill = SimpleNamespace(
                name="demo",
                user_invocable=True,
                command_dispatch="tool",
            )
            brain.skill_manager = MagicMock()
            brain.skill_manager.enabled = True
            brain.skill_manager.get.return_value = skill
            brain._run_skill_direct_dispatch = AsyncMock(return_value="<think>raw</think>工具输出")

            reply = await brain.run_skill_command(
                chat_id="chat1",
                raw_user_message="/skill demo",
                skill_name="demo",
                user_input="pwd",
            )

            assert reply == "<think>raw</think>工具输出"
            brain.memory.append.assert_any_call("chat1", "assistant", "<think>raw</think>工具输出")
