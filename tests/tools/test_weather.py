"""get_weather tool tests."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.weather import fetch_weather


@pytest.mark.asyncio
async def test_fetch_weather_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current_condition": [{
            "temp_C": "25",
            "windspeedKmph": "10",
            "humidity": "60",
            "lang_zh": [{"value": "晴"}],
        }]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("src.tools.weather.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_weather("东京")

    assert result["temperature"] == "25"
    assert result["description"] == "晴"
    assert "error" not in result


@pytest.mark.asyncio
async def test_fetch_weather_empty_location():
    result = await fetch_weather("")
    assert "error" in result
