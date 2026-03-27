"""TelegramApp 适配层测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.telegram_app import TelegramApp


@pytest.fixture
def app_with_container():
    brain = MagicMock()
    brain.think = AsyncMock(return_value="ok")
    brain.memory = MagicMock()
    container = SimpleNamespace(
        brain=brain,
        start=AsyncMock(),
        shutdown=AsyncMock(),
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

    container.start.assert_awaited_once_with(bot=application.bot)
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
