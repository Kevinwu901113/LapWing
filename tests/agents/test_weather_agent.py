"""WeatherAgent 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import AgentTask
from src.agents.weather_agent import WeatherAgent


def make_task(user_message: str = "北京今天天气怎么样") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def _mock_async_client(response=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(return_value=response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, client


def _make_response(payload: dict):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


@pytest.mark.asyncio
class TestWeatherAgent:
    async def test_execute_formats_weather_with_regex_city(self):
        router = MagicMock()
        router.complete = AsyncMock()
        response = _make_response(
            {
                "current_condition": [
                    {
                        "temp_C": "18",
                        "windspeedKmph": "12",
                        "weatherDesc": [{"value": "晴"}],
                    }
                ]
            }
        )
        cm, client = _mock_async_client(response=response)

        with patch("src.agents.weather_agent.httpx.AsyncClient", return_value=cm):
            result = await WeatherAgent().execute(make_task(), router)

        assert result.needs_persona_formatting is True
        assert result.metadata["city"] == "北京"
        assert "北京 当前天气" in result.content
        assert "18°C" in result.content
        client.get.assert_awaited_once()
        router.complete.assert_not_called()

    async def test_extract_city_falls_back_to_llm(self):
        router = MagicMock()
        router.complete = AsyncMock(return_value='{"city":"上海"}')

        with patch("src.agents.weather_agent.load_prompt", return_value="{user_message}"):
            city = await WeatherAgent()._extract_city("帮我看看申城那边", router)

        assert city == "上海"

    async def test_execute_returns_hint_when_city_missing(self):
        router = MagicMock()
        router.complete = AsyncMock(return_value='{"city": null}')

        with patch("src.agents.weather_agent.load_prompt", return_value="{user_message}"):
            result = await WeatherAgent().execute(make_task("帮我看看天气"), router)

        assert "哪个城市" in result.content

    async def test_execute_returns_friendly_message_on_api_failure(self):
        router = MagicMock()
        router.complete = AsyncMock(return_value='{"city":"北京"}')

        with patch("src.agents.weather_agent.load_prompt", return_value="{user_message}"), \
             patch.object(WeatherAgent, "_fetch_weather", AsyncMock(return_value=None)):
            result = await WeatherAgent().execute(make_task("帮我看看天气"), router)

        assert "没查到" in result.content

    async def test_prefers_lang_zh_description(self):
        agent = WeatherAgent()
        description = agent._extract_description(
            {
                "lang_zh": [{"value": "多云"}],
                "weatherDesc": [{"value": "Cloudy"}],
            }
        )
        assert description == "多云"
