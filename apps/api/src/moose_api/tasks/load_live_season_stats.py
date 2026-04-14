"""Load live 2026 season stats from MLB Stats API with Statcast fallback.

Fetches season-to-date player statistics primarily from Baseball Savant Statcast API
(faster bulk data) with fallback to official MLB Stats API. Populates the StatLine
table for daily z-score computations.

Replaces FanGraphs as the primary source for in-season player statistics.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date

from sqlalchemy import delete, select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import StatLine
from moose_api.services.mlb_client import MLBClient
from moose_api.services.statcast_client import StatcastClient

logger = logging.getLogger(__name__)


async def _fetch_player_season_stats(
    mlb_client: MLBClient,
    mlb_id: int,
    season: int,
    bulk_statcast_data: dict[int, dict] | None,
) -> tuple[dict | None, bool]:
    """Fetch season stats for a single player from bulk Statcast or MLB API.

    Prioritizes bulk Statcast data, then MLB Stats API for inactive players.

    Args:
        mlb_client: MLB client for fallback
        mlb_id: MLB player ID
        season: Season year
        bulk_statcast_data: Pre-fetched bulk Statcast data dictionary

    Returns:
        Tuple of (stats dict or None, True if bulk data was used)
    """
    # Try bulk Statcast data first (fastest)
    if bulk_statcast_data and mlb_id in bulk_statcast_data:
        return bulk_statcast_data[mlb_id], True

    # Skip individual Statcast queries - they're slow and only work for active players
    # Go directly to MLB API for players not in bulk data (inactive players)
    # Fallback to MLB Stats API
    try:
        hitting_data = await mlb_client.get_player_stats(mlb_id, season, "hitting")

        if hitting_data and hitting_data.get("stats"):
            for stat_entry in hitting_data["stats"]:
                splits = stat_entry.get("splits", [])
                if splits:
                    stats = splits[0].get("stat", {})
                    if stats:
                        stats["is_pitcher"] = False
                        return stats, False

        pitching_data = await mlb_client.get_player_stats(mlb_id, season, "pitching")

        if pitching_data and pitching_data.get("stats"):
            for stat_entry in pitching_data["stats"]:
                splits = stat_entry.get("splits", [])
                if splits:
                    stats = splits[0].get("stat", {})
                    if stats:
                        stats["is_pitcher"] = True
                        return stats, False

        return None, False
    except Exception as e:
        logger.error("Error fetching MLB stats for player %d: %s", mlb_id, e)
        return None, False


def _map_mlb_stats_to_statline(mlb_stats: dict, player_id: int) -> StatLine:
    """Map MLB Stats API or Statcast response to StatLine model.

    Supports both MLB API camelCase (e.g., 'strikeOuts') and Statcast
    snake_case (e.g., 'strikeouts') field naming conventions.

    Args:
        mlb_stats: Stats dict from MLB API or Statcast
        player_id: Database player ID

    Returns:
        StatLine instance
    """
    is_pitcher = mlb_stats.get("is_pitcher", False)

    def get_stat(field_camel: str, field_snake: str, default=0):
        for field in (field_camel, field_snake):
            if field in mlb_stats:
                val = mlb_stats[field]
                if val is None or val == "":
                    continue
                if isinstance(val, (int, float)):
                    # Check for NaN values
                    if isinstance(val, float) and math.isnan(val):
                        continue
                    return val
                if isinstance(val, str):
                    try:
                        # Try converting to int first, then float for decimal stats
                        if "." in val:
                            return float(val)
                        else:
                            return int(val)
                    except (ValueError, TypeError):
                        continue
        return default

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
            wins=get_stat("wins", "wins"),
            saves=get_stat("saves", "saves"),
            strikeouts=get_stat("strikeOuts", "strikeouts"),
            innings_pitched=get_stat("inningsPitched", "innings_pitched"),
            earned_runs=get_stat("earnedRuns", "earned_runs"),
            walks=get_stat("baseOnBalls", "walks"),
            era=get_stat("era", "era", default=None),
            whip=get_stat("whip", "whip", default=None),
        )
    else:
        return StatLine(
            player_id=player_id,
            game_date=date.today(),
            source="mlb_api",
            is_pitcher=False,
            runs=get_stat("runs", "runs"),
            home_runs=get_stat("homeRuns", "home_runs"),
            rbi=get_stat("rbi", "rbi"),
            stolen_bases=get_stat("stolenBases", "stolen_bases"),
            hits=get_stat("hits", "hits"),
            at_bats=get_stat("atBats", "at_bats"),
            batting_avg=get_stat("avg", "avg", default=None),
            wins=0,
            saves=0,
            strikeouts=get_stat("strikeOuts", "strikeouts"),
            innings_pitched=0,
            earned_runs=0,
            walks=get_stat("baseOnBalls", "walks"),
            era=None,
            whip=None,
        )


async def run_load_live_season_stats():
    """Load live season stats from MLB Stats API for all players.

    This task fetches current season-to-date statistics primarily from
    Baseball Savant Statcast API (faster) with fallback to official
    MLB Stats API. Populates the StatLine table for daily z-score computations.

    Uses concurrent fetching with rate limiting for efficiency.
    """
    mlb_client = None
    statcast_client = None
    try:
        # Initialize Statcast client (may fail if pybaseball not installed)
        try:
            statcast_client = StatcastClient()
            logger.info("Statcast client initialized successfully")
        except ImportError:
            logger.warning("Statcast client unavailable, using MLB Stats API only")
            statcast_client = None

        mlb_client = MLBClient()
        season = date.today().year

        # Fetch bulk Statcast data once if available
        bulk_statcast_data = None
        if statcast_client:
            try:
                start_dt = f"{season}-03-01"
                end_dt = f"{season}-12-31"
                logger.info("Fetching bulk Statcast data for season %d", season)

                # Fetch both hitting and pitching data
                hitting_data = await statcast_client.fetch_bulk_stats(start_dt, end_dt, "hitting")
                pitching_data = await statcast_client.fetch_bulk_stats(start_dt, end_dt, "pitching")

                # Merge the data (handle players who appear in both)
                bulk_statcast_data = {}
                for player_id, stats in hitting_data.items():
                    bulk_statcast_data[player_id] = stats
                for player_id, stats in pitching_data.items():
                    # If player already exists (two-way player), keep the pitcher role
                    bulk_statcast_data[player_id] = stats

                logger.info(
                    "Fetched Statcast data for %d players (%d hitting, %d pitching)",
                    len(bulk_statcast_data),
                    len(hitting_data),
                    len(pitching_data),
                )
            except Exception as e:
                logger.warning("Bulk Statcast fetch failed, falling back to individual queries: %s", e)
                bulk_statcast_data = None

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
            bulk_hit_count = 0
            individual_fallback_count = 0
            log_interval = max(1, total // 10)

            async def fetch_and_store(player: Player):
                nonlocal inserted_count, failed_count, bulk_hit_count, individual_fallback_count
                try:
                    if not player.mlb_id:
                        return (player.id, None, None, None)
                    mlb_stats, used_bulk = await _fetch_player_season_stats(
                        mlb_client, player.mlb_id, season, bulk_statcast_data
                    )
                    if used_bulk:
                        bulk_hit_count += 1
                    else:
                        individual_fallback_count += 1
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
                "Live season stats load complete: %d inserted, %d failed, %d from bulk, %d from fallback (%.1f%% bulk hit rate)",
                inserted_count,
                failed_count,
                bulk_hit_count,
                individual_fallback_count,
                (bulk_hit_count / total * 100) if total > 0 else 0,
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
        if statcast_client:
            await statcast_client.close()
        if mlb_client:
            await mlb_client.close()
