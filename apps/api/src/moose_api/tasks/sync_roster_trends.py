"""Sync Roster Percentages and calculate 3-day Trends.

Hits the Yahoo percent_owned endpoint in batches of 25.
Updates the PlayerValueSnapshot for today with roster_percent and roster_trend.
"""

import asyncio
import contextlib
import logging
from datetime import date, timedelta

from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot
from moose_api.tasks.sync_league import _get_yahoo_client, _resolve_league_key

logger = logging.getLogger(__name__)


async def run_sync_roster_trends():
    """Sync roster percent_owned and calculate 3-day trends."""
    try:
        client = await _get_yahoo_client()
        league_key = _resolve_league_key()

        async with async_session_factory() as session:
            players_res = await session.execute(select(Player))
            players = players_res.scalars().all()

            if not players:
                logger.info("No players found. Sync free agents first.")
                return

            today = date.today()
            trend_date = today - timedelta(days=3)

            past_snaps_res = await session.execute(
                select(PlayerValueSnapshot).where(
                    PlayerValueSnapshot.snapshot_date == trend_date,
                    PlayerValueSnapshot.type == "season",
                )
            )
            past_snaps: dict[int, PlayerValueSnapshot] = {s.player_id: s for s in past_snaps_res.scalars().all()}

            today_snaps_res = await session.execute(
                select(PlayerValueSnapshot).where(
                    PlayerValueSnapshot.snapshot_date == today, PlayerValueSnapshot.type == "season"
                )
            )
            today_snaps: dict[int, PlayerValueSnapshot] = {s.player_id: s for s in today_snaps_res.scalars().all()}

            batch_size = 25
            updates_count = 0

            for i in range(0, len(players), batch_size):
                batch = players[i : i + batch_size]
                keys = [p.yahoo_player_key for p in batch]

                try:
                    percents = await client.get_player_roster_percents(league_key, keys)
                except Exception as e:
                    logger.warning("Failed to fetch percent_owned for batch: %s", e)
                    continue

                for p in batch:
                    if p.yahoo_player_key in percents:
                        current_pct = percents[p.yahoo_player_key]
                        snap = today_snaps.get(p.id)
                        if snap:
                            snap.roster_percent = current_pct

                            past_snap = past_snaps.get(p.id)
                            if past_snap and past_snap.roster_percent is not None:
                                snap.roster_trend = current_pct - float(past_snap.roster_percent)
                            else:
                                snap.roster_trend = 0.0

                            updates_count += 1

                # Very important to respect Yahoo rate limits
                # Yahoo allows ~10,000 requests/day, but too many concurrent bursts
                # will cause 429 Too Many Requests
                await asyncio.sleep(0.5)

            await session.commit()

            logger.info("Updated roster trends for %d players", updates_count)
            notif = CommissionerNotification(
                type="info", message=f"Roster trend sync complete: updated {updates_count} players."
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_roster_trends failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Roster trends sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
    finally:
        with contextlib.suppress(Exception):
            await client.close()
