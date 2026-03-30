"""TelegramApp 适配层测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.telegram_app import TelegramApp


@pytest.fixture
def app_with_container():
    brain = MagicMock()
    brain.think = AsyncMock(return_value="ok")
    brain.run_skill_command = AsyncMock(return_value="skill ok")
    brain.memory = MagicMock()
    channel_manager = MagicMock()
    channel_manager.register = MagicMock()
    channel_manager.send_to_kevin = AsyncMock()
    container = SimpleNamespace(
        brain=brain,
        start=AsyncMock(),
        shutdown=AsyncMock(),
        channel_manager=channel_manager,
    )
    return TelegramApp(container=container), container


def make_message(chat_id: int = 42, text: str = "hello"):
    message = MagicMock()
    message.chat_id = chat_id
    message.text = text
    message.reply_text = AsyncMock()
    message.chat = SimpleNamespace(
        send_action=AsyncMock(),
        send_message=AsyncMock(),
    )
    message.voice = None
    message.audio = None
    return message


@pytest.mark.asyncio
async def test_post_init_and_shutdown_bridge_container_lifecycle(app_with_container):
    app, container = app_with_container
    application = SimpleNamespace(bot=MagicMock(), bot_data={})

    await app._post_init(application)
    await app._post_shutdown(application)

    container.start.assert_awaited_once()
    call_kwargs = container.start.await_args.kwargs
    assert "send_fn" in call_kwargs
    assert callable(call_kwargs["send_fn"])
    container.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_uses_buffer_and_flushes(app_with_container):
    app, _ = app_with_container
    update = SimpleNamespace(message=make_message(text="first"))
    context = MagicMock()

    with patch("src.app.telegram_app.MESSAGE_BUFFER_SECONDS", 0), \
         patch("src.app.telegram_app.asyncio.sleep", new=AsyncMock()), \
         patch.object(app, "_think_and_reply", new=AsyncMock()) as mock_think:
        await app.handle_message(update, context)
        task = app._buffer_tasks["42"]
        await task

    mock_think.assert_awaited_once()
    assert mock_think.await_args.args[1] == "42"
    assert mock_think.await_args.args[2] == "first"


@pytest.mark.asyncio
async def test_handle_voice_transcribes_and_enqueues(app_with_container):
    app, _ = app_with_container
    message = make_message()
    message.voice = SimpleNamespace(file_id="voice_1", mime_type="audio/ogg")
    update = SimpleNamespace(message=message)
    downloaded = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"abc")))
    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=downloaded))
    )

    with patch("src.tools.transcriber.transcribe", new=AsyncMock(return_value="你好")):
        with patch.object(app, "_enqueue_message") as mock_enqueue:
            await app.handle_voice(update, context)

    message.reply_text.assert_awaited_once_with("🎤 你好")
    mock_enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_skill_requires_name_arg(app_with_container):
    app, _ = app_with_container
    message = make_message()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=[])

    await app.cmd_skill(update, context)

    message.reply_text.assert_awaited_once_with("用法：/skill <name> [args]")


@pytest.mark.asyncio
async def test_cmd_skill_calls_brain_run_skill_command(app_with_container):
    app, container = app_with_container
    message = make_message(text="/skill demo do something")
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=["demo", "do", "something"])

    await app.cmd_skill(update, context)

    container.brain.run_skill_command.assert_awaited_once()
    kwargs = container.brain.run_skill_command.await_args.kwargs
    assert kwargs["chat_id"] == "42"
    assert kwargs["skill_name"] == "demo"
    assert kwargs["user_input"] == "do something"


@pytest.mark.asyncio
async def test_handle_message_skill_shortcut_bypasses_buffer(app_with_container):
    app, _ = app_with_container
    update = SimpleNamespace(message=make_message(text="/demo: ls -la"))
    context = MagicMock()

    with patch.object(app, "_run_skill_command", new=AsyncMock()) as mock_run_skill, \
         patch.object(app, "_enqueue_message") as mock_enqueue:
        await app.handle_message(update, context)

    mock_run_skill.assert_awaited_once()
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_model_list_shows_options_and_effective_models(app_with_container):
    app, container = app_with_container
    message = make_message(text="/model")
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=[])

    container.brain.list_model_options = MagicMock(
        return_value=[
            {"index": 1, "alias": "codex", "ref": "openai-codex/gpt-5.4"},
            {"index": 2, "alias": "minimax", "ref": "MiniMax-M2.7"},
        ]
    )
    container.brain.model_status = MagicMock(
        return_value={
            "purposes": {
                "chat": {"effective": "openai-codex/gpt-5.4", "override": "openai-codex/gpt-5.4"},
                "tool": {"effective": "MiniMax-M2.7", "override": None},
                "heartbeat": {"effective": "MiniMax-M2.7", "override": None},
            }
        }
    )

    await app.cmd_model(update, context)

    container.brain.list_model_options.assert_called_once_with()
    container.brain.model_status.assert_called_once_with("42")
    text = message.reply_text.await_args.args[0]
    assert "可用模型（会话级）" in text
    assert "1. codex -> openai-codex/gpt-5.4" in text
    assert "chat: openai-codex/gpt-5.4 (override)" in text


@pytest.mark.asyncio
async def test_cmd_model_status_routes_to_brain_status(app_with_container):
    app, container = app_with_container
    message = make_message(text="/model status")
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=["status"])
    container.brain.model_status = MagicMock(
        return_value={
            "purposes": {
                "chat": {"default": "a", "effective": "b", "override": "b"},
                "tool": {"default": "c", "effective": "c", "override": None},
                "heartbeat": {"default": "d", "effective": "d", "override": None},
            }
        }
    )

    await app.cmd_model(update, context)

    container.brain.model_status.assert_called_once_with("42")
    text = message.reply_text.await_args.args[0]
    assert "当前模型状态（会话级）" in text
    assert "default: a" in text
    assert "effective: b" in text


@pytest.mark.asyncio
async def test_cmd_model_switch_and_default(app_with_container):
    app, container = app_with_container
    message = make_message(text="/model 2")
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=["2"])
    container.brain.switch_model = MagicMock(
        return_value={
            "selected": {"ref": "MiniMax-M2.7"},
            "applied": {"chat": "MiniMax-M2.7", "tool": "MiniMax-M2.7"},
            "skipped": {"heartbeat": "缺少 openai oauth profile"},
        }
    )

    await app.cmd_model(update, context)

    container.brain.switch_model.assert_called_once_with("42", "2")
    switch_text = message.reply_text.await_args.args[0]
    assert "已选择模型：MiniMax-M2.7" in switch_text
    assert "未切换" in switch_text

    message_default = make_message(text="/model default")
    update_default = SimpleNamespace(message=message_default)
    context_default = SimpleNamespace(args=["default"])
    container.brain.reset_model = MagicMock(return_value={"cleared": 2})

    await app.cmd_model(update_default, context_default)

    container.brain.reset_model.assert_called_once_with("42")
    default_text = message_default.reply_text.await_args.args[0]
    assert "已恢复默认模型" in default_text


@pytest.mark.asyncio
async def test_cmd_model_handles_value_error(app_with_container):
    app, container = app_with_container
    message = make_message(text="/model unknown")
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=["unknown"])
    container.brain.switch_model = MagicMock(side_effect=ValueError("模型不在 allowlist"))

    await app.cmd_model(update, context)

    container.brain.switch_model.assert_called_once_with("42", "unknown")
    text = message.reply_text.await_args.args[0]
    assert "模型切换失败" in text


@pytest.mark.asyncio
async def test_send_reply_strips_thinking_tags(app_with_container):
    app, _ = app_with_container
    message = make_message()

    await app._send_reply(message, "<think>内部</think>可见")

    message.reply_text.assert_awaited_once()
    assert message.reply_text.await_args.args[0] == "可见"
    assert message.reply_text.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_status_sender_ignores_stale_task_token(app_with_container):
    app, _ = app_with_container
    app._bot = SimpleNamespace(send_message=AsyncMock())
    app._active_status_tokens["42"] = "active-token"

    sender = app._build_status_sender(task_token="stale-token")
    await sender("42", "stage:planning")

    app._bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_status_sender_formats_report_stage(app_with_container):
    app, _ = app_with_container
    app._bot = SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock())
    app._active_status_tokens["42"] = "token-1"

    sender = app._build_status_sender(task_token="token-1")
    with patch("config.settings.TELEGRAM_PROGRESS_STYLE", "report"):
        await sender("42", "stage:executing:web_search:1:2")

    app._bot.send_message.assert_awaited_once()
    sent = app._bot.send_message.await_args.kwargs
    assert sent["chat_id"] == 42
    assert sent["parse_mode"] == "HTML"
    assert "执行中：web_search（1/2）" in sent["text"]


@pytest.mark.asyncio
async def test_status_sender_silent_only_sends_typing(app_with_container):
    app, _ = app_with_container
    app._bot = SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock())
    app._active_status_tokens["42"] = "token-1"

    sender = app._build_status_sender(task_token="token-1")
    with patch("config.settings.TELEGRAM_PROGRESS_STYLE", "silent"):
        await sender("42", "stage:executing:web_search:1:2")

    app._bot.send_chat_action.assert_awaited_once()
    app._bot.send_message.assert_not_called()
