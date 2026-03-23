"""transcriber 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("WHISPER_API_KEY", "")
    monkeypatch.setenv("WHISPER_BASE_URL", "")
    monkeypatch.setenv("WHISPER_MODEL", "whisper-1")


@pytest.mark.asyncio
async def test_transcribe_success():
    """正常转写返回文字。"""
    mock_response = "你好，这是一段语音"

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()

    with patch("src.tools.transcriber._make_client", return_value=mock_client):
        from src.tools.transcriber import transcribe
        result = await transcribe(b"fake-audio-bytes", filename="voice.ogg")

    assert result == "你好，这是一段语音"
    mock_client.audio.transcriptions.create.assert_awaited_once()
    call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["response_format"] == "text"


@pytest.mark.asyncio
async def test_transcribe_empty_response_returns_none():
    """API 返回空字符串时，返回 None。"""
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value="   ")
    mock_client.close = AsyncMock()

    with patch("src.tools.transcriber._make_client", return_value=mock_client):
        from src.tools.transcriber import transcribe
        result = await transcribe(b"fake-audio-bytes")

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_api_error_returns_none():
    """API 调用失败时返回 None 而不是崩溃。"""
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(side_effect=Exception("API error"))
    mock_client.close = AsyncMock()

    with patch("src.tools.transcriber._make_client", return_value=mock_client):
        from src.tools.transcriber import transcribe
        result = await transcribe(b"fake-audio-bytes")

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_uses_whisper_api_key_when_set():
    """优先使用 WHISPER_API_KEY 而不是 LLM_API_KEY。"""
    captured = {}

    def fake_client(api_key, base_url=None):
        captured["api_key"] = api_key
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value="text")
        client.close = AsyncMock()
        return client

    with patch("config.settings.WHISPER_API_KEY", "whisper-specific-key"), \
         patch("src.tools.transcriber.AsyncOpenAI", side_effect=fake_client):
        from src.tools.transcriber import transcribe
        result = await transcribe(b"bytes")

    assert captured["api_key"] == "whisper-specific-key"
    assert result == "text"


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_llm_key_when_whisper_not_set():
    """WHISPER_API_KEY 未配置时，回退到 LLM_API_KEY。"""
    captured = {}

    def fake_client(api_key, base_url=None):
        captured["api_key"] = api_key
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value="text")
        client.close = AsyncMock()
        return client

    with patch("config.settings.WHISPER_API_KEY", ""), \
         patch("config.settings.LLM_API_KEY", "test-key"), \
         patch("src.tools.transcriber.AsyncOpenAI", side_effect=fake_client):
        from src.tools.transcriber import transcribe
        await transcribe(b"bytes")

    assert captured["api_key"] == "test-key"
