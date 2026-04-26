from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tools.sports_tool import (
    MLB_STATS_BASE,
    _classify_confidence,
    _normalize_team_name,
    get_sports_score,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, params=None):
        self.calls.append((url, params or {}))
        return _FakeResponse(self._responses.pop(0))


@pytest.mark.asyncio
async def test_normalize_team_name_alias_without_llm():
    assert await _normalize_team_name("道奇") == "Los Angeles Dodgers"


@pytest.mark.asyncio
async def test_normalize_team_name_uses_llm_for_unknown():
    router = SimpleNamespace(complete=AsyncMock(return_value="Los Angeles Dodgers"))
    result = await _normalize_team_name("LA棒球队", router)
    assert result == "Los Angeles Dodgers"


@pytest.mark.asyncio
async def test_get_sports_score_success(monkeypatch):
    recent = "2026-04-25T23:15:00Z"
    fake_client = _FakeAsyncClient([
        {"dates": [{"games": [{
            "gamePk": 823960,
            "gameDate": recent,
            "officialDate": "2026-04-25",
            "status": {"abstractGameState": "Final", "detailedState": "Final"},
            "teams": {
                "home": {"team": {"name": "Los Angeles Dodgers"}, "score": 5},
                "away": {"team": {"name": "New York Mets"}, "score": 3},
            },
            "venue": {"name": "Dodger Stadium"},
        }]}]},
    ])
    monkeypatch.setattr(
        "src.tools.sports_tool.httpx.AsyncClient",
        lambda **_kwargs: fake_client,
    )

    result = await get_sports_score("Dodgers", llm_router=None)

    assert result["team_canonical"] == "Los Angeles Dodgers"
    assert result["last_match"]["home_score"] == "5"
    assert result["last_match"]["start_time_utc"] == "2026-04-25T23:15:00+00:00"
    assert result["last_match"]["start_time_local"] == "2026-04-26T07:15:00+08:00"
    assert result["last_match"]["local_date"] == "2026-04-26"
    assert result["last_match"]["local_time"] == "07:15"
    assert result["last_match"]["timezone"] == "Asia/Shanghai"
    assert result["source"] == "mlb_stats_api"
    assert fake_client.calls[0][0] == f"{MLB_STATS_BASE}/schedule"


@pytest.mark.asyncio
async def test_get_sports_score_team_not_found(monkeypatch):
    fake_client = _FakeAsyncClient([{"teams": None}])
    monkeypatch.setattr(
        "src.tools.sports_tool.httpx.AsyncClient",
        lambda **_kwargs: fake_client,
    )

    result = await get_sports_score("不存在的队", llm_router=None)

    assert "error" in result


def test_classify_confidence_stale():
    result = {"last_match": {"date_utc": "2020-01-01T00:00:00+00:00"}}
    assert _classify_confidence(result) == "stale"
