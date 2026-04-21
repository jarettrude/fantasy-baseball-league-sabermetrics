"""MLB Statistics API client.

Provides HTTP client for MLB Statistics API with player data,
team information, and advanced statistics retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

MLB_API_MIN_SLEEP_SECONDS = float(os.getenv("MLB_API_MIN_SLEEP_SECONDS", "0.75"))
MLB_API_JITTER_SECONDS = float(os.getenv("MLB_API_JITTER_SECONDS", "0.35"))
MLB_API_RATE_LIMIT_COOLDOWN = float(os.getenv("MLB_API_RATE_LIMIT_COOLDOWN", "2.5"))
MLB_API_MAX_CONCURRENCY = max(1, int(os.getenv("MLB_API_MAX_CONCURRENCY", "2")))

_CONCURRENCY_SEMAPHORE = asyncio.Semaphore(MLB_API_MAX_CONCURRENCY)


class MLBRateLimitError(Exception):
    """Raised when the Stats API responds with HTTP 429."""


def _rate_limit_sleep_delay(retry_after_header: str | None) -> float:
    if retry_after_header:
        try:
            return max(float(retry_after_header), MLB_API_RATE_LIMIT_COOLDOWN)
        except ValueError:
            pass
    return MLB_API_RATE_LIMIT_COOLDOWN + random.uniform(0, MLB_API_JITTER_SECONDS)


class MLBGameSchedule(BaseModel):
    game_pk: int
    game_date: date
    game_datetime_utc: str
    home_team_id: int
    home_team_abbr: str
    away_team_id: int
    away_team_abbr: str
    status: str
    probable_pitcher_home_id: int | None = None
    probable_pitcher_away_id: int | None = None


class MLBPlayerInjury(BaseModel):
    player_id: int
    full_name: str
    team_id: int
    injury_status: str | None = None
    injury_description: str | None = None


class MLBClient:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    async def get_bulk_season_stats(
        self,
        season: int,
        group: str = "hitting",
        page_size: int = 1000,
        max_pages: int = 20,
    ) -> dict[int, dict]:
        """Fetch season-to-date stats for every MLB player in one group.

        Uses the league-wide `/stats` endpoint, which returns all players in a
        single group paginated by ``limit``/``offset``. This is dramatically
        faster than per-player ``/people/{id}/stats`` calls (seconds vs. hours)
        and returns the fantasy-relevant raw fields (R, HR, RBI, SB, AVG for
        hitting; W, SV, SO, ERA, WHIP, IP, ER, BB for pitching).

        Args:
            season: Season year (e.g. 2026).
            group: ``"hitting"`` or ``"pitching"``.
            page_size: Results per page (MLB API caps around 1000).
            max_pages: Safety cap to prevent runaway pagination.

        Returns:
            Mapping of ``mlb_player_id -> stat dict`` (camelCase from the API).
            Empty dict on failure (caller may fall back to per-player queries).
        """
        results: dict[int, dict] = {}
        offset = 0
        for _ in range(max_pages):
            data = await self._request(
                "stats",
                params={
                    "stats": "season",
                    "group": group,
                    "season": season,
                    "sportId": 1,
                    "gameType": "R",
                    "playerPool": "All",
                    "limit": page_size,
                    "offset": offset,
                },
                cache_ttl=1800,
            )
            if not data:
                break

            stats_blocks = data.get("stats") or []
            if not stats_blocks:
                break

            # /stats returns a list of stat-type blocks; for stats=season there
            # is one block whose ``splits`` holds the per-player rows.
            splits = stats_blocks[0].get("splits") or []
            if not splits:
                break

            for split in splits:
                person = split.get("player") or {}
                pid = person.get("id")
                stat = split.get("stat") or {}
                if pid and stat:
                    results[int(pid)] = stat

            total = stats_blocks[0].get("totalSplits")
            offset += len(splits)
            # Stop when we've consumed all rows or the page came back short.
            if (isinstance(total, int) and offset >= total) or len(splits) < page_size:
                break
        else:
            logger.warning(
                "Bulk %s stats pagination hit max_pages=%d (offset=%d); result may be truncated",
                group,
                max_pages,
                offset,
            )

        logger.info("Bulk MLB %s stats: fetched %d players (season=%d)", group, len(results), season)
        return results

    async def get_player_stats(self, player_id: int, season: int, group: str = "hitting") -> dict | None:
        """Get player stats from MLB Stats API.

        Args:
            player_id: MLB player ID
            season: Season year
            group: Stat group (hitting or pitching)

        Returns:
            Stats dict or None if unavailable
        """
        return await self._request(
            f"people/{player_id}/stats",
            params={"stats": "season", "season": season, "group": group},
            cache_ttl=3600,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (
                httpx.ReadError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
                MLBRateLimitError,
            )
        ),
        reraise=True,
    )
    async def _request(self, endpoint: str, params: dict | None = None, cache_ttl: int | None = None) -> dict | None:
        if cache_ttl:
            import hashlib

            from moose_api.core.redis import get_cached, set_cached

            key_suffix = hashlib.md5(f"{endpoint}{params}".encode()).hexdigest()
            cache_key = f"mlb:req:{key_suffix}"
            cached = await get_cached(cache_key)
            if cached:
                return cached

        async with _CONCURRENCY_SEMAPHORE:
            # Base sleep plus jitter to avoid thundering herd
            await asyncio.sleep(MLB_API_MIN_SLEEP_SECONDS + random.uniform(0, MLB_API_JITTER_SECONDS))

            resp = await self._client.get(
                f"{MLB_API_BASE}/{endpoint}",
                params=params,
            )

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            delay = _rate_limit_sleep_delay(retry_after)
            logger.warning(
                "MLB API rate limited (429) on %s; cooling down for %.2fs (Retry-After=%s)",
                endpoint,
                delay,
                retry_after,
            )
            await asyncio.sleep(delay)
            raise MLBRateLimitError(f"MLB Stats API rate limited on {endpoint}")

        if 400 <= resp.status_code < 500:
            logger.error("MLB API client error %s on %s", resp.status_code, endpoint)
            return None

        if resp.status_code >= 500:
            logger.warning("MLB API server error %s on %s", resp.status_code, endpoint)
            resp.raise_for_status()

        data = resp.json()
        if cache_ttl:
            await set_cached(cache_key, data, cache_ttl)

        return data

    async def get_schedule(self, start_date: date, end_date: date) -> list[MLBGameSchedule]:
        data = await self._request(
            "schedule",
            params={
                "sportId": 1,
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "hydrate": "probablePitcher,team",
            },
            cache_ttl=21600,  # Cache schedule for 6 hours
        )
        if not data:
            return []
        games = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home = game.get("teams", {}).get("home", {})
                away = game.get("teams", {}).get("away", {})

                pp_home = home.get("probablePitcher", {}).get("id")
                pp_away = away.get("probablePitcher", {}).get("id")

                games.append(
                    MLBGameSchedule(
                        game_pk=game["gamePk"],
                        game_date=date_entry["date"],
                        game_datetime_utc=game.get("gameDate", ""),
                        home_team_id=home.get("team", {}).get("id", 0),
                        home_team_abbr=home.get("team", {}).get("abbreviation", ""),
                        away_team_id=away.get("team", {}).get("id", 0),
                        away_team_abbr=away.get("team", {}).get("abbreviation", ""),
                        status=game.get("status", {}).get("detailedState", ""),
                        probable_pitcher_home_id=pp_home,
                        probable_pitcher_away_id=pp_away,
                    )
                )

        return games

    async def get_injuries(self, sport_id: int = 1) -> list[MLBPlayerInjury]:
        """Fetch current IL placements via the MLB transactions endpoint.

        The legacy /sports/{sportId}/injuries endpoint was removed (404).
        We now query /transactions for the last 60 days, filter for IL
        placements, remove players who were subsequently activated, and
        deduplicate to keep only the latest per player.
        """

        today = date.today()
        start = today - timedelta(days=60)

        data = await self._request(
            "transactions",
            params={
                "sportId": sport_id,
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": today.strftime("%Y-%m-%d"),
            },
            cache_ttl=3600,
        )
        if not data:
            return []

        il_pattern = re.compile(
            r"placed .+ on the (\d+-day) injured list",
            re.IGNORECASE,
        )

        current_il: dict[int, MLBPlayerInjury] = {}

        sorted_txns = sorted(
            data.get("transactions", []),
            key=lambda t: t.get("date", ""),
        )

        for txn in sorted_txns:
            desc = txn.get("description", "")
            player = txn.get("person", {})
            player_id = player.get("id", 0)

            match = il_pattern.search(desc)
            if match:
                team = txn.get("toTeam", {}) or txn.get("fromTeam", {})

                il_days = match.group(1).title()
                injury_status = f"{il_days} IL"

                injury_note = None
                il_pos = desc.lower().find("injured list")
                if il_pos != -1:
                    dot_after_il = desc.find(".", il_pos)
                    if dot_after_il != -1 and dot_after_il < len(desc) - 1:
                        injury_note = desc[dot_after_il + 1 :].strip().rstrip(".")
                        if not injury_note:
                            injury_note = None

                current_il[player_id] = MLBPlayerInjury(
                    player_id=player_id,
                    full_name=player.get("fullName", ""),
                    team_id=team.get("id", 0),
                    injury_status=injury_status,
                    injury_description=injury_note,
                )
                continue

            if "activated" in desc.lower() or "reinstated" in desc.lower():
                current_il.pop(player_id, None)

        return list(current_il.values())

    async def search_player(self, name: str) -> list[dict[str, Any]]:
        data = await self._request(
            "people/search",
            params={"names": name, "sportId": 1},
            cache_ttl=86400,  # Cache player searches for 24h
        )
        if not data:
            return []
        results = []
        for person in data.get("people", []):
            results.append(
                {
                    "id": person.get("id"),
                    "fullName": person.get("fullName"),
                    "primaryPosition": person.get("primaryPosition", {}).get("abbreviation"),
                    "currentTeam": person.get("currentTeam", {}).get("abbreviation"),
                }
            )
        return results

    async def search_players_chunked(self, names: list[str], chunk_size: int = 5) -> list[dict[str, Any]]:
        """Call the MLB search API in multiple smaller chunks to avoid query bloat."""

        if not names:
            return []

        chunk_size = max(1, chunk_size)
        combined: list[dict[str, Any]] = []

        for i in range(0, len(names), chunk_size):
            chunk = names[i : i + chunk_size]
            query = ",".join(chunk)
            try:
                combined.extend(await self.search_player(query))
            except Exception as exc:  # noqa: BLE001
                logger.warning("MLB chunked search failed for %s: %s", chunk, exc)

        return combined
