"""Yahoo Fantasy Sports API client.

Provides authenticated HTTP client for Yahoo Fantasy Sports API with
OAuth token management, request retry logic, and error handling.
"""

from __future__ import annotations

import contextlib
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from moose_api.core.config import settings
from moose_api.core.security import encrypt_token

logger = logging.getLogger(__name__)

YAHOO_API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


class YahooAPIError(Exception):
    """Raised when Yahoo Fantasy Sports API requests fail."""


class YahooLeagueMeta(BaseModel):
    league_key: str
    name: str
    season: int
    num_teams: int
    scoring_type: str
    current_week: int
    start_week: int
    end_week: int
    stat_categories: list[dict[str, Any]]
    roster_positions: list[dict[str, Any]]


class YahooTeamData(BaseModel):
    team_key: str
    name: str
    manager_guid: str | None = None
    manager_name: str | None = None
    logo_url: str | None = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    standing: int | None = None


class YahooMatchupData(BaseModel):
    week: int
    team_a_key: str
    team_b_key: str
    team_a_stats: dict[str, Any] = {}
    team_b_stats: dict[str, Any] = {}
    is_complete: bool = False


class YahooPlayerData(BaseModel):
    player_key: str
    player_id: str
    name: str
    primary_position: str
    eligible_positions: list[str] = []
    team_abbr: str | None = None
    is_pitcher: bool = False
    yahoo_rank: int | None = None


