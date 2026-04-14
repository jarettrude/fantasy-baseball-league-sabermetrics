import logging
from datetime import date

import pandas as pd
from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot

logger = logging.getLogger(__name__)


async def run_sync_advanced_metrics():
    """Sync Statcast Expected Metrics (xwOBA, xERA) to today's PlayerValueSnapshot."""
    try:
        try:
            from pybaseball import (
                cache,
                statcast_batter_expected_stats,
                statcast_pitcher_expected_stats,
            )

            cache.purge()
            cache.enable()
        except ImportError:
            logger.error("pybaseball is not installed")
            return

        today = date.today()
        season = today.year

        logger.info("Fetching Statcast expected metrics...")
        try:
            batters_df = statcast_batter_expected_stats(season, 50)
            pitchers_df = statcast_pitcher_expected_stats(season, 20)
        except Exception as e:
            logger.error("Failed to fetch Statcast data: %s", e)
            async with async_session_factory() as session:
                notif = CommissionerNotification(
                    type="sync_failure",
                    message=f"Advanced metrics sync failed (Statcast fetch): {e}",
                )
                session.add(notif)
                await session.commit()
            raise

        batters_dict = batters_df.set_index("player_id")["est_woba"].to_dict() if not batters_df.empty else {}
        pitchers_dict = pitchers_df.set_index("player_id")["xera"].to_dict() if not pitchers_df.empty else {}

        updates = 0
        async with async_session_factory() as session:
            players_result = await session.execute(select(Player).where(Player.mlb_id.isnot(None)))
            players = players_result.scalars().all()

            for player in players:
                xwoba = batters_dict.get(str(player.mlb_id))
                xera = pitchers_dict.get(str(player.mlb_id))

                if xwoba is not None and pd.isna(xwoba):
                    xwoba = None
                if xera is not None and pd.isna(xera):
                    xera = None

                if xwoba is not None or xera is not None:
                    snapshots_result = await session.execute(
                        select(PlayerValueSnapshot).where(
                            PlayerValueSnapshot.player_id == player.id,
                            PlayerValueSnapshot.snapshot_date == today,
                        )
                    )
                    snap = snapshots_result.scalars().first()

                    if not snap:
                        snap = PlayerValueSnapshot(
                            player_id=player.id,
                            snapshot_date=today,
                            type="season",
                            category_scores={},
                            composite_value=0.0,
                            yahoo_rank=player.yahoo_rank,
                            our_rank=None,
                            injury_weight=1.0,
                            roster_percent=0.0,
                            roster_trend=0.0,
                        )
                        session.add(snap)
                        updates += 1

                    if snap:
                        if xwoba is not None:
                            snap.xwoba = float(xwoba)
                        if xera is not None:
                            snap.xera = float(xera)
                        updates += 1

            await session.commit()

            notif = CommissionerNotification(
                type="info",
                message=f"Advanced metrics sync complete: {updates} snapshots updated with Statcast xwOBA/xERA.",
            )
            session.add(notif)
            await session.commit()

        logger.info("Updated %d PlayerValueSnapshot entries with Statcast metrics", updates)

    except Exception as e:
        logger.error("sync_advanced_metrics failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Advanced metrics sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
