"""WeatherAgent - 查询指定城市天气。"""

import json
import logging
import re
from urllib.parse import quote

import httpx

from config.settings import SEARCH_PROXY_URL
from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.agents.weather")

_TIMEOUT = 10
_CITY_STOPWORDS = {
    "看看",
    "看下",
    "看一下",
    "查查",
    "查下",
    "查一下",
    "问下",
    "问一下",
    "告诉我",
    "天气",
    "气温",
    "温度",
    "风速",
}
_CITY_PATTERNS = [
    re.compile(
        r"(?:帮我|帮忙|麻烦|请|想知道|告诉我|查下|查一下|看下|看一下|问下)?"
        r"(?P<city>[\u4e00-\u9fffA-Za-z·\-\s]{2,20}?)"
        r"(?:今天|明天|后天|现在|当前)?(?:的)?(?:天气|气温|温度|风速)",
    ),
    re.compile(
        r"(?:帮我|帮忙|麻烦|请|想知道|告诉我|查下|查一下|看下|看一下|问下)?"
        r"(?P<city>[\u4e00-\u9fffA-Za-z·\-\s]{2,20}?)"
        r"(?:现在|今天|明天|后天)?(?:多少度|几度|冷不冷|热不热)",
    ),
]


class WeatherAgent(BaseAgent):
    name = "weather"
    description = "查询城市当前天气、气温和风速"
    capabilities = ["查询天气", "查询温度", "查询风速"]

    async def execute(self, task: AgentTask, router) -> AgentResult:
        city = await self._extract_city(task.user_message, router)
        if city is None:
            return AgentResult(
                content="你想查哪个城市的天气？",
                needs_persona_formatting=True,
            )

        weather = await self._fetch_weather(city)
        if weather is None:
            return AgentResult(
                content=f"我暂时没查到 {city} 的天气，可能是网络不太稳定，稍后再试试。",
                needs_persona_formatting=True,
            )

        lines = [
            f"{city} 当前天气：",
            f"- 温度：{weather['temperature']}°C",
            f"- 天气：{weather['description']}",
            f"- 风速：{weather['wind_speed']} km/h",
        ]
        return AgentResult(
            content="\n".join(lines),
            needs_persona_formatting=True,
            metadata={"city": city},
        )

    async def _extract_city(self, user_message: str, router) -> str | None:
        for pattern in _CITY_PATTERNS:
            match = pattern.search(user_message)
            if match:
                city = self._normalize_city(match.group("city"))
                if city:
                    return city

        prompt = load_prompt("agent_weather").replace("{user_message}", user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=128,
            )
        except Exception as exc:
            logger.warning(f"[weather] 城市提取失败: {exc}")
            return None

        return self._parse_city(raw)

    def _parse_city(self, raw: str) -> str | None:
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        city = data.get("city") if isinstance(data, dict) else None
        if city is None:
            return None
        return self._normalize_city(str(city))

    def _normalize_city(self, city: str) -> str | None:
        normalized = re.sub(r"\s+", "", city).strip("，。！？?!.")
        if not normalized:
            return None
        if normalized in _CITY_STOPWORDS:
            return None
        return normalized

    async def _fetch_weather(self, city: str) -> dict | None:
        url = f"https://wttr.in/{quote(city)}?format=j1&lang=zh"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, proxy=SEARCH_PROXY_URL or None) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:
            logger.warning(f"[weather] 请求失败 city={city!r}: {exc}")
            return None

        try:
            payload = response.json()
            current = payload["current_condition"][0]
        except Exception as exc:
            logger.warning(f"[weather] 解析天气响应失败 city={city!r}: {exc}")
            return None

        description = self._extract_description(current)
        temperature = current.get("temp_C") or current.get("FeelsLikeC") or "?"
        wind_speed = current.get("windspeedKmph") or "?"
        return {
            "description": description or "未知",
            "temperature": temperature,
            "wind_speed": wind_speed,
        }

    def _extract_description(self, current: dict) -> str:
        for key in ("lang_zh", "weatherDesc"):
            values = current.get(key) or []
            if values and isinstance(values[0], dict):
                value = values[0].get("value")
                if value:
                    return str(value)
        return ""
