"""MiniMax Coding Plan VLM 客户端 — 图片理解。

通过 /v1/coding_plan/vlm 端点实现图片分析，可用于浏览器视觉理解等场景。
"""

import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("lapwing.core.minimax_vlm")


class MiniMaxVLM:
    """调用 MiniMax Coding Plan VLM 端点进行图片理解。"""

    def __init__(self, api_key: str, api_host: str = "https://api.minimaxi.com"):
        self.api_key = api_key
        self.api_host = api_host.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    async def understand_image(self, prompt: str, image_source: str) -> str:
        """分析图片内容。

        Args:
            prompt: 分析指令（如"描述页面内容"、"提取文字"）
            image_source: 图片 URL 或 base64 data URL 或本地文件路径

        Returns:
            VLM 的分析结果文本
        """
        # 本地文件路径转 base64 data URL
        if image_source.startswith("/") or image_source.startswith("./"):
            image_source = self._file_to_data_url(image_source)

        url = f"{self.api_host}/v1/coding_plan/vlm"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": prompt,
            "image_source": image_source,
        }

        return await self._do_request(url, headers, payload)

    async def _do_request(self, url: str, headers: dict, payload: dict) -> str:
        from src.utils.retry import async_retry

        @async_retry(max_attempts=2, base_delay=2.0)
        async def _request():
            resp = await self._client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", data.get("text", str(data)))

        try:
            return await _request()
        except httpx.HTTPStatusError as e:
            logger.error("VLM API 错误: %d — %s", e.response.status_code, e.response.text[:200])
            raise
        except Exception as e:
            logger.error("VLM 调用失败: %s", e)
            raise

    @staticmethod
    def _file_to_data_url(path: str) -> str:
        """本地文件转 base64 data URL。"""
        p = Path(path)
        suffix = p.suffix.lower()
        media_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_types.get(suffix, "image/png")
        data = base64.b64encode(p.read_bytes()).decode()
        return f"data:{media_type};base64,{data}"

    async def close(self) -> None:
        await self._client.aclose()
