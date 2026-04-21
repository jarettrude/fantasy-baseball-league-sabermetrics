"""Roster synchronization with Yahoo Fantasy Sports API.

Fetches and stores current roster assignments for all teams in the league.
Tracks roster changes by week and maintains historical roster data.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.roster import RosterSlot
from moose_api.models.team import Team
from moose_api.tasks.sync_league import _get_yahoo_client, _resolve_league_key

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def run_sync_roster():
    """Sync all team rosters from Yahoo for the current week."""
    try:
        client = await _get_yahoo_client()
        _resolve_league_key()

        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping roster sync")
                return

            current_week = league.current_week if league.current_week is not None else 1
            season = league.season

            teams_result = await session.execute(select(Team).where(Team.league_id == league.id))
            teams = teams_result.scalars().all()

            for team in teams:
                try:
                    roster_data = await client.get_roster(team.yahoo_team_key, current_week)
                except Exception as e:
                    logger.warning("Failed to fetch roster for team %s: %s", team.name, e)
                    continue

                # Mark all existing slots for this team/week as inactive before upserting
                existing_slots_result = await session.execute(
                    select(RosterSlot).where(
                        RosterSlot.team_id == team.id,
                        RosterSlot.week == current_week,
                        RosterSlot.season == season,
                    )
                )
                for slot in existing_slots_result.scalars().all():
                    slot.is_active = False

                for player_data in roster_data:
                    # Find or create player
                    player_result = await session.execute(
                        select(Player).where(Player.yahoo_player_key == player_data.player_key)
                    )
                    player = player_result.scalar_one_or_none()

                    if player is None:
                        player = Player(
                            yahoo_player_key=player_data.player_key,
                            yahoo_player_id=player_data.player_id,
                            name=player_data.name,
                            primary_position=player_data.primary_position,
                            eligible_positions=player_data.eligible_positions,
                            team_abbr=player_data.team_abbr,
                            is_pitcher=player_data.is_pitcher,
                            yahoo_rank=player_data.yahoo_rank,
                        )
                        session.add(player)
                        await session.flush()
                    else:
                        player.name = player_data.name
                        player.team_abbr = player_data.team_abbr
                        player.eligible_positions = player_data.eligible_positions
                        player.is_pitcher = player_data.is_pitcher
                        if player_data.yahoo_rank is not None:
                            player.yahoo_rank = player_data.yahoo_rank

                    # Upsert roster slot
                    slot_result = await session.execute(
                        select(RosterSlot).where(
                            RosterSlot.team_id == team.id,
                            RosterSlot.player_id == player.id,
                            RosterSlot.season == season,
                            RosterSlot.week == current_week,
                            RosterSlot.position == player_data.primary_position,
                        )
                    )
                    slot = slot_result.scalar_one_or_none()

                    if slot is None:
                        slot = RosterSlot(
                            team_id=team.id,
                            player_id=player.id,
                            position=player_data.primary_position,
                            season=season,
                            week=current_week,
                            is_active=True,
                        )
                        session.add(slot)
                    else:
                        slot.is_active = True
                        slot.updated_at = datetime.now(UTC)

                logger.info(
                    "Roster sync complete for %s: %d players (week %d)",
                    team.name,
                    len(roster_data),
                    current_week,
                )

            await session.commit()
            logger.info("Full roster sync complete for %d teams", len(teams))

            notif = CommissionerNotification(
                type="info",
                message=(f"Roster sync complete: {len(teams)} teams synced for week {current_week}"),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_roster failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Roster sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
    finally:
        with contextlib.suppress(Exception):
            await client.close()
