"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import logging
from openai import AsyncOpenAI
from config.settings import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL,
    LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL,
    NIM_API_KEY, NIM_BASE_URL, NIM_MODEL,
)

logger = logging.getLogger("lapwing.llm_router")

# purpose -> (api_key, base_url, model) 的映射配置
_PURPOSE_ENV: dict[str, tuple[str, str, str]] = {
    "chat": (LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL),
    "tool": (LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL),
    "heartbeat": (NIM_API_KEY, NIM_BASE_URL, NIM_MODEL),
}


class LLMRouter:
    """按 purpose 路由到对应 LLM client。

    用法：
        router = LLMRouter()
        reply = await router.complete(messages, purpose="chat")
    """

    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI] = {}
        self._models: dict[str, str] = {}
        self._setup_clients()

    def _setup_clients(self) -> None:
        """根据配置初始化各 purpose 的 client，未配置时回退到通用 LLM_*。"""
        # 校验通用配置（所有 purpose 的最终回退）
        if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
            raise ValueError(
                "LLM 通用配置不完整，请检查 config/.env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL"
            )

        for purpose, (api_key, base_url, model) in _PURPOSE_ENV.items():
            if api_key and base_url and model:
                self._clients[purpose] = AsyncOpenAI(api_key=api_key, base_url=base_url)
                self._models[purpose] = model
                logger.info(f"[{purpose}] 使用专用模型: {model} ({base_url})")
            else:
                # 回退到通用配置
                if purpose not in self._clients:
                    self._clients[purpose] = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
                self._models[purpose] = LLM_MODEL
                logger.info(f"[{purpose}] 回退到通用模型: {LLM_MODEL} ({LLM_BASE_URL})")

    def model_for(self, purpose: str) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._models.get(purpose, LLM_MODEL)

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
    ) -> str:
        """向对应 purpose 的模型发送请求，返回回复文本。

        Args:
            messages: OpenAI 格式的消息列表
            purpose: 用途标识（"chat" 或 "tool"），未知值回退到 chat 模型
            max_tokens: 最大生成 token 数

        Returns:
            模型回复的文本内容

        Raises:
            Exception: LLM API 调用失败时向上抛出，由调用方处理
        """
        client = self._clients.get(purpose)
        if client is None:
            logger.warning(f"[{purpose}] 未知的 purpose，回退到 chat 模型")
            client = self._clients["chat"]
            model = self._models.get("chat", LLM_MODEL)
        else:
            model = self._models.get(purpose, LLM_MODEL)

        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content or ""
