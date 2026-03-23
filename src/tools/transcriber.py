"""语音转写工具 — 使用 Whisper API 将音频转为文字。"""

import logging
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger("lapwing.tools.transcriber")


def _make_client() -> AsyncOpenAI:
    """按配置创建 Whisper 客户端，延迟导入避免循环依赖。"""
    from config.settings import (
        LLM_API_KEY, LLM_BASE_URL,
        WHISPER_API_KEY, WHISPER_BASE_URL,
    )
    api_key = WHISPER_API_KEY or LLM_API_KEY
    base_url = WHISPER_BASE_URL or LLM_BASE_URL or None
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """将音频字节转写为文字。

    Args:
        audio_bytes: 音频文件的原始字节（Telegram 语音为 .ogg OPUS 格式）
        filename: 文件名，包含扩展名，供 API 推断格式

    Returns:
        转写后的文字，失败时返回 None
    """
    from config.settings import WHISPER_MODEL

    suffix = Path(filename).suffix or ".ogg"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        client = _make_client()
        try:
            with tmp_path.open("rb") as f:
                response = await client.audio.transcriptions.create(
                    model=WHISPER_MODEL,
                    file=(filename, f),
                    response_format="text",
                )
            text = str(response).strip()
            logger.info(f"[transcriber] 转写成功，{len(text)} 字符")
            return text or None
        finally:
            tmp_path.unlink(missing_ok=True)
            await client.close()

    except Exception as e:
        logger.warning(f"[transcriber] 转写失败: {e}")
        return None
