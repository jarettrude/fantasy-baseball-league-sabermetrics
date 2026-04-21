from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from moose_api.core.database import get_db
from moose_api.core.security import get_current_user, require_commissioner
from moose_api.models.free_agent import FreeAgentSnapshot
from moose_api.models.league import League
from moose_api.models.player import Player, PlayerMapping
from moose_api.models.roster import RosterSlot
from moose_api.models.stats import PlayerValueSnapshot
from moose_api.models.team import Team
from moose_api.schemas.player import (
    BenchRecommendationsResponse,
    BenchResponse,
    DropCandidateResponse,
    FreeAgentListResponse,
    FreeAgentResponse,
    PlayerMappingResponse,
    PlayerMappingUpdateRequest,
    PlayerResponse,
    PlayerValueResponse,
    PositionUpgradeResponse,
    RosterSlotResponse,
    ValuedPlayerResponse,
)
from moose_api.services.roster_optimizer import (
    DropCandidate,
    PositionUpgrade,
    ValuedPlayer,
    build_recommendations,
)

router = APIRouter(prefix="/players", tags=["players"])


async def _latest_values_for_players(
    db: AsyncSession, player_ids: list[int]
) -> tuple[dict[int, PlayerValueSnapshot], dict[int, PlayerValueSnapshot]]:
    """Return the latest season and next_games snapshots for a set of players.

    Performs a single query across both snapshot types and folds the rows
    into two ``player_id -> snapshot`` dicts, keeping the newest row per
    ``(player_id, type)``. Used instead of per-player lookups to avoid
    N+1 query fan-out on roster/free-agent pages.

    Args:
        db: Active async session.
        player_ids: Players whose latest snapshots are needed. Empty input
            short-circuits to two empty dicts.

    Returns:
        ``(season_by_player_id, next_games_by_player_id)``.
    """
    season_vals: dict[int, PlayerValueSnapshot] = {}
    next_vals: dict[int, PlayerValueSnapshot] = {}
    if not player_ids:
        return season_vals, next_vals

    result = await db.execute(
        select(PlayerValueSnapshot)
        .where(PlayerValueSnapshot.player_id.in_(player_ids))
        .order_by(PlayerValueSnapshot.snapshot_date.desc())
    )
    for snap in result.scalars():
        bucket = season_vals if snap.type == "season" else next_vals if snap.type == "next_games" else None
        if bucket is not None and snap.player_id not in bucket:
            bucket[snap.player_id] = snap

    return season_vals, next_vals


def _player_to_response(p: Player) -> PlayerResponse:
    """Convert a Player database model instance into a standard API response schema.

    Args:
        p: Player model instance

    Returns:
        PlayerResponse with relevant player data
    """
    return PlayerResponse(
        id=p.id,
        yahoo_player_key=p.yahoo_player_key,
        name=p.name,
        primary_position=p.primary_position,
        eligible_positions=p.eligible_positions or [],
        team_abbr=p.team_abbr,
        is_pitcher=p.is_pitcher,
        yahoo_rank=p.yahoo_rank,
        injury_status=p.injury_status,
        injury_note=p.injury_note,
    )


def _snapshot_to_response(snap: PlayerValueSnapshot | None, player: Player) -> PlayerValueResponse | None:
    """Safely map a PlayerValueSnapshot model to the client response object schema.

    Includes safe fallbacks for missing snapshot instances (valueless players).

    Args:
        snap: PlayerValueSnapshot instance or None
        player: Player model instance for fallback data

    Returns:
        PlayerValueResponse or None if snapshot is missing
    """
    if not snap:
        return None
    return PlayerValueResponse(
        player_id=player.id,
        player_name=player.name,
        primary_position=player.primary_position,
        team_abbr=player.team_abbr,
        snapshot_date=snap.snapshot_date,
        type=snap.type,
        category_scores=snap.category_scores or {},
        composite_value=snap.composite_value,
        yahoo_rank=snap.yahoo_rank,
        our_rank=snap.our_rank,
        injury_weight=snap.injury_weight,
        injury_status=player.injury_status,
        xwoba=snap.xwoba,
        xera=snap.xera,
        roster_percent=float(snap.roster_percent) if snap.roster_percent is not None else None,
        roster_trend=float(snap.roster_trend) if snap.roster_trend is not None else None,
    )


