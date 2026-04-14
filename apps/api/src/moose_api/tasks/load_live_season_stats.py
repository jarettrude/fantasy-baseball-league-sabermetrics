"""Load live 2026 season stats from MLB Stats API.

Fetches season-to-date player statistics from the official MLB Stats API
and populates the StatLine table for daily z-score computations.

Replaces FanGraphs as the primary source for in-season player statistics.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import delete, select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import StatLine
from moose_api.services.mlb_client import MLBClient

logger = logging.getLogger(__name__)


async def _fetch_player_season_stats(mlb_client: MLBClient, mlb_id: int, season: int) -> dict | None:
    """Fetch season-to-date stats for a single player from MLB Stats API.

    Args:
        mlb_client: MLB Stats API client
        mlb_id: MLB player ID
        season: Season year

    Returns:
        Dict with hitting or pitching stats, or None if no data available
    """
    try:
        hitting_data = await mlb_client.get_player_stats(mlb_id, season, "hitting")

        if hitting_data and hitting_data.get("stats"):
            stats = hitting_data["stats"][0].get("splits", [{}])[0].get("stat", {})
            if stats:
                stats["is_pitcher"] = False
                return stats

        pitching_data = await mlb_client.get_player_stats(mlb_id, season, "pitching")

        if pitching_data and pitching_data.get("stats"):
            stats = pitching_data["stats"][0].get("splits", [{}])[0].get("stat", {})
            if stats:
                stats["is_pitcher"] = True
                return stats

        return None

    except Exception as e:
        logger.warning("Failed to fetch stats for MLB ID %d: %s", mlb_id, e)
        return None


def _map_mlb_stats_to_statline(mlb_stats: dict, player_id: int) -> StatLine:
    """Map MLB Stats API response to StatLine model.

    Args:
        mlb_stats: Stats dict from MLB API
        player_id: Database player ID

    Returns:
        StatLine instance
    """
    is_pitcher = mlb_stats.get("is_pitcher", False)

    if is_pitcher:
        return StatLine(
            player_id=player_id,
            game_date=date.today(),
            source="mlb_api",
            is_pitcher=True,
            runs=0,
            home_runs=0,
            rbi=0,
            stolen_bases=0,
            hits=0,
            at_bats=0,
            batting_avg=None,
            wins=mlb_stats.get("wins"),
            saves=mlb_stats.get("saves"),
            strikeouts=mlb_stats.get("strikeOuts"),
            innings_pitched=mlb_stats.get("inningsPitched"),
            earned_runs=mlb_stats.get("earnedRuns"),
            walks=mlb_stats.get("baseOnBalls"),
            era=mlb_stats.get("era"),
            whip=mlb_stats.get("whip"),
        )
    else:
        return StatLine(
            player_id=player_id,
            game_date=date.today(),
            source="mlb_api",
            is_pitcher=False,
            runs=mlb_stats.get("runs"),
            home_runs=mlb_stats.get("homeRuns"),
            rbi=mlb_stats.get("rbi"),
            stolen_bases=mlb_stats.get("stolenBases"),
            hits=mlb_stats.get("hits"),
            at_bats=mlb_stats.get("atBats"),
            batting_avg=mlb_stats.get("avg"),
            wins=0,
            saves=0,
            strikeouts=mlb_stats.get("strikeOuts"),
            innings_pitched=0,
            earned_runs=0,
            walks=mlb_stats.get("baseOnBalls"),
            era=None,
            whip=None,
        )


async def run_load_live_season_stats():
    """Load live season stats from MLB Stats API for all players.

    This task fetches current season-to-date statistics and populates
    the StatLine table. It should run daily before the valuation engine
    to ensure z-scores are computed from live data.

    Uses concurrent fetching with rate limiting for efficiency.
    """
    mlb_client = None
    try:
        mlb_client = MLBClient()
        season = date.today().year

        async with async_session_factory() as session:
            players_result = await session.execute(select(Player).where(Player.mlb_id.isnot(None)))
            players = players_result.scalars().all()

            if not players:
                logger.warning("No players with MLB IDs found")
                return

            logger.info("Loading live season stats for %d players (season %d)", len(players), season)

            today = date.today()
            delete_result = await session.execute(
                delete(StatLine).where(
                    StatLine.game_date == today,
                    StatLine.source == "mlb_api",
                )
            )
            logger.info("Deleted %d existing StatLine entries for today", delete_result.rowcount)

            total = len(players)
            inserted_count = 0
            failed_count = 0
            log_interval = max(1, total // 10)

            async def fetch_and_store(player: Player):
                nonlocal inserted_count, failed_count
                try:
                    if not player.mlb_id:
                        return (player.id, None, None, None)
                    mlb_stats = await _fetch_player_season_stats(mlb_client, player.mlb_id, season)
                    if mlb_stats:
                        statline = _map_mlb_stats_to_statline(mlb_stats, player.id)
                        return (player.id, statline, None, None)
                    else:
                        failed_count += 1
                        return (player.id, None, None, None)
                except Exception as e:
                    return (player.id, None, e, str(e))

            tasks = [fetch_and_store(player) for player in players]
            results = await asyncio.gather(*tasks)

            for i, result in enumerate(results, 1):
                player_id, statline, error, error_msg = result
                if error:
                    logger.warning("Error fetching stats for player ID %d: %s", player_id, error_msg)
                    failed_count += 1
                elif statline:
                    session.add(statline)
                    inserted_count += 1
                if i % log_interval == 0 or i == total:
                    logger.info(
                        "Progress: %d/%d players processed (%.1f%%), %d inserted, %d failed",
                        i,
                        total,
                        (i / total) * 100,
                        inserted_count,
                        failed_count,
                    )

            await session.commit()

            logger.info(
                "Live season stats load complete: %d inserted, %d failed",
                inserted_count,
                failed_count,
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"Live season stats loaded: {inserted_count} players updated, "
                    f"{failed_count} players had no data (season {season})"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("load_live_season_stats failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Live season stats load failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
    finally:
        if mlb_client:
            await mlb_client.close()
