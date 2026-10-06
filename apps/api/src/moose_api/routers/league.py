from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.core.database import get_db
from moose_api.core.redis import get_cached, set_cached
from moose_api.core.security import get_current_user
from moose_api.models.league import League
from moose_api.models.matchup import Matchup
from moose_api.models.team import Team
from moose_api.schemas.league import (
    LeagueResponse,
    MatchupResponse,
    StandingsEntry,
    StandingsResponse,
    WeekMatchupsResponse,
)

router = APIRouter(prefix="/league", tags=["league"])

STANDINGS_TTL = 30 * 60
MATCHUPS_TTL = 15 * 60  # live score updates
LEAGUE_INFO_TTL = 24 * 60 * 60


@router.get("/standings", response_model=StandingsResponse)
async def get_standings(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the current league standings, cached for 30 minutes.

    Returns an ordered list of teams based on their current rank, including
    win/loss records and team metadata.
    """
    cache_key = "league:standings"
    cached = await get_cached(cache_key)
    if cached:
        return StandingsResponse(**cached)

    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    teams_result = await db.execute(
        select(Team).where(Team.league_id == league.id).order_by(Team.standing.asc().nullslast())
    )
    teams = teams_result.scalars().all()

    response = StandingsResponse(
        league_name=league.name,
        season=league.season,
        current_week=league.current_week,
        standings=[
            StandingsEntry(
                team_id=t.id,
                team_name=t.name,
                logo_url=t.logo_url,
                wins=t.wins,
                losses=t.losses,
                ties=t.ties,
                standing=t.standing,
            )
            for t in teams
        ],
        generated_at=datetime.now(UTC),
    )
    await set_cached(cache_key, response.model_dump(), STANDINGS_TTL)
    return response


@router.get("/matchups", response_model=WeekMatchupsResponse)
async def get_matchups(
    week: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve head-to-head matchup results for a specific week.

    If no week is provided, defaults to the current active week. Cached for
    15 minutes to reflect live score updates during games.
    """
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    target_week = week or league.current_week or 1
    cache_key = f"league:matchups:{target_week}"
    cached = await get_cached(cache_key)
    if cached:
        return WeekMatchupsResponse(**cached)

    matchups_result = await db.execute(
        select(Matchup).where(
            Matchup.league_id == league.id,
            Matchup.week == target_week,
        )
    )
    matchups = matchups_result.scalars().all()

    team_ids = set()
    for m in matchups:
        team_ids.add(m.team_a_id)
        team_ids.add(m.team_b_id)

    teams_result = await db.execute(select(Team).where(Team.id.in_(team_ids)))
    teams_map = {t.id: t for t in teams_result.scalars().all()}

    response = WeekMatchupsResponse(
        week=target_week,
        matchups=[
            MatchupResponse(
                id=m.id,
                week=m.week,
                team_a_name=teams_map[m.team_a_id].name if m.team_a_id in teams_map else "Unknown",
                team_a_logo=teams_map[m.team_a_id].logo_url if m.team_a_id in teams_map else None,
                team_b_name=teams_map[m.team_b_id].name if m.team_b_id in teams_map else "Unknown",
                team_b_logo=teams_map[m.team_b_id].logo_url if m.team_b_id in teams_map else None,
                team_a_stats=m.team_a_stats or {},
                team_b_stats=m.team_b_stats or {},
                category_results=m.category_results or {},
                team_a_wins=m.team_a_wins,
                team_b_wins=m.team_b_wins,
                ties=m.ties,
                is_complete=m.is_complete,
            )
            for m in matchups
        ],
        generated_at=datetime.now(UTC),
    )
    await set_cached(cache_key, response.model_dump(), MATCHUPS_TTL)
    return response


@router.get("/info", response_model=LeagueResponse)
async def get_league_info(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve core league configuration and metadata.

    Includes scoring types, stat categories, roster positions, and
    season schedule limits. Cached globally for 24 hours.
    """
    cache_key = "league:info"
    cached = await get_cached(cache_key)
    if cached:
        return LeagueResponse(**cached)

    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    response = LeagueResponse(
        id=league.id,
        yahoo_league_key=league.yahoo_league_key,
        name=league.name,
        season=league.season,
        num_teams=league.num_teams,
        scoring_type=league.scoring_type,
        current_week=league.current_week,
        start_week=league.start_week,
        end_week=league.end_week,
        stat_categories=league.stat_categories or [],
        roster_positions=league.roster_positions or [],
        updated_at=league.updated_at,
    )
    await set_cached(cache_key, response.model_dump(), LEAGUE_INFO_TTL)
    return response
