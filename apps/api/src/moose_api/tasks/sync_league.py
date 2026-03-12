"""League synchronization with Yahoo Fantasy Sports API.

Fetches and stores league metadata, team information, and weekly matchup data
from Yahoo Fantasy Sports API. Updates league configuration and team records.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from moose_api.core.config import settings
from moose_api.core.database import async_session_factory
from moose_api.core.redis import invalidate_cache
from moose_api.core.security import decrypt_token
from moose_api.models.league import League
from moose_api.models.matchup import Matchup
from moose_api.models.notification import CommissionerNotification
from moose_api.models.team import Team
from moose_api.models.user import User
from moose_api.models.yahoo_token import YahooToken
from moose_api.services.yahoo_client import YahooClient

logger = logging.getLogger(__name__)


async def _get_yahoo_client() -> YahooClient:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.yahoo_guid == settings.commissioner_yahoo_guid))
        commissioner = result.scalar_one_or_none()
        if not commissioner:
            raise RuntimeError("Commissioner user not found. Complete OAuth login first.")

        token_result = await session.execute(select(YahooToken).where(YahooToken.user_id == commissioner.id))
        token = token_result.scalar_one_or_none()
        if not token:
            raise RuntimeError("Commissioner Yahoo token not found.")

        access_token = decrypt_token(token.access_token_encrypted)
        refresh_token = decrypt_token(token.refresh_token_encrypted)

    return YahooClient(access_token, refresh_token, commissioner.id)


def _resolve_league_key() -> str:
    league_id = settings.yahoo_league_id
    if ".l." in league_id:
        return league_id
    return f"mlb.l.{league_id}"


async def run_sync_league_meta():
    try:
        client = await _get_yahoo_client()
        league_key = _resolve_league_key()
        meta = await client.get_league_meta(league_key)
        standings = await client.get_standings(league_key)
        await client.close()

        async with async_session_factory() as session:
            result = await session.execute(select(League).limit(1))
            league = result.scalar_one_or_none()

            if league and league.yahoo_league_key != meta.league_key:
                logger.error(
                    "Database contains league %s, but config is for %s. Nuke DB to switch.",
                    league.yahoo_league_key,
                    meta.league_key,
                )
                return

            if league is None:
                league = League(
                    yahoo_league_key=meta.league_key,
                    name=meta.name,
                    season=meta.season,
                    num_teams=meta.num_teams,
                    scoring_type=meta.scoring_type,
                    current_week=meta.current_week,
                    start_week=meta.start_week,
                    end_week=meta.end_week,
                    stat_categories=meta.stat_categories,
                    roster_positions=meta.roster_positions,
                )
                session.add(league)
                await session.flush()
            else:
                if league.num_teams != meta.num_teams and league.num_teams != 0:
                    notif = CommissionerNotification(
                        type="sync_failure",
                        message=(
                            f"Yahoo num_teams ({meta.num_teams}) differs from DB ({league.num_teams}). Sync aborted."
                        ),
                    )
                    session.add(notif)
                    await session.commit()
                    return

                league.name = meta.name
                league.num_teams = meta.num_teams
                league.scoring_type = meta.scoring_type
                league.current_week = meta.current_week
                league.start_week = meta.start_week
                league.end_week = meta.end_week
                league.stat_categories = meta.stat_categories
                league.roster_positions = meta.roster_positions

            for team_data in standings:
                team_result = await session.execute(select(Team).where(Team.yahoo_team_key == team_data.team_key))
                team = team_result.scalar_one_or_none()

                manager_user = None
                if team_data.manager_guid:
                    user_result = await session.execute(select(User).where(User.yahoo_guid == team_data.manager_guid))
                    manager_user = user_result.scalar_one_or_none()
                    if manager_user and team_data.manager_name and manager_user.display_name != team_data.manager_name:
                        manager_user.display_name = team_data.manager_name

                if team is None:
                    team = Team(
                        league_id=league.id,
                        yahoo_team_key=team_data.team_key,
                        yahoo_manager_guid=team_data.manager_guid,
                        name=team_data.name,
                        manager_user_id=manager_user.id if manager_user else None,
                        logo_url=team_data.logo_url,
                        wins=team_data.wins,
                        losses=team_data.losses,
                        ties=team_data.ties,
                        standing=team_data.standing,
                    )
                    session.add(team)
                else:
                    team.name = team_data.name
                    team.logo_url = team_data.logo_url
                    team.wins = team_data.wins
                    team.losses = team_data.losses
                    team.ties = team_data.ties
                    team.standing = team_data.standing
                    team.yahoo_manager_guid = team_data.manager_guid
                    if manager_user:
                        team.manager_user_id = manager_user.id

            await session.commit()
            await invalidate_cache("league:standings", "league:info")
            logger.info("League meta sync complete: %s", league.name)

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"League meta sync complete: {league.name} "
                    f"(season {league.season}, week {league.current_week}, "
                    f"{league.num_teams} teams)"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_league_meta failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"League meta sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise


async def run_sync_matchups():
    """Sync matchups for all weeks (current + historical).

    On each run:
      - Iterates weeks from start_week to current_week.
      - Skips weeks where ALL existing matchups are already marked is_complete
        (no need to re-fetch finalized weeks from Yahoo).
      - Always syncs the current week (live scores).
      - New weeks (no DB rows yet) are always fetched.
    """
    try:
        client = await _get_yahoo_client()
        league_key = _resolve_league_key()

        async with async_session_factory() as session:
            result = await session.execute(select(League).limit(1))
            league = result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping matchup sync")
                return

            current_week = league.current_week or 1
            start_week = league.start_week or 1
            weeks_synced = 0

            for week in range(start_week, current_week + 1):
                if week < current_week:
                    existing_result = await session.execute(
                        select(Matchup).where(
                            Matchup.league_id == league.id,
                            Matchup.week == week,
                        )
                    )
                    existing_matchups = existing_result.scalars().all()
                    if existing_matchups and all(m.is_complete for m in existing_matchups):
                        # Week is finalized, skip re-fetch
                        continue

                try:
                    matchups_data = await client.get_matchups(league_key, week)
                except Exception as e:
                    logger.warning("Failed to fetch matchups for week %d: %s", week, e)
                    continue

                for md in matchups_data:
                    team_a_result = await session.execute(select(Team).where(Team.yahoo_team_key == md.team_a_key))
                    team_a = team_a_result.scalar_one_or_none()

                    team_b_result = await session.execute(select(Team).where(Team.yahoo_team_key == md.team_b_key))
                    team_b = team_b_result.scalar_one_or_none()

                    if not team_a or not team_b:
                        continue

                    existing = await session.execute(
                        select(Matchup).where(
                            Matchup.league_id == league.id,
                            Matchup.week == md.week,
                            Matchup.team_a_id == team_a.id,
                            Matchup.team_b_id == team_b.id,
                        )
                    )
                    matchup = existing.scalar_one_or_none()

                    if matchup is None:
                        matchup = Matchup(
                            league_id=league.id,
                            week=md.week,
                            team_a_id=team_a.id,
                            team_b_id=team_b.id,
                            team_a_stats=md.team_a_stats,
                            team_b_stats=md.team_b_stats,
                            is_complete=md.is_complete,
                        )
                        session.add(matchup)
                    else:
                        matchup.team_a_stats = md.team_a_stats
                        matchup.team_b_stats = md.team_b_stats
                        matchup.is_complete = md.is_complete

                weeks_synced += 1
                await invalidate_cache(f"league:matchups:{week}")

            await session.commit()
            logger.info(
                "Matchup sync complete: %d weeks synced (weeks %d-%d)",
                weeks_synced,
                start_week,
                current_week,
            )

            notif = CommissionerNotification(
                type="info",
                message=(f"Matchup sync complete: {weeks_synced} weeks synced (weeks {start_week}-{current_week})"),
            )
            session.add(notif)
            await session.commit()

        await client.close()

    except Exception as e:
        logger.error("sync_matchups failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Matchup sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
