"""DeepEval judge model configuration.

Uses MiniMax M2.7 via LLMRouter's Anthropic-compatible path as the
evaluation judge, avoiding external API dependencies.
"""
from __future__ import annotations

import asyncio
from typing import Any

from deepeval.models import DeepEvalBaseLLM


class LapwingJudgeLLM(DeepEvalBaseLLM):
    """MiniMax M2.7 judge for DeepEval metrics.

    Wraps LLMRouter.query_lightweight for structured evaluation prompts.
    Router is lazily initialized on first use to avoid import-time side
    effects.
    """

    def __init__(self):
        self._router = None

    def _get_router(self):
        if self._router is None:
            from src.core.llm_router import LLMRouter
            self._router = LLMRouter()
        return self._router

    def load_model(self) -> Any:
        return self._get_router()

    def generate(self, prompt: str, schema: Any = None) -> str:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
        return loop.run_until_complete(self.a_generate(prompt, schema))

    async def a_generate(self, prompt: str, schema: Any = None) -> str:
        router = self._get_router()
        return await router.complete(
            messages=[
                {"role": "system", "content": "You are an impartial evaluator. Respond in valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            purpose="tool",
            max_tokens=2048,
            origin="eval_judge",
        )

    def get_model_name(self) -> str:
        return "MiniMax-M2.7"


JUDGE = LapwingJudgeLLM()
