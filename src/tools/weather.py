"""get_weather tool — query weather via wttr.in."""

import logging
from urllib.parse import quote

import httpx

from config.settings import SEARCH_PROXY_URL

logger = logging.getLogger("lapwing.tools.weather")

_TIMEOUT = 10


async def fetch_weather(location: str) -> dict:
    """Fetch current weather for a location from wttr.in.

    Args:
        location: City/location name (e.g. "东京", "Los Angeles")

    Returns:
        dict with keys: location, temperature, description, wind_speed, humidity
        On failure: dict with key "error"
    """
    if not location or not location.strip():
        return {"error": "未指定地点。"}

    location = location.strip()
    url = f"https://wttr.in/{quote(location)}?format=j1&lang=zh"
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            proxy=SEARCH_PROXY_URL or None,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("[weather] request failed location=%r: %s", location, exc)
        return {"error": f"查询 {location} 天气失败: {exc}"}

    try:
        payload = response.json()
        current = payload["current_condition"][0]
    except Exception as exc:
        logger.warning("[weather] parse failed location=%r: %s", location, exc)
        return {"error": f"解析 {location} 天气数据失败。"}

    # Extract Chinese description if available
    description = ""
    for key in ("lang_zh", "weatherDesc"):
        values = current.get(key) or []
        if values and isinstance(values[0], dict):
            value = values[0].get("value")
            if value:
                description = str(value)
                break

    return {
        "location": location,
        "temperature": current.get("temp_C", "?"),
        "description": description or "未知",
        "wind_speed": current.get("windspeedKmph", "?"),
        "humidity": current.get("humidity", "?"),
    }
