import logging
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    reraise=True,
)
def _fetch_statcast_batter_stats(season: int, min_pa: int):
    """Fetch batter expected stats from Statcast with retry logic."""
    from pybaseball import statcast_batter_expected_stats

    logger.info("Fetching Statcast batter expected stats for season %d (min PA: %d)", season, min_pa)
    df = statcast_batter_expected_stats(season, min_pa)

    if df is None:
        logger.warning("Statcast batter stats returned None")
        return pd.DataFrame()

    if not isinstance(df, pd.DataFrame):
        logger.error("Statcast batter stats returned unexpected type: %s", type(df))
        raise ValueError(f"Expected DataFrame, got {type(df)}")

    logger.info("Fetched %d batter records from Statcast", len(df))
    return df


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    reraise=True,
)
def _fetch_statcast_pitcher_stats(season: int, min_ip: int):
    """Fetch pitcher expected stats from Statcast with retry logic."""
    from pybaseball import statcast_pitcher_expected_stats

    logger.info("Fetching Statcast pitcher expected stats for season %d (min IP: %d)", season, min_ip)
    df = statcast_pitcher_expected_stats(season, min_ip)

    if df is None:
        logger.warning("Statcast pitcher stats returned None")
        return pd.DataFrame()

    if not isinstance(df, pd.DataFrame):
        logger.error("Statcast pitcher stats returned unexpected type: %s", type(df))
        raise ValueError(f"Expected DataFrame, got {type(df)}")

    logger.info("Fetched %d pitcher records from Statcast", len(df))
    return df


def _safe_dataframe_to_dict(df: pd.DataFrame, index_col: str, value_col: str) -> dict:
    """Safely convert DataFrame to dictionary with validation."""
    if df.empty:
        logger.warning("DataFrame is empty, returning empty dict")
        return {}

    if index_col not in df.columns:
        logger.error("Index column '%s' not found in DataFrame columns: %s", index_col, df.columns.tolist())
        return {}

    if value_col not in df.columns:
        logger.error("Value column '%s' not found in DataFrame columns: %s", value_col, df.columns.tolist())
        return {}

    try:
        result = df.set_index(index_col)[value_col].to_dict()
        logger.info("Successfully converted DataFrame to dict with %d entries", len(result))
        return result
    except Exception as e:
        logger.error("Failed to convert DataFrame to dict: %s", e)
        return {}


