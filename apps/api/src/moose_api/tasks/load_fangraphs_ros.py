"""Load Rest of Season data from FanGraphs JSON API.

This task hits the FanGraphs projections API directly. It attempts to fetch
'rfangraphsdc' (Rest of Season Depth Charts). If that returns empty (e.g. during
the offseason before Opening Day), it falls back seamlessly to 'fangraphsdc'
(Preseason Depth Charts).

The data is saved into `projection_baseline` with the source 'fangraphs_ros'.
Because `recompute_values` always grabs the most recently updated baseline for
a player, running this daily ensures player valuations use the freshest
futuristic predictions.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import ProjectionBaseline
from moose_api.services.valuation_engine import (
    StatCategory,
)

logger = logging.getLogger(__name__)

FG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
FG_API_URL = "https://www.fangraphs.com/api/projections"


async def _fetch_fangraphs_data(stats_type: str) -> list[dict]:
    """Fetch data from FanGraphs API, with fallback to preseason if RoS is empty.

    Includes retry logic for rate limiting (429) and WAF blocks (403),
    and caches results in Redis for 6 hours.
    """
    from moose_api.core.redis import get_cached, set_cached

    cache_key = f"fangraphs:ros:{stats_type}"
    cached = await get_cached(cache_key)
    if cached:
        logger.info("Using cached FanGraphs RoS data for %s", stats_type)
        return cached

    max_retries = 3
    data: list[dict] = []

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "pos": "all",
                    "stats": stats_type,
                    "type": "rfangraphsdc",
                    "team": "0",
                    "players": "0",
                    "pageitems": "100000",
                    "pg": "1",
                }
                logger.info("Fetching Rest of Season (rfangraphsdc) for %s (attempt %d)...", stats_type, attempt + 1)

                # Polite delay
                if attempt > 0:
                    delay = 5 * (2**attempt)
                    logger.info("Backing off %ds before retry...", delay)
                    import asyncio

                    await asyncio.sleep(delay)

                res = await client.get(FG_API_URL, params=params, headers=FG_HEADERS, timeout=30.0)

                if res.status_code == 429:
                    retry_after = res.headers.get("Retry-After", "30")
                    logger.warning("FanGraphs rate limited (429), sleeping %ss", retry_after)
                    import asyncio

                    await asyncio.sleep(float(retry_after))
                    continue

                if res.status_code == 403:
                    logger.warning("FanGraphs returned 403 — WAF may be blocking our requests")
                    import asyncio

                    await asyncio.sleep(10)
                    continue

                res.raise_for_status()
                data = res.json()

                if not data:
                    logger.info("RoS data empty (likely offseason). Falling back to basic Depth Charts.")
                    params["type"] = "fangraphsdc"
                    res = await client.get(FG_API_URL, params=params, headers=FG_HEADERS, timeout=30.0)
                    res.raise_for_status()
                    data = res.json()

                break  # Success

        except httpx.HTTPStatusError:
            if attempt == max_retries - 1:
                raise
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("FanGraphs request failed: %s (attempt %d)", e, attempt + 1)
            if attempt == max_retries - 1:
                raise

    logger.info("Fetched %d %s from FanGraphs API", len(data), stats_type)

    # Cache for 6 hours
    if data:
        await set_cached(cache_key, data, 21600)

    return data


def extract_batting_stats(row: dict) -> dict[str, float]:
    """Extract standard batting stats."""
    stats: dict[str, float] = {}
    for col in ["R", "HR", "RBI", "SB", "H", "AB", "AVG", "OBP", "SLG", "BB", "SO"]:
        val = row.get(col)
        if val is not None:
            stats[col] = float(val)
    return stats


def extract_pitching_stats(row: dict) -> dict[str, float]:
    """Extract standard pitching stats."""
    stats: dict[str, float] = {}
    for col in ["W", "SV", "SO", "ERA", "WHIP", "IP", "ER", "BB"]:
        val = row.get(col)
        if val is not None:
            stats[col] = float(val)
    return stats


def extract_advanced_stats(row: dict, is_pitcher: bool) -> dict[str, float]:
    """Extract advanced sabermetrics."""
    advanced: dict[str, float] = {}
    if is_pitcher:
        for col in ["FIP", "xFIP", "K/9", "BB/9", "HR/9", "BABIP", "LOB%", "WAR", "K%", "BB%"]:
            val = row.get(col)
            if val is not None:
                advanced[col] = float(val)
    else:
        for col in ["wOBA", "wRC+", "BABIP", "ISO", "WAR", "K%", "BB%"]:
            val = row.get(col)
            if val is not None:
                advanced[col] = float(val)
    return advanced


def _match_fg_to_player(fg_name: str, fg_team: str, db_players: list) -> Player | None:
    fg_name_lower = fg_name.strip().lower()
    fg_team_upper = (fg_team or "").strip().upper()

    for player in db_players:
        db_name_lower = player.name.strip().lower()
        db_team_upper = (player.team_abbr or "").strip().upper()
        if db_name_lower == fg_name_lower:
            if not fg_team_upper or not db_team_upper:
                return player
            if fg_team_upper == db_team_upper:
                return player

    for player in db_players:
        if player.name.strip().lower() == fg_name_lower:
            return player

    return None


async def run_load_fangraphs_ros():
    """Load RoS FanGraphs stats and build Rest of Season projection baselines."""
    try:
        batters_data = await _fetch_fangraphs_data("bat")
        pitchers_data = await _fetch_fangraphs_data("pit")

        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found — sync league first")
                return

            categories = [StatCategory(**cat) for cat in (league.stat_categories or [])]
            if not categories:
                logger.warning("No stat categories configured")
                return

            players_result = await session.execute(select(Player))
            all_players = players_result.scalars().all()
            if not all_players:
                logger.info("No players in DB — sync rosters first")
                return

            source_name = "fangraphs_ros"
            matched_batters = 0
            matched_pitchers = 0

            # Batters
            for row in batters_data:
                fg_name = str(row.get("PlayerName", ""))
                fg_team = str(row.get("Team", ""))

                player = _match_fg_to_player(fg_name, fg_team, all_players)
                if not player:
                    continue

                stats = extract_batting_stats(row)
                if not stats:
                    continue

                advanced = extract_advanced_stats(row, is_pitcher=False)
                full_projection = {**stats, **advanced}

                existing = await session.execute(
                    select(ProjectionBaseline).where(
                        ProjectionBaseline.player_id == player.id,
                        ProjectionBaseline.source == source_name,
                        ProjectionBaseline.season == league.season,
                    )
                )
                baseline = existing.scalar_one_or_none()
                if baseline:
                    baseline.projected_stats = full_projection
                else:
                    baseline = ProjectionBaseline(
                        player_id=player.id,
                        source=source_name,
                        season=league.season,
                        projected_stats=full_projection,
                    )
                    session.add(baseline)

                matched_batters += 1

            # Pitchers
            for row in pitchers_data:
                fg_name = str(row.get("PlayerName", ""))
                fg_team = str(row.get("Team", ""))

                player = _match_fg_to_player(fg_name, fg_team, all_players)
                if not player:
                    continue

                stats = extract_pitching_stats(row)
                if not stats:
                    continue

                advanced = extract_advanced_stats(row, is_pitcher=True)
                full_projection = {**stats, **advanced}

                existing = await session.execute(
                    select(ProjectionBaseline).where(
                        ProjectionBaseline.player_id == player.id,
                        ProjectionBaseline.source == source_name,
                        ProjectionBaseline.season == league.season,
                    )
                )
                baseline = existing.scalar_one_or_none()
                if baseline:
                    baseline.projected_stats = full_projection
                else:
                    baseline = ProjectionBaseline(
                        player_id=player.id,
                        source=source_name,
                        season=league.season,
                        projected_stats=full_projection,
                    )
                    session.add(baseline)

                matched_pitchers += 1

            await session.commit()

            logger.info(
                "FanGraphs Rest of Season load complete: %d batters, %d pitchers matched",
                matched_batters,
                matched_pitchers,
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"FanGraphs Rest of Season projections loaded: "
                    f"{matched_batters} batters, "
                    f"{matched_pitchers} pitchers matched and updated."
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("load_fangraphs_ros failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"FanGraphs RoS data load failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
