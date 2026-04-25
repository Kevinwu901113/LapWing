"""Sports score/schedule lookup tool."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.time_utils import now, parse_iso_datetime
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.sports")

SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/123"
TIMEOUT = 10.0

_TEAM_ALIASES = {
    "道奇": "Los Angeles Dodgers",
    "洛杉矶道奇": "Los Angeles Dodgers",
    "dodgers": "Los Angeles Dodgers",
    "湖人": "Los Angeles Lakers",
    "洛杉矶湖人": "Los Angeles Lakers",
    "lakers": "Los Angeles Lakers",
    "曼联": "Manchester United",
    "man utd": "Manchester United",
    "manchester united": "Manchester United",
}


async def get_sports_score(
    team: str,
    league: str | None = None,
    llm_router: Any = None,
) -> dict[str, Any]:
    """Return recent and upcoming matches for a team."""
    if not team or not team.strip():
        return {"error": "未指定队伍。"}

    canonical = await _normalize_team_name(team, llm_router)
    if not canonical:
        return {"error": f"无法识别队伍 '{team}'。"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            team_resp = await client.get(
                f"{SPORTSDB_BASE}/searchteams.php",
                params={"t": canonical},
            )
            team_resp.raise_for_status()
            teams = team_resp.json().get("teams") or []
            if league:
                league_lower = league.lower()
                teams = [
                    item for item in teams
                    if league_lower in str(item.get("strLeague", "")).lower()
                    or league_lower in str(item.get("strSport", "")).lower()
                ] or teams
            if not teams:
                return {"error": f"未找到队伍 '{canonical}'。"}

            team_id = teams[0].get("idTeam")
            if not team_id:
                return {"error": f"队伍 '{canonical}' 缺少 idTeam。"}

            last_resp = await client.get(
                f"{SPORTSDB_BASE}/eventslast.php",
                params={"id": team_id},
            )
            next_resp = await client.get(
                f"{SPORTSDB_BASE}/eventsnext.php",
                params={"id": team_id},
            )
            last_resp.raise_for_status()
            next_resp.raise_for_status()

            last_events = (last_resp.json().get("results") or [])[:1]
            next_events = (next_resp.json().get("events") or [])[:1]
    except Exception as exc:
        logger.warning("[sports] fetch failed team=%s: %s", canonical, exc)
        return {"error": f"查询失败：{exc}"}

    result = {
        "team_canonical": canonical,
        "last_match": _format_match(last_events[0]) if last_events else None,
        "live_match": None,
        "next_match": _format_match(next_events[0]) if next_events else None,
        "source": "thesportsdb",
    }
    result["confidence"] = _classify_confidence(result)
    return result


async def _normalize_team_name(team: str, llm_router: Any = None) -> str:
    text = team.strip()
    mapped = _TEAM_ALIASES.get(text.lower()) or _TEAM_ALIASES.get(text)
    if mapped:
        return mapped
    if llm_router is None:
        return text

    prompt = f"""把以下队伍名转为 TheSportsDB 使用的英文标准名。

输入：{text}

规则：
- "道奇" / "洛杉矶道奇" / "Dodgers" -> "Los Angeles Dodgers"
- "湖人" -> "Los Angeles Lakers"
- "曼联" -> "Manchester United"
- 不确定的，原样返回

只输出标准名，不要解释。"""

    try:
        result = await llm_router.complete(
            [{"role": "user", "content": prompt}],
            purpose="lightweight_judgment",
            max_tokens=50,
        )
    except Exception:
        return text
    return str(result).strip().strip('"').strip("'") or text


def _format_match(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "home": event.get("strHomeTeam"),
        "away": event.get("strAwayTeam"),
        "home_score": event.get("intHomeScore"),
        "away_score": event.get("intAwayScore"),
        "date_utc": event.get("strTimestamp") or event.get("dateEvent"),
        "league": event.get("strLeague"),
        "status": event.get("strStatus"),
    }


def _classify_confidence(result: dict[str, Any]) -> str:
    if result.get("live_match"):
        return "live"
    last = result.get("last_match") or {}
    parsed = parse_iso_datetime(last.get("date_utc"))
    if parsed is None:
        return "scheduled" if result.get("next_match") else "stale"
    hours_since = (now() - parsed.astimezone(now().tzinfo)).total_seconds() / 3600
    if hours_since < 24:
        return "recent"
    return "scheduled" if result.get("next_match") else "stale"


async def get_sports_score_executor(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    team = str(req.arguments.get("team", "")).strip()
    league = str(req.arguments.get("league", "")).strip() or None
    result = await get_sports_score(
        team,
        league=league,
        llm_router=ctx.services.get("llm_router"),
    )
    if "error" in result:
        return ToolExecutionResult(success=False, payload=result, reason=result["error"])
    return ToolExecutionResult(success=True, payload=result, reason=f"confidence={result['confidence']}")


SPORTS_TOOL_SPEC = ToolSpec(
    name="get_sports_score",
    description=(
        "查询体育赛事的最近比分、当前赛程、下一场。"
        "问到比分、赛程、球队胜负时优先用此工具；返回 stale 时再用 research 兜底。"
        "支持中英文队名。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "team": {"type": "string", "description": "队伍名，中英文都可"},
            "league": {"type": "string", "description": "联赛或运动名，可选，如 MLB/NBA/EPL"},
        },
        "required": ["team"],
    },
    executor=get_sports_score_executor,
    capability="web",
    capabilities=("sports",),
    risk_level="low",
    max_result_tokens=800,
)