def _coerce_stat(value) -> float | None:
    """Convert a pandas/Statcast cell into a finite float or None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def run_sync_advanced_metrics():
    """Sync Statcast expected metrics (xwOBA, xERA) onto today's PlayerValueSnapshot.

    Pulls the two season-aggregate DataFrames from Baseball Savant via
    pybaseball, joins them to ``Player.mlb_id``, and upserts ``xwoba`` /
    ``xera`` into the ``(player_id, snapshot_date=today, type='season')``
    row. Uses a single bulk ``ON CONFLICT DO UPDATE`` instead of per-player
    SELECT-then-INSERT-or-UPDATE so a ~1.5k-player run completes in a
    single round-trip rather than ~3k.

    The job intentionally does NOT purge the pybaseball on-disk cache;
    the library's own TTL handles freshness and purging forces a multi-
    minute re-download of the entire season CSV on every run.
    """
    try:
        try:
            from pybaseball import cache

            cache.enable()
        except ImportError:
            logger.error("pybaseball is not installed")
            async with async_session_factory() as session:
                notif = CommissionerNotification(
                    type="sync_failure",
                    message="Advanced metrics sync failed: pybaseball not installed",
                )
                session.add(notif)
                await session.commit()
            return

        today = date.today()
        season = today.year

        logger.info("Fetching Statcast expected metrics for season %d...", season)
        try:
            batters_df = _fetch_statcast_batter_stats(season, 50)
        except Exception as e:
            logger.error("Failed to fetch Statcast batter stats after retries: %s", e)
            batters_df = pd.DataFrame()

        try:
            pitchers_df = _fetch_statcast_pitcher_stats(season, 20)
        except Exception as e:
            logger.error("Failed to fetch Statcast pitcher stats after retries: %s", e)
            pitchers_df = pd.DataFrame()

        if batters_df.empty and pitchers_df.empty:
            logger.warning("No Statcast data retrieved for batters or pitchers")
            async with async_session_factory() as session:
                notif = CommissionerNotification(
                    type="sync_failure",
                    message=f"Advanced metrics sync failed: No data retrieved from Statcast (season {season})",
                )
                session.add(notif)
                await session.commit()
            return

        batters_dict = _safe_dataframe_to_dict(batters_df, "player_id", "est_woba")
        pitchers_dict = _safe_dataframe_to_dict(pitchers_df, "player_id", "xera")

        logger.info(
            "Statcast data converted: %d batters, %d pitchers",
            len(batters_dict),
            len(pitchers_dict),
        )

        async with async_session_factory() as session:
            players_result = await session.execute(select(Player).where(Player.mlb_id.isnot(None)))
            players = list(players_result.scalars().all())
            players_with_mlb_id = len(players)

            existing_result = await session.execute(
                select(
                    PlayerValueSnapshot.player_id,
                    PlayerValueSnapshot.category_scores,
                    PlayerValueSnapshot.composite_value,
                    PlayerValueSnapshot.yahoo_rank,
                    PlayerValueSnapshot.our_rank,
                    PlayerValueSnapshot.injury_weight,
                    PlayerValueSnapshot.roster_percent,
                    PlayerValueSnapshot.roster_trend,
                ).where(
                    PlayerValueSnapshot.snapshot_date == today,
                    PlayerValueSnapshot.type == "season",
                )
            )
            existing_by_player = {row.player_id: row for row in existing_result}

            logger.info(
                "Processing %d players with MLB IDs (%d existing season snapshots for %s)",
                players_with_mlb_id,
                len(existing_by_player),
                today,
            )

            rows: list[dict] = []
            snapshots_created = 0
            snapshots_updated = 0

            for player in players:
                xwoba = _coerce_stat(batters_dict.get(str(player.mlb_id)))
                xera = _coerce_stat(pitchers_dict.get(str(player.mlb_id)))
                if xwoba is None and xera is None:
                    continue

                existing = existing_by_player.get(player.id)
                if existing is not None:
                    snapshots_updated += 1
                    row = {
                        "player_id": player.id,
                        "snapshot_date": today,
                        "type": "season",
                        "category_scores": existing.category_scores or {},
                        "composite_value": existing.composite_value,
                        "yahoo_rank": existing.yahoo_rank,
                        "our_rank": existing.our_rank,
                        "injury_weight": existing.injury_weight,
                        "roster_percent": existing.roster_percent,
                        "roster_trend": existing.roster_trend,
                    }
                else:
                    snapshots_created += 1
                    row = {
                        "player_id": player.id,
                        "snapshot_date": today,
                        "type": "season",
                        "category_scores": {},
                        "composite_value": 0.0,
                        "yahoo_rank": player.yahoo_rank,
                        "our_rank": None,
                        "injury_weight": 1.0,
                        "roster_percent": 0.0,
                        "roster_trend": 0.0,
                    }

                if xwoba is not None:
                    row["xwoba"] = xwoba
                if xera is not None:
                    row["xera"] = xera
                rows.append(row)

            if rows:
                stmt = pg_insert(PlayerValueSnapshot).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["player_id", "snapshot_date", "type"],
                    set_={
                        "xwoba": stmt.excluded.xwoba,
                        "xera": stmt.excluded.xera,
                    },
                )
                await session.execute(stmt)

            updates = len(rows)
            logger.info(
                "Advanced metrics sync complete: %d upserted (%d created, %d updated) / %d players with MLB IDs",
                updates,
                snapshots_created,
                snapshots_updated,
                players_with_mlb_id,
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"Advanced metrics sync complete: {updates} snapshots upserted with Statcast xwOBA/xERA "
                    f"({snapshots_created} new, {snapshots_updated} updated) from {len(batters_dict)} batters "
                    f"and {len(pitchers_dict)} pitchers."
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_advanced_metrics failed: %s", e, exc_info=True)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Advanced metrics sync failed: {str(e)[:500]}",
            )
            session.add(notif)
            await session.commit()
        raise
