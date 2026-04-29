"""Sports score/schedule lookup tool."""

from __future__ import annotations

import logging
from datetime import timedelta, timezone
from typing import Any

import httpx

from src.core.time_utils import local_timezone_name, local_tz, now, parse_iso_datetime
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.sports_tool")

SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/123"
MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
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

_MLB_TEAM_IDS = {
    "Los Angeles Dodgers": 119,
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
            mlb_result = await _fetch_mlb_schedule(client, canonical, league=league)
            if mlb_result is not None:
                return mlb_result

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
    start_utc = _event_start_utc(event)
    start_local = start_utc.astimezone(local_tz()) if start_utc is not None else None
    start_utc_iso = start_utc.isoformat() if start_utc is not None else None
    start_local_iso = start_local.isoformat() if start_local is not None else None
    return {
        "home": event.get("strHomeTeam"),
        "away": event.get("strAwayTeam"),
        "home_score": event.get("intHomeScore"),
        "away_score": event.get("intAwayScore"),
        "start_time_utc": start_utc_iso,
        "start_time_local": start_local_iso,
        "local_date": start_local.date().isoformat() if start_local is not None else None,
        "local_time": start_local.strftime("%H:%M") if start_local is not None else None,
        "timezone": local_timezone_name(),
        # Backward-compatible key. Prefer start_time_utc/start_time_local.
        "date_utc": start_utc_iso or event.get("strTimestamp") or event.get("dateEvent"),
        "source_time_fields": {
            "strTimestamp": event.get("strTimestamp"),
            "dateEvent": event.get("dateEvent"),
            "strTime": event.get("strTime"),
            "dateEventLocal": event.get("dateEventLocal"),
            "strTimeLocal": event.get("strTimeLocal"),
        },
        "league": event.get("strLeague"),
        "status": event.get("strStatus"),
    }


async def _fetch_mlb_schedule(
    client: httpx.AsyncClient,
    canonical: str,
    *,
    league: str | None,
) -> dict[str, Any] | None:
    team_id = _MLB_TEAM_IDS.get(canonical)
    if team_id is None:
        return None
    if league and "mlb" not in league.lower() and "baseball" not in league.lower():
        return None

    current = now()
    start_date = (current.date() - timedelta(days=7)).isoformat()
    end_date = (current.date() + timedelta(days=14)).isoformat()

    try:
        resp = await client.get(
            f"{MLB_STATS_BASE}/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "startDate": start_date,
                "endDate": end_date,
                "hydrate": "probablePitcher(note)",
            },
        )
        resp.raise_for_status()
        dates = resp.json().get("dates") or []
    except Exception as exc:
        logger.warning("[sports] MLB StatsAPI fetch failed team=%s: %s", canonical, exc)
        return None

    games: list[dict[str, Any]] = []
    for day in dates:
        games.extend(day.get("games") or [])
    if not games:
        return None

    def game_start(game: dict[str, Any]):
        return parse_iso_datetime(game.get("gameDate"))

    games_with_start = [(game_start(g), g) for g in games]
    games_with_start = [(dt, g) for dt, g in games_with_start if dt is not None]
    if not games_with_start:
        return None
    games_with_start.sort(key=lambda item: item[0])

    current_utc = current.astimezone(timezone.utc)
    final_games: list[dict[str, Any]] = []
    live_games: list[dict[str, Any]] = []
    upcoming_games: list[dict[str, Any]] = []
    for start_utc, game in games_with_start:
        status = game.get("status") or {}
        abstract_state = str(status.get("abstractGameState") or "").lower()
        detailed_state = str(status.get("detailedState") or "").lower()
        if abstract_state == "live" or "in progress" in detailed_state:
            live_games.append(game)
        elif abstract_state == "final" or detailed_state in {"final", "game over"}:
            final_games.append(game)
        elif start_utc >= current_utc:
            upcoming_games.append(game)

    result = {
        "team_canonical": canonical,
        "last_match": _format_mlb_game(final_games[-1]) if final_games else None,
        "live_match": _format_mlb_game(live_games[0]) if live_games else None,
        "next_match": _format_mlb_game(upcoming_games[0]) if upcoming_games else None,
        "source": "mlb_stats_api",
    }
    result["confidence"] = _classify_confidence(result)
    return result


def _format_mlb_game(game: dict[str, Any]) -> dict[str, Any]:
    teams = game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    status = game.get("status") or {}
    start_utc = parse_iso_datetime(game.get("gameDate"))
    start_local = start_utc.astimezone(local_tz()) if start_utc is not None else None
    return {
        "home": home_team.get("name"),
        "away": away_team.get("name"),
        "home_score": _score_to_str(home.get("score")),
        "away_score": _score_to_str(away.get("score")),
        "start_time_utc": start_utc.isoformat() if start_utc is not None else None,
        "start_time_local": start_local.isoformat() if start_local is not None else None,
        "local_date": start_local.date().isoformat() if start_local is not None else None,
        "local_time": start_local.strftime("%H:%M") if start_local is not None else None,
        "timezone": local_timezone_name(),
        "date_utc": start_utc.isoformat() if start_utc is not None else None,
        "official_date": game.get("officialDate"),
        "venue": (game.get("venue") or {}).get("name"),
        "game_id": game.get("gamePk"),
        "source_time_fields": {
            "gameDate": game.get("gameDate"),
            "officialDate": game.get("officialDate"),
        },
        "league": "MLB",
        "status": status.get("detailedState") or status.get("abstractGameState"),
        "probable_pitchers": {
            "home": ((home.get("probablePitcher") or {}).get("fullName")),
            "away": ((away.get("probablePitcher") or {}).get("fullName")),
        },
    }


def _score_to_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _event_start_utc(event: dict[str, Any]):
    raw_timestamp = event.get("strTimestamp")
    parsed = parse_iso_datetime(raw_timestamp)
    if parsed is not None:
        return parsed

    date_event = str(event.get("dateEvent") or "").strip()
    str_time = str(event.get("strTime") or "").strip()
    if date_event and str_time:
        parsed = parse_iso_datetime(f"{date_event}T{str_time}")
        if parsed is not None:
            return parsed

    if date_event:
        return parse_iso_datetime(date_event)
    return None


def _classify_confidence(result: dict[str, Any]) -> str:
    if result.get("live_match"):
        return "live"
    last = result.get("last_match") or {}
    parsed = parse_iso_datetime(last.get("start_time_utc") or last.get("date_utc"))
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
    from src.core.tool_dispatcher import ServiceContextView
    svc = ServiceContextView(ctx.services or {})
    result = await get_sports_score(
        team,
        league=league,
        llm_router=svc.llm_router,
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