class YahooClient:
    """
    Client for interacting with the Yahoo Fantasy Sports API.
    Handles authentication, token refresh, and data retrieval for leagues, teams, rosters, and players.
    """

    def __init__(self, access_token: str, refresh_token: str, user_id: int | None = None):
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._user_id = user_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=15),
        retry=retry_if_exception_type((httpx.ReadError, httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _request(self, url: str, params: dict | None = None, cache_ttl: int | None = None) -> str:
        if cache_ttl:
            import hashlib

            from moose_api.core.redis import get_cached, set_cached

            # Hash to avoid extremely long Redis keys
            key_suffix = hashlib.md5(f"{url}{params}".encode()).hexdigest()
            cache_key = f"yahoo:req:{key_suffix}"
            cached = await get_cached(cache_key)
            if cached:
                return cached

        # Quick rate-limit delay to avoid rapid hammering
        import asyncio

        await asyncio.sleep(0.5)

        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = await self._client.get(url, headers=headers, params=params)

        if resp.status_code == 401:
            await self._refresh_access_token()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = await self._client.get(url, headers=headers, params=params)

        if resp.status_code != 200:
            raise YahooAPIError(f"Yahoo API error {resp.status_code}: {resp.text[:500]}")

        if cache_ttl:
            await set_cached(cache_key, resp.text, cache_ttl)

        return resp.text

    async def _refresh_access_token(self):
        import base64

        credentials = base64.b64encode(f"{settings.yahoo_client_id}:{settings.yahoo_client_secret}".encode()).decode()

        resp = await self._client.post(
            "https://api.login.yahoo.com/oauth2/get_token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "redirect_uri": settings.yahoo_redirect_uri,
            },
        )

        if resp.status_code != 200:
            raise YahooAPIError(f"Token refresh failed: {resp.status_code}")

        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)

        if self._user_id:
            from sqlalchemy import select

            from moose_api.core.database import async_session_factory
            from moose_api.models.yahoo_token import YahooToken

            async with async_session_factory() as session:
                result = await session.execute(select(YahooToken).where(YahooToken.user_id == self._user_id))
                token = result.scalar_one_or_none()
                if token:
                    token.access_token_encrypted = encrypt_token(self._access_token)
                    token.refresh_token_encrypted = encrypt_token(self._refresh_token)
                    await session.commit()

    async def get_league_meta(self, league_key: str, force_refresh: bool = False) -> YahooLeagueMeta:
        url = f"{YAHOO_API_BASE}/league/{league_key}/settings"
        if force_refresh:
            import hashlib

            from moose_api.core.redis import invalidate_cache

            key_suffix = hashlib.md5(f"{url}{None}".encode()).hexdigest()
            await invalidate_cache(f"yahoo:req:{key_suffix}")
        # League settings rarely change; cache for 24h
        raw = await self._request(url, cache_ttl=86400)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        league_el = root.find(".//y:league", ns)
        if league_el is None:
            raise YahooAPIError("Could not parse league element from response")

        def _text(parent, tag, default=""):
            el = parent.find(f"y:{tag}", ns)
            return el.text if el is not None and el.text else default

        stat_cats = []
        for cat in root.findall(".//y:stat_categories/y:stats/y:stat", ns):
            stat_cats.append(
                {
                    "stat_id": int(_text(cat, "stat_id", "0")),
                    "display_name": _text(cat, "display_name"),
                    "position_type": _text(cat, "position_type"),
                    "is_only_display_stat": _text(cat, "is_only_display_stat", "0") == "1",
                }
            )

        roster_pos = []
        for pos in root.findall(".//y:roster_positions/y:roster_position", ns):
            roster_pos.append(
                {
                    "position": _text(pos, "position"),
                    "count": int(_text(pos, "count", "0")),
                    "position_type": _text(pos, "position_type"),
                }
            )

        return YahooLeagueMeta(
            league_key=_text(league_el, "league_key"),
            name=_text(league_el, "name"),
            season=int(_text(league_el, "season", "2026")),
            num_teams=int(_text(league_el, "num_teams", "0")),
            scoring_type=_text(league_el, "scoring_type"),
            current_week=int(_text(league_el, "current_week", "1")),
            start_week=int(_text(league_el, "start_week", "1")),
            end_week=int(_text(league_el, "end_week", "104")),  # Absolute maximum possible
            stat_categories=stat_cats,
            roster_positions=roster_pos,
        )

    async def get_standings(self, league_key: str) -> list[YahooTeamData]:
        url = f"{YAHOO_API_BASE}/league/{league_key}/standings"
        # Standings update daily; cache for 4h
        raw = await self._request(url, cache_ttl=14400)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        def _team_text(tel, tag, default=""):
            el = tel.find(f"y:{tag}", ns)
            return el.text if el is not None and el.text else default

        teams = []
        for team_el in root.findall(".//y:team", ns):
            standings_el = team_el.find(".//y:team_standings", ns)
            wins = losses = ties = 0
            rank = None
            if standings_el is not None:
                oc = standings_el.find("y:outcome_totals", ns)
                if oc is not None:
                    wins = int(oc.findtext("y:wins", "0", ns))
                    losses = int(oc.findtext("y:losses", "0", ns))
                    ties = int(oc.findtext("y:ties", "0", ns))
                rank_el = standings_el.find("y:rank", ns)
                if rank_el is not None and rank_el.text:
                    rank = int(rank_el.text)

            managers_el = team_el.find(".//y:managers/y:manager", ns)
            guid = None
            manager_name = None
            if managers_el is not None:
                guid_el = managers_el.find("y:guid", ns)
                if guid_el is not None:
                    guid = guid_el.text
                nickname_el = managers_el.find("y:nickname", ns)
                if nickname_el is not None and nickname_el.text:
                    manager_name = nickname_el.text

            logo_el = team_el.find(".//y:team_logos/y:team_logo/y:url", ns)

            teams.append(
                YahooTeamData(
                    team_key=_team_text(team_el, "team_key"),
                    name=_team_text(team_el, "name"),
                    manager_guid=guid,
                    manager_name=manager_name,
                    logo_url=logo_el.text if logo_el is not None else None,
                    wins=wins,
                    losses=losses,
                    ties=ties,
                    standing=rank,
                )
            )

        return teams

    async def get_matchups(self, league_key: str, week: int) -> list[YahooMatchupData]:
        url = f"{YAHOO_API_BASE}/league/{league_key}/scoreboard;week={week}"
        # Matchups change frequently during games; cache for 15 minutes
        raw = await self._request(url, cache_ttl=900)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        matchups = []
        for matchup_el in root.findall(".//y:matchup", ns):
            teams_els = matchup_el.findall(".//y:team", ns)
            if len(teams_els) < 2:
                continue

            def _team_key(tel):
                el = tel.find("y:team_key", ns)
                return el.text if el is not None else ""

            def _team_stats(tel):
                stats = {}
                for stat in tel.findall(".//y:stat", ns):
                    sid = stat.findtext("y:stat_id", "", ns)
                    val = stat.findtext("y:value", "", ns)
                    if sid:
                        stats[sid] = val
                return stats

            status_el = matchup_el.find("y:status", ns)
            is_complete = (
                status_el is not None and status_el.text and status_el.text.lower() in ("postevent", "complete")
            )

            matchups.append(
                YahooMatchupData(
                    week=week,
                    team_a_key=_team_key(teams_els[0]),
                    team_b_key=_team_key(teams_els[1]),
                    team_a_stats=_team_stats(teams_els[0]),
                    team_b_stats=_team_stats(teams_els[1]),
                    is_complete=is_complete,
                )
            )

        return matchups

    async def get_roster(self, team_key: str, week: int | None = None) -> list[YahooPlayerData]:
        base_url = f"{YAHOO_API_BASE}/team/{team_key}/roster"
        modifiers: list[str] = []

        # Yahoo rejects ;out=ranks when a week projection is requested (preseason weeks in particular).
        if week is None:
            modifiers.append("out=ranks")
        if week:
            modifiers.append(f"week={week}")

        url = base_url if not modifiers else f"{base_url};" + ";".join(modifiers)

        # Rosters can change with daily transactions; cache for 2 hours
        raw = await self._request(url, cache_ttl=7200)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        def _pl_text(pel, tag, default=""):
            el = pel.find(f"y:{tag}", ns)
            return el.text if el is not None and el.text else default

        def _parse_player(player_el):
            name_el = player_el.find(".//y:name/y:full", ns)
            name = name_el.text if name_el is not None else _pl_text(player_el, "name")

            elig = []
            for ep in player_el.findall(".//y:eligible_positions/y:position", ns):
                if ep.text:
                    elig.append(ep.text)

            pos = _pl_text(
                player_el,
                "display_position",
                _pl_text(player_el, "primary_position"),
            )
            pitcher_positions = {"SP", "RP", "P"}

            # Parse Yahoo overall rank (OR)
            yahoo_rank = None
            ranks_el = player_el.find(".//y:player_ranks", ns)
            if ranks_el is not None:
                for rank_node in ranks_el.findall("y:player_rank", ns):
                    rtype = rank_node.find("y:rank_type", ns)
                    rval = rank_node.find("y:rank_value", ns)
                    if rtype is not None and rtype.text == "OR" and rval is not None and rval.text:
                        try:
                            yahoo_rank = int(float(rval.text.replace(",", "")))
                            break
                        except (ValueError, TypeError):
                            pass

            return YahooPlayerData(
                player_key=_pl_text(player_el, "player_key"),
                player_id=_pl_text(player_el, "player_id"),
                name=name,
                primary_position=pos,
                eligible_positions=elig,
                team_abbr=_pl_text(player_el, "editorial_team_abbr"),
                is_pitcher=pos in pitcher_positions or bool(set(elig) & pitcher_positions),
                yahoo_rank=yahoo_rank,
            )

        players = []
        for player_el in root.findall(".//y:player", ns):
            players.append(_parse_player(player_el))

        return players

    async def get_free_agents(self, league_key: str, start: int = 0, count: int = 25) -> list[YahooPlayerData]:
        url = f"{YAHOO_API_BASE}/league/{league_key}/players;status=A;out=ranks;start={start};count={count}"
        # Free agents pool caching - 1 hour is safe for big batches
        raw = await self._request(url, cache_ttl=3600)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        def _fa_text(pel, tag, default=""):
            el = pel.find(f"y:{tag}", ns)
            return el.text if el is not None and el.text else default

        def _parse_fa(player_el):
            name_el = player_el.find(".//y:name/y:full", ns)
            name = name_el.text if name_el is not None else _fa_text(player_el, "name")

            elig = []
            for ep in player_el.findall(".//y:eligible_positions/y:position", ns):
                if ep.text:
                    elig.append(ep.text)

            pos = _fa_text(
                player_el,
                "display_position",
                _fa_text(player_el, "primary_position"),
            )
            pitcher_positions = {"SP", "RP", "P"}

            # Parse Yahoo overall rank (OR)
            yahoo_rank = None
            ranks_el = player_el.find(".//y:player_ranks", ns)
            if ranks_el is not None:
                for rank_node in ranks_el.findall("y:player_rank", ns):
                    rtype = rank_node.find("y:rank_type", ns)
                    rval = rank_node.find("y:rank_value", ns)
                    if rtype is not None and rtype.text == "OR" and rval is not None and rval.text:
                        try:
                            yahoo_rank = int(float(rval.text.replace(",", "")))
                            break
                        except (ValueError, TypeError):
                            pass

            return YahooPlayerData(
                player_key=_fa_text(player_el, "player_key"),
                player_id=_fa_text(player_el, "player_id"),
                name=name,
                primary_position=pos,
                eligible_positions=elig,
                team_abbr=_fa_text(player_el, "editorial_team_abbr"),
                is_pitcher=pos in pitcher_positions or bool(set(elig) & pitcher_positions),
                yahoo_rank=yahoo_rank,
            )

        players = []
        for player_el in root.findall(".//y:player", ns):
            players.append(_parse_fa(player_el))

        return players

    async def get_draft_picks(self, league_key: str) -> list[dict]:
        """Fetch all draft picks for a league in order from Yahoo.

        Returns a list of dicts with keys: pick, round, team_key, player_key, player_name, position.

        Args:
            league_key: Yahoo league key (e.g. "mlb.l.12345")

        Returns:
            List of draft pick dicts sorted by pick number
        """
        url = f"{YAHOO_API_BASE}/league/{league_key}/draftresults"
        raw = await self._request(url)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        def _pick_text(el, tag, default=""):
            child = el.find(f"y:{tag}", ns)
            return child.text if child is not None and child.text else default

        picks = []
        for pick_el in root.findall(".//y:draft_result", ns):
            pick_num_raw = _pick_text(pick_el, "pick")
            round_raw = _pick_text(pick_el, "round")
            if not pick_num_raw:
                continue

            pick_num = int(pick_num_raw)
            round_num = int(round_raw) if round_raw else 0

            # Yahoo provides a cost field in auction drafts; snake draft has pick_cost=""
            team_key = _pick_text(pick_el, "team_key")
            player_key = _pick_text(pick_el, "player_key")

            picks.append(
                {
                    "pick_number": pick_num,
                    "round_number": round_num,
                    "team_key": team_key,
                    "player_key": player_key,
                }
            )

        picks.sort(key=lambda p: p["pick_number"])
        return picks

    async def get_player_roster_percents(self, league_key: str, player_keys: list[str]) -> dict[str, float]:
        """Fetch percent_owned statistics for a batch of players (max 25)."""
        if not player_keys:
            return {}

        keys_str = ",".join(player_keys)
        url = f"{YAHOO_API_BASE}/league/{league_key}/players;player_keys={keys_str}/percent_owned"
        # Roster percent is generally daily/weekly moving average; cache for 12 hours
        raw = await self._request(url, cache_ttl=43200)
        root = ET.fromstring(raw)
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

        results = {}
        for player_el in root.findall(".//y:player", ns):
            key_el = player_el.find("y:player_key", ns)
            if key_el is None or not key_el.text:
                continue

            percent_el = player_el.find(".//y:percent_owned/y:value", ns)
            if percent_el is not None and percent_el.text:
                with contextlib.suppress(ValueError):
                    results[key_el.text] = float(percent_el.text)

        return results