@router.get("/bench", response_model=BenchResponse)
async def get_bench(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the authenticating manager's active roster and associated player values.

    Returns all active roster slots for the current week with season and next-games
    value projections. Includes the most recent manager briefing for the team.

    Args:
        current_user: Authenticated user from JWT token
        db: Database session

    Returns:
        BenchResponse with roster, values, and briefing

    Raises:
        HTTPException: If team not found for user
    """
    team_result = await db.execute(select(Team).where(Team.manager_user_id == current_user["id"]))
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    current_week = league.current_week if league and league.current_week is not None else 1

    roster_result = await db.execute(
        select(RosterSlot, Player)
        .join(Player, Player.id == RosterSlot.player_id)
        .where(
            RosterSlot.team_id == team.id,
            RosterSlot.week == current_week,
            RosterSlot.is_active.is_(True),
        )
    )
    roster_rows = roster_result.all()

    player_ids = [player.id for _, player in roster_rows]
    season_vals, next_vals = await _latest_values_for_players(db, player_ids)

    roster_responses = [
        RosterSlotResponse(
            player=_player_to_response(player),
            position=slot.position,
            season_value=_snapshot_to_response(season_vals.get(player.id), player),
            next_games_value=_snapshot_to_response(next_vals.get(player.id), player),
        )
        for slot, player in roster_rows
    ]

    max_season_res = await db.execute(
        select(func.max(PlayerValueSnapshot.snapshot_date)).where(PlayerValueSnapshot.type == "season")
    )
    max_season_date = max_season_res.scalar_one_or_none()

    max_next_res = await db.execute(
        select(func.max(PlayerValueSnapshot.snapshot_date)).where(PlayerValueSnapshot.type == "next_games")
    )
    max_next_date = max_next_res.scalar_one_or_none()

    from moose_api.models.manager_briefing import ManagerBriefing

    briefing_result = await db.execute(
        select(ManagerBriefing).where(ManagerBriefing.team_id == team.id).order_by(ManagerBriefing.date.desc()).limit(1)
    )
    briefing = briefing_result.scalar_one_or_none()
    briefing_resp = None
    if briefing:
        from moose_api.core.rendering import render_markdown
        from moose_api.schemas.player import ManagerBriefingResponse

        briefing_resp = ManagerBriefingResponse(
            id=briefing.id,
            date=briefing.date,
            content=render_markdown(briefing.content),
            is_viewed=briefing.is_viewed,
        )

    return BenchResponse(
        team_name=team.name,
        roster=roster_responses,
        season_value_updated=max_season_date.isoformat() if max_season_date else None,
        next_games_value_updated=max_next_date.isoformat() if max_next_date else None,
        briefing=briefing_resp,
    )


@router.post("/briefings/read")
async def mark_briefings_read(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all unread briefings for the authenticated manager's team as read.

    Args:
        current_user: Authenticated user from JWT token
        db: Database session

    Returns:
        Status confirmation
    """
    team_result = await db.execute(select(Team).where(Team.manager_user_id == current_user["id"]))
    team = team_result.scalar_one_or_none()
    if not team:
        return {"status": "ok"}
    from sqlalchemy import update

    from moose_api.models.manager_briefing import ManagerBriefing

    await db.execute(
        update(ManagerBriefing)
        .where(ManagerBriefing.team_id == team.id, ManagerBriefing.is_viewed.is_(False))
        .values(is_viewed=True)
    )
    await db.commit()
    return {"status": "ok"}


def _valued_to_schema(vp: ValuedPlayer | None) -> ValuedPlayerResponse | None:
    """Convert a ``ValuedPlayer`` dataclass to its wire schema."""
    if vp is None:
        return None
    return ValuedPlayerResponse(
        id=vp.player.id,
        name=vp.player.name,
        primary_position=vp.player.primary_position,
        eligible_positions=list(vp.player.eligible_positions),
        team_abbr=vp.player.team_abbr,
        is_pitcher=vp.player.is_pitcher,
        injury_status=vp.player.injury_status,
        composite_value=vp.composite_value,
        next_games_value=vp.next_games_value,
        our_rank=vp.our_rank,
        yahoo_rank=vp.yahoo_rank,
        roster_slot=vp.roster_slot,
    )


def _drop_to_schema(drop: DropCandidate) -> DropCandidateResponse:
    return DropCandidateResponse(
        player=_valued_to_schema(drop.player),
        reason=drop.reason,
        position=drop.position,
        replacement=_valued_to_schema(drop.replacement),
        delta=drop.delta,
    )


def _upgrade_to_schema(up: PositionUpgrade) -> PositionUpgradeResponse:
    return PositionUpgradeResponse(
        position=up.position,
        incumbent=_valued_to_schema(up.incumbent),
        top_free_agents=[_valued_to_schema(vp) for vp in up.top_free_agents],
        delta=up.delta,
        recommend=up.recommend,
    )


@router.get("/bench/recommendations", response_model=BenchRecommendationsResponse)
async def get_bench_recommendations(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return position-aligned drop and pickup recommendations.

    Delegates to ``services.roster_optimizer.build_recommendations`` so
    the payload matches the structure consumed by the daily manager
    briefing LLM prompt. This is the single source of truth for roster
    optimization suggestions across the product.
    """
    team_result = await db.execute(select(Team).where(Team.manager_user_id == current_user["id"]))
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    current_week = league.current_week if league and league.current_week is not None else 1

    rec = await build_recommendations(db, team, league, current_week)

    return BenchRecommendationsResponse(
        team_id=rec.team_id,
        team_name=rec.team_name,
        roster=[_valued_to_schema(vp) for vp in rec.roster],
        drop_candidates=[_drop_to_schema(d) for d in rec.drop_candidates],
        upgrades_by_position={pos: _upgrade_to_schema(up) for pos, up in rec.upgrades_by_position.items()},
        top_fa_overall=[_valued_to_schema(vp) for vp in rec.top_fa_overall],
    )


@router.get("/free-agents", response_model=FreeAgentListResponse)
async def get_free_agents(
    position: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the list of recently polled free agents.

    Returns free agents from the most recent system snapshot array, ordered
    descending by their overall seasonal fantasy value score.
    """
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    subq = (
        select(FreeAgentSnapshot.player_id, func.max(FreeAgentSnapshot.snapshot_at).label("max_snap"))
        .where(FreeAgentSnapshot.league_id == league.id)
        .group_by(FreeAgentSnapshot.player_id)
        .subquery()
    )

    query = (
        select(FreeAgentSnapshot, Player)
        .join(
            subq,
            (FreeAgentSnapshot.player_id == subq.c.player_id) & (FreeAgentSnapshot.snapshot_at == subq.c.max_snap),
        )
        .join(Player, Player.id == FreeAgentSnapshot.player_id)
        .where(FreeAgentSnapshot.is_available.is_(True))
        .order_by(FreeAgentSnapshot.snapshot_at.desc())
    )
    result = await db.execute(query)
    rows = result.all()

    # Filter by position
    if position:
        pos_upper = position.upper()
        # manual filter on the python array since `any` is pg specific
        rows = [r for r in rows if r.Player.eligible_positions and pos_upper in r.Player.eligible_positions]

    if not rows:
        return FreeAgentListResponse(free_agents=[], snapshot_at=None)

    player_ids = [r.Player.id for r in rows]
    season_vals, next_vals = await _latest_values_for_players(db, player_ids)

    fa_responses = []
    for _snap, player in rows:
        season_val = season_vals.get(player.id)
        next_val = next_vals.get(player.id)

        fa_responses.append(
            FreeAgentResponse(
                player=_player_to_response(player),
                season_value=_snapshot_to_response(season_val, player),
                next_games_value=_snapshot_to_response(next_val, player),
                is_available=True,
            )
        )

    return FreeAgentListResponse(
        free_agents=fa_responses,
        snapshot_at=rows[0][0].snapshot_at if rows else None,
    )


@router.get("/mappings", response_model=list[PlayerMappingResponse])
async def get_mappings(
    status_filter: str | None = Query(None, alias="status"),
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """List internal player mapping records linking Yahoo IDs with MLB/Lahman IDs.

    Requires commissioner access. Supports optional status filtering to focus
    on unconfirmed or problematic mappings.

    Args:
        status_filter: Optional status filter (e.g., 'pending', 'confirmed')
        current_user: Authenticated commissioner user
        db: Database session

    Returns:
        List of player mappings
    """
    query = select(PlayerMapping)
    if status_filter:
        query = query.where(PlayerMapping.status == status_filter)
    result = await db.execute(query.order_by(PlayerMapping.updated_at.desc()).limit(500))
    mappings = result.scalars().all()

    responses = []
    for m in mappings:
        player_result = await db.execute(select(Player).where(Player.yahoo_player_key == m.yahoo_player_key))
        player = player_result.scalar_one_or_none()
        responses.append(
            PlayerMappingResponse(
                id=m.id,
                yahoo_player_key=m.yahoo_player_key,
                player_name=player.name if player else "Unknown",
                mlb_id=m.mlb_id,
                lahman_id=m.lahman_id,
                source_confidence=m.source_confidence,
                auto_mapped=m.auto_mapped,
                status=m.status,
                notes=m.notes,
            )
        )
    return responses


@router.put("/mappings/{mapping_id}", response_model=PlayerMappingResponse)
async def update_mapping(
    mapping_id: int,
    req: PlayerMappingUpdateRequest,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Update a specific player mapping. Typically used by the commissioner to resolve ambiguity.

    Allows manual correction of automatic mappings or confirmation of pending mappings.
    Marks the mapping as manually edited (auto_mapped=False).

    Args:
        mapping_id: ID of the mapping to update
        req: Update request with new MLB/Lahman IDs and status
        current_user: Authenticated user (must be commissioner)
        db: Database session

    Returns:
        Updated player mapping

    Raises:
        HTTPException: If user is not commissioner or mapping not found
    """
    result = await db.execute(select(PlayerMapping).where(PlayerMapping.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")

    if req.mlb_id is not None:
        mapping.mlb_id = req.mlb_id
    if req.lahman_id is not None:
        mapping.lahman_id = req.lahman_id
    mapping.status = req.status
    mapping.notes = req.notes
    mapping.auto_mapped = False

    player_result = await db.execute(select(Player).where(Player.yahoo_player_key == mapping.yahoo_player_key))
    player = player_result.scalar_one_or_none()

    return PlayerMappingResponse(
        id=mapping.id,
        yahoo_player_key=mapping.yahoo_player_key,
        player_name=player.name if player else "Unknown",
        mlb_id=mapping.mlb_id,
        lahman_id=mapping.lahman_id,
        source_confidence=mapping.source_confidence,
        auto_mapped=mapping.auto_mapped,
        status=mapping.status,
        notes=mapping.notes,
    )
