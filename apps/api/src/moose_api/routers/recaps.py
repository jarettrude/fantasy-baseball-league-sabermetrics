from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.core.database import get_db
from moose_api.core.rendering import render_markdown
from moose_api.core.security import get_current_user, require_commissioner
from moose_api.models.league import League
from moose_api.models.recap import Recap
from moose_api.models.team import Team
from moose_api.schemas.recap import (
    RecapEditRequest,
    RecapListResponse,
    RecapResponse,
)

router = APIRouter(prefix="/recaps", tags=["recaps"])


@router.get("/history", response_model=list[int])
async def get_recap_history(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Returns a unique list of weeks that have published recaps, newest first."""
    result = await db.execute(
        select(Recap.week).where(Recap.status == "published").distinct().order_by(Recap.week.desc())
    )
    return list(result.scalars().all())


async def _recap_to_response(recap: Recap, db: AsyncSession) -> RecapResponse:
    """Converts a Recap database model into a RecapResponse schema.

    Resolves the associated team name if a team_id is present and safely
    compiles formatting markdown into sanitized HTML content.
    """
    team_name = None
    if recap.team_id:
        team_result = await db.execute(select(Team).where(Team.id == recap.team_id))
        team = team_result.scalar_one_or_none()
        team_name = team.name if team else None

    return RecapResponse(
        id=recap.id,
        week=recap.week,
        type=recap.type,
        team_id=recap.team_id,
        team_name=team_name,
        status=recap.status,
        content=render_markdown(recap.content),
        model_used=recap.model_used,
        provider_used=recap.provider_used,
        tokens_used=recap.tokens_used,
        cost_usd=recap.cost_usd,
        published_at=recap.published_at,
        created_at=recap.created_at,
    )


@router.get("/week/{week}", response_model=RecapListResponse)
async def get_week_recaps(
    week: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves published recaps for a specified week - league recap + user's own recap."""
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    team_result = await db.execute(select(Team).where(Team.manager_user_id == current_user["id"]))
    user_team = team_result.scalar_one_or_none()

    if user_team:
        type_filter = or_(Recap.type == "league", and_(Recap.type == "manager", Recap.team_id == user_team.id))
    else:
        type_filter = Recap.type == "league"

    result = await db.execute(
        select(Recap).where(
            Recap.league_id == league.id,
            Recap.week == week,
            Recap.status == "published",
            type_filter,
        )
    )
    recaps = result.scalars().all()

    return RecapListResponse(
        recaps=[await _recap_to_response(r, db) for r in recaps],
        week=week,
    )


@router.get("/my-recap", response_model=RecapResponse | None)
async def get_my_recap(
    week: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves the personalized manager recap for the authenticated user for a given week."""
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    target_week = week or league.current_week or 1

    team_result = await db.execute(select(Team).where(Team.manager_user_id == current_user["id"]))
    team = team_result.scalar_one_or_none()
    if not team:
        return None

    result = await db.execute(
        select(Recap).where(
            Recap.league_id == league.id,
            Recap.week == target_week,
            Recap.type == "manager",
            Recap.team_id == team.id,
            Recap.status == "published",
        )
    )
    recap = result.scalar_one_or_none()
    if not recap:
        return None

    return await _recap_to_response(recap, db)


@router.put("/{recap_id}", response_model=RecapResponse)
async def edit_recap(
    recap_id: int,
    req: RecapEditRequest,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Updates the content of an individual recap. Requires commissioner privileges."""
    result = await db.execute(select(Recap).where(Recap.id == recap_id))
    recap = result.scalar_one_or_none()
    if not recap:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recap not found")

    recap.content = req.content
    await db.commit()
    return await _recap_to_response(recap, db)
