"""Free agent availability polling.

Polls Yahoo Fantasy Sports API for free agent availability and stores
snapshots for waiver wire analysis and player value calculations.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from moose_api.core.database import async_session_factory
from moose_api.models.free_agent import FreeAgentSnapshot
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.tasks.sync_league import _get_yahoo_client, _resolve_league_key

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=5, max=15),
    retry=retry_if_exception_type((httpx.ReadError, httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
async def _fetch_free_agents(client, league_key, start):
    return await client.get_free_agents(league_key, start=start, count=25)


async def run_sync_free_agents():
    try:
        client = await _get_yahoo_client()
        league_key = _resolve_league_key()

        start = 0
        fa_data = []
        while True:
            batch = await _fetch_free_agents(client, league_key, start)

            if not batch:
                break
            fa_data.extend(batch)
            if len(batch) < 25:
                break
            start += 25

        await client.close()

        async with async_session_factory() as session:
            result = await session.execute(select(League).limit(1))
            league = result.scalar_one_or_none()
            if not league:
                return

            for fa in fa_data:
                player_result = await session.execute(select(Player).where(Player.yahoo_player_key == fa.player_key))
                player = player_result.scalar_one_or_none()

                if player is None:
                    player = Player(
                        yahoo_player_key=fa.player_key,
                        yahoo_player_id=fa.player_id,
                        name=fa.name,
                        primary_position=fa.primary_position,
                        eligible_positions=fa.eligible_positions,
                        team_abbr=fa.team_abbr,
                        is_pitcher=fa.is_pitcher,
                        yahoo_rank=fa.yahoo_rank,
                    )
                    session.add(player)
                    await session.flush()
                else:
                    if fa.yahoo_rank is not None:
                        player.yahoo_rank = fa.yahoo_rank

                snapshot = FreeAgentSnapshot(
                    league_id=league.id,
                    player_id=player.id,
                    is_available=True,
                )
                session.add(snapshot)

            await session.commit()
            logger.info("Free agent sync complete: %d players", len(fa_data))

            notif = CommissionerNotification(
                type="info",
                message=(f"Free agent sync complete: {len(fa_data)} players scanned"),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_free_agents failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Free agent sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
