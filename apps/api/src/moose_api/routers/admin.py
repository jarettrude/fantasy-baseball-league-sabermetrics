from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.core.database import get_db
from moose_api.core.rate_limit import admin_sync_rate_limit, recap_rate_limit
from moose_api.core.rendering import render_markdown
from moose_api.core.security import require_commissioner
from moose_api.models.draft import DraftPick, DraftSummary
from moose_api.models.league import League
from moose_api.models.manager_briefing import ManagerBriefing
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.recap import Recap
from moose_api.models.team import Team
from moose_api.models.user import User
from moose_api.schemas.admin import (
    AdminBriefingResponse,
    AdminOverviewResponse,
    AISettingsResponse,
    AISettingsUpdateRequest,
    DraftPickResponse,
    DraftSummaryResponse,
    JobStatusResponse,
    NotificationListResponse,
    NotificationResponse,
    SyncTriggerRequest,
    SyncTriggerResponse,
    TeamDraftResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

VALID_JOBS = [
    "sync_league_meta",
    "sync_matchups",
    "sync_roster",
    "sync_roster_trends",
    "sync_free_agents",
    "sync_rotowire_injuries",
    "sync_injury_status",
    "recompute_season_values",
    "recompute_next_games_values",
    "sync_advanced_metrics_job",
    "generate_weekly_recaps",
    "purge_session_logs",
    "purge_ai_prompt_raw",
    "purge_free_agent_snapshots",
    "load_mlb_roster_data",
    "resolve_player_mappings",
    "load_live_season_stats",
    "run_preseason_setup_job",
    "run_force_preseason_setup_job",
    "run_daily_sync_job",
    "generate_briefings",
    "generate_briefings_force",
    "generate_draft_summary",
    "generate_draft_summary_force",
]


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Compile a dashboard summary for the commissioner.

    Includes unread notification counts, current background job queue statuses,
    and aggregate ecosystem metrics (users, teams, players, recaps).
    """
    unread_result = await db.execute(
        select(func.count(CommissionerNotification.id)).where(CommissionerNotification.is_read.is_(False))
    )
    unread_count = unread_result.scalar() or 0

    from moose_api.core.redis import redis_client
    from moose_api.worker import JOB_STATUS_KEY_PREFIX, JOB_STOP_KEY

    job_statuses = []
    job_stop_active = False
    job_stop_since: datetime | None = None
    for job_name in VALID_JOBS:
        redis_key = f"{JOB_STATUS_KEY_PREFIX}{job_name}"
        try:
            job_data = await redis_client.hgetall(redis_key)
        except Exception:
            job_data = {}

        redis_status = job_data.get("status", "idle")
        started_at_str = job_data.get("started_at", "")
        completed_at_str = job_data.get("completed_at", "")
        elapsed_str = job_data.get("elapsed_seconds", "")
        error_str = job_data.get("error", "")

        started_at = None
        completed_at = None
        elapsed_seconds = None

        if started_at_str:
            with contextlib.suppress(ValueError):
                started_at = datetime.fromisoformat(started_at_str)

        if completed_at_str:
            with contextlib.suppress(ValueError):
                completed_at = datetime.fromisoformat(completed_at_str)

        if elapsed_str:
            with contextlib.suppress(ValueError):
                elapsed_seconds = float(elapsed_str)

        if redis_status == "processing" and started_at:
            elapsed_seconds = round((datetime.now(UTC) - started_at).total_seconds(), 1)

        job_statuses.append(
            JobStatusResponse(
                job_name=job_name,
                last_success=completed_at if redis_status == "success" else None,
                last_failure=completed_at if redis_status == "failed" else None,
                status=redis_status,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_seconds=elapsed_seconds,
                error=error_str or None,
            )
        )

    try:
        stop_value = await redis_client.get(JOB_STOP_KEY)
        if stop_value:
            job_stop_active = True
            with contextlib.suppress(ValueError):
                job_stop_since = datetime.fromisoformat(stop_value)
    except Exception as e:
        logger.error("Failed to read job stop flag: %s", e)

    user_count_result = await db.execute(select(func.count(User.id)))
    team_count_result = await db.execute(select(func.count(Team.id)))
    player_count_result = await db.execute(select(func.count(Player.id)))
    recap_count_result = await db.execute(select(func.count(Recap.id)))

    return AdminOverviewResponse(
        unread_notifications=unread_count,
        job_statuses=job_statuses,
        user_count=user_count_result.scalar() or 0,
        team_count=team_count_result.scalar() or 0,
        player_count=player_count_result.scalar() or 0,
        recap_count=recap_count_result.scalar() or 0,
        job_stop_active=job_stop_active,
        job_stop_since=job_stop_since,
    )


@router.get("/notifications", response_model=NotificationListResponse)
async def get_notifications(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the 100 most recent system notifications for the commissioner."""
    result = await db.execute(
        select(CommissionerNotification).order_by(CommissionerNotification.created_at.desc()).limit(200)
    )
    notifications = result.scalars().all()
    unread_count = sum(1 for n in notifications if not n.is_read)

    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                type=n.type,
                message=n.message,
                metadata=n.metadata_ or {},
                is_read=n.is_read,
                created_at=n.created_at,
            )
            for n in notifications
        ],
        unread_count=unread_count,
    )


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Mark a specific notification as 'read'."""
    result = await db.execute(select(CommissionerNotification).where(CommissionerNotification.id == notification_id))
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    notification.is_read = True
    await db.commit()
    return {"status": "ok"}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Bulk mark all unread system notifications as 'read'."""
    result = await db.execute(select(CommissionerNotification).where(CommissionerNotification.is_read.is_(False)))
    for notif in result.scalars().all():
        notif.is_read = True
    await db.commit()
    return {"status": "ok"}


@router.post("/refresh-job-status", response_model=dict)
async def refresh_job_status(
    current_user: dict = Depends(require_commissioner),
):
    """Force refresh job status cache - useful when status gets stuck."""
    from moose_api.core.redis import redis_client
    from moose_api.worker import JOB_STATUS_KEY_PREFIX

    refreshed_jobs = []

    for job_name in ["resolve_player_mappings", "run_preseason_setup_job"]:
        redis_key = f"{JOB_STATUS_KEY_PREFIX}{job_name}"
        try:
            job_data = await redis_client.hgetall(redis_key)
            status = job_data.get("status", "idle")
            started_at_str = job_data.get("started_at", "")

            if status == "processing" and started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=UTC)
                    elapsed = (datetime.now(UTC) - started_at).total_seconds()

                    if elapsed > 1800:
                        if job_name == "resolve_player_mappings":
                            from moose_api.core.database import async_session_factory
                            from moose_api.models.player import PlayerMapping

                            async with async_session_factory() as db:
                                count_result = await db.execute(select(func.count(PlayerMapping.id)))
                                mapping_count = count_result.scalar() or 0

                                if mapping_count > 0:
                                    await redis_client.hmset(
                                        redis_key,
                                        {
                                            "status": "success",
                                            "completed_at": datetime.now(UTC).isoformat(),
                                            "elapsed_seconds": str(elapsed),
                                            "error": "",
                                        },
                                    )
                                    refreshed_jobs.append(f"{job_name}: marked as success ({mapping_count} mappings)")
                                else:
                                    await redis_client.hmset(
                                        redis_key,
                                        {
                                            "status": "failed",
                                            "completed_at": datetime.now(UTC).isoformat(),
                                            "elapsed_seconds": str(elapsed),
                                            "error": "Job timed out",
                                        },
                                    )
                                    refreshed_jobs.append(f"{job_name}: marked as failed")

                        elif job_name == "run_preseason_setup_job":
                            from moose_api.tasks.preseason_setup import check_setup_status

                            status_obj = await check_setup_status()
                            if status_obj.is_complete:
                                await redis_client.hmset(
                                    redis_key,
                                    {
                                        "status": "success",
                                        "completed_at": datetime.now(UTC).isoformat(),
                                        "elapsed_seconds": str(elapsed),
                                        "error": "",
                                    },
                                )
                                refreshed_jobs.append(f"{job_name}: marked as success (setup complete)")
                            else:
                                await redis_client.hmset(
                                    redis_key,
                                    {
                                        "status": "failed",
                                        "completed_at": datetime.now(UTC).isoformat(),
                                        "elapsed_seconds": str(elapsed),
                                        "error": f"Setup incomplete: {status_obj.next_step}",
                                    },
                                )
                                refreshed_jobs.append(f"{job_name}: marked as failed ({status_obj.next_step})")

                except Exception as e:
                    logger.error(f"Error checking job {job_name}: {e}")

        except Exception as e:
            logger.error(f"Error refreshing job {job_name}: {e}")

    return {"refreshed_jobs": refreshed_jobs}


@router.post("/jobs/stop", response_model=dict)
async def stop_all_jobs(
    current_user: dict = Depends(require_commissioner),
):
    """Set a Redis kill switch that interrupts all running jobs."""
    from moose_api.core.redis import redis_client
    from moose_api.worker import JOB_STOP_KEY

    issued_at = datetime.now(UTC).isoformat()

    try:
        await redis_client.set(JOB_STOP_KEY, issued_at)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to enable job stop flag: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to stop jobs") from exc

    logger.warning("Commissioner %s enabled HARD STOP at %s", current_user.get("email"), issued_at)
    return {"status": "stopped", "since": issued_at}


@router.post("/jobs/resume", response_model=dict)
async def resume_all_jobs(
    current_user: dict = Depends(require_commissioner),
):
    """Clear the Redis kill switch so workers can run again."""
    from moose_api.core.redis import redis_client
    from moose_api.worker import JOB_STOP_KEY

    try:
        await redis_client.delete(JOB_STOP_KEY)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to clear job stop flag: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to resume jobs") from exc

    logger.warning("Commissioner %s cleared HARD STOP", current_user.get("email"))
    return {"status": "resumed"}


@router.post(
    "/sync",
    response_model=SyncTriggerResponse,
    dependencies=[Depends(admin_sync_rate_limit)],
)
async def trigger_sync(
    req: SyncTriggerRequest,
    current_user: dict = Depends(require_commissioner),
):
    """Enqueue a designated background job immediately.

    Enforces rate limits to prevent spamming costly upstream APIs. Returns
    the queued job ID for tracking.
    """
    if req.job_name not in VALID_JOBS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown job: {req.job_name}. Valid: {VALID_JOBS}",
        )

    from arq.connections import create_pool

    from moose_api.worker import WorkerSettings

    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        job = await pool.enqueue_job(req.job_name)
    finally:
        await pool.close()

    return SyncTriggerResponse(
        job_name=req.job_name,
        status="queued",
        message=f"Job {req.job_name} has been queued (job_id={job.job_id})",
    )


# ---- AI Settings ----


def _read_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_prompt(filename: str, content: str) -> None:
    path = PROMPTS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@router.get("/ai/settings", response_model=AISettingsResponse)
async def get_ai_settings(
    current_user: dict = Depends(require_commissioner),
):
    """Retrieve the current configurations and raw markdown templates for AI logic."""
    return AISettingsResponse(
        primary_model="gemini-3.1-flash-lite-preview",
        fallback_model="google/gemini-3.1-flash-lite-preview (OpenRouter)",
        league_recap_prompt=_read_prompt("league_recap.md"),
        manager_recap_prompt=_read_prompt("manager_recap.md"),
        manager_briefing_prompt=_read_prompt("morning_briefing.md"),
        draft_summary_prompt=_read_prompt("draft_summary.md"),
        guardrails=_read_prompt("guardrails.md"),
    )


@router.put("/ai/settings", response_model=AISettingsResponse)
async def update_ai_settings(
    req: AISettingsUpdateRequest,
    current_user: dict = Depends(require_commissioner),
):
    """Persist updated prompt templates directly to the file system repository."""
    if req.league_recap_prompt is not None:
        _write_prompt("league_recap.md", req.league_recap_prompt)
    if req.manager_recap_prompt is not None:
        _write_prompt("manager_recap.md", req.manager_recap_prompt)
    if req.manager_briefing_prompt is not None:
        _write_prompt("morning_briefing.md", req.manager_briefing_prompt)
    if req.draft_summary_prompt is not None:
        _write_prompt("draft_summary.md", req.draft_summary_prompt)
    if req.guardrails is not None:
        _write_prompt("guardrails.md", req.guardrails)

    return await get_ai_settings(current_user=current_user)


@router.get("/briefings", response_model=list[AdminBriefingResponse])
async def get_admin_briefings(
    week: int | None = None,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve briefings for commissioner review, optionally filtered by week."""

    if week is not None:
        league_result = await db.execute(select(League).limit(1))
        league = league_result.scalar_one_or_none()

        if not league or not league.start_week or not league.current_week:
            week = None

    if week is not None:
        from datetime import timedelta

        today = datetime.now(UTC).date()
        current_week_start = today - timedelta(days=today.weekday())
        requested_week_start = current_week_start - timedelta(weeks=(league.current_week - week))
        requested_week_end = requested_week_start + timedelta(days=6)

        stmt = (
            select(ManagerBriefing, Team.name.label("team_name"))
            .join(Team, Team.id == ManagerBriefing.team_id)
            .where(ManagerBriefing.date >= requested_week_start)
            .where(ManagerBriefing.date <= requested_week_end)
            .order_by(ManagerBriefing.date.desc(), Team.name.asc())
            .limit(100)
        )
    else:
        stmt = (
            select(ManagerBriefing, Team.name.label("team_name"))
            .join(Team, Team.id == ManagerBriefing.team_id)
            .order_by(ManagerBriefing.date.desc(), Team.name.asc())
            .limit(100)
        )

    result = await db.execute(stmt)
    rows = result.all()

    out = []
    for row in rows:
        b = row.ManagerBriefing
        out.append(
            AdminBriefingResponse(
                id=b.id,
                team_name=row.team_name,
                date=b.date,
                content=render_markdown(b.content),
                is_viewed=b.is_viewed,
                created_at=b.created_at,
            )
        )
    return out


@router.put("/briefings/{briefing_id}")
async def edit_briefing(
    briefing_id: int,
    req: dict,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Updates the content of an individual briefing. Requires commissioner privileges."""
    result = await db.execute(select(ManagerBriefing).where(ManagerBriefing.id == briefing_id))
    briefing = result.scalar_one_or_none()
    if not briefing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Briefing not found")

    briefing.content = req.get("content", briefing.content)
    await db.commit()

    team_result = await db.execute(select(Team).where(Team.id == briefing.team_id))
    team = team_result.scalar_one_or_none()

    return {
        "id": briefing.id,
        "team_name": team.name if team else None,
        "date": briefing.date,
        "content": render_markdown(briefing.content),
        "is_viewed": briefing.is_viewed,
        "created_at": briefing.created_at.isoformat(),
    }


@router.post(
    "/briefings/{briefing_id}/regenerate",
    dependencies=[Depends(recap_rate_limit)],
)
async def regenerate_briefing(
    briefing_id: int,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a specific briefing using AI."""
    from moose_api.ai.cost_tracker import log_usage
    from moose_api.ai.llm_router import LLMError, generate_text, reset_batch_quota_state
    from moose_api.ai.prompt_loader import build_guarded_prompt

    result = await db.execute(select(ManagerBriefing).where(ManagerBriefing.id == briefing_id))
    briefing = result.scalar_one_or_none()
    if not briefing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Briefing not found")

    team_result = await db.execute(select(Team).where(Team.id == briefing.team_id))
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    from moose_api.models.free_agent import FreeAgentSnapshot
    from moose_api.models.player import Player
    from moose_api.models.roster import RosterSlot
    from moose_api.models.stats import PlayerValueSnapshot

    fa_stmt = (
        select(PlayerValueSnapshot, Player.name, Player.primary_position)
        .join(Player, Player.id == PlayerValueSnapshot.player_id)
        .join(FreeAgentSnapshot, FreeAgentSnapshot.player_id == Player.id)
        .where(FreeAgentSnapshot.is_available.is_(True))
        .where(PlayerValueSnapshot.type == "season")
        .order_by(PlayerValueSnapshot.composite_value.desc())
        .limit(3)
    )
    fa_rows = (await db.execute(fa_stmt)).all()
    free_agents_data = [
        {
            "name": row.name,
            "position": row.primary_position,
            "composite_value": float(row.PlayerValueSnapshot.composite_value),
        }
        for row in fa_rows
    ]

    roster_stmt = (
        select(Player.name, Player.primary_position, Player.injury_status, RosterSlot.position)
        .join(RosterSlot, RosterSlot.player_id == Player.id)
        .where(RosterSlot.team_id == team.id)
        .where(RosterSlot.is_active.is_(True))
    )
    roster_rows = (await db.execute(roster_stmt)).all()
    roster_data = [
        {
            "name": r.name,
            "position": r.primary_position,
            "roster_spot": r.position,
            "injury_status": r.injury_status,
        }
        for r in roster_rows
    ]

    system_prompt = build_guarded_prompt("morning_briefing.md", {"date": str(briefing.date)})
    payload = {
        "team_name": team.name,
        "roster": roster_data,
        "top_free_agents": free_agents_data,
    }
    user_prompt = "Here is the data payload:\n" + json.dumps(payload, indent=2)

    try:
        reset_batch_quota_state()
        response = await generate_text(user_prompt, system_prompt=system_prompt)
        briefing.content = response.content
        await db.commit()
        await log_usage(db, response, briefing_id=briefing_id)

        return {"message": "Briefing regenerated successfully"}
    except LLMError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM generation failed: {e}",
        ) from e
    return briefing


@router.get("/recaps/{week}", response_model=list[dict])
async def get_admin_recaps(
    week: int,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full unfiltered recap metadata for the commissioner editor view."""

    result = await db.execute(
        select(Recap).where(Recap.week == week).order_by(Recap.type.asc(), Recap.created_at.desc())
    )
    recaps = result.scalars().all()

    out = []
    for r in recaps:
        team_name = None
        if r.team_id:
            team_result = await db.execute(select(Team).where(Team.id == r.team_id))
            t = team_result.scalar_one_or_none()
            team_name = t.name if t else None

        out.append(
            {
                "id": r.id,
                "week": r.week,
                "type": r.type,
                "team_id": r.team_id,
                "team_name": team_name,
                "status": r.status,
                "content": r.content,
                "model_used": r.model_used,
                "provider_used": r.provider_used,
                "tokens_used": r.tokens_used,
                "cost_usd": str(r.cost_usd) if r.cost_usd else None,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "created_at": r.created_at.isoformat(),
            }
        )
    return out


@router.post(
    "/recaps/{recap_id}/regenerate",
    dependencies=[Depends(recap_rate_limit)],
)
async def regenerate_recap(
    recap_id: int,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a specific recap using fresh data from the database."""
    from moose_api.ai.cost_tracker import estimate_cost, log_usage
    from moose_api.ai.llm_router import LLMError, generate_text, reset_batch_quota_state
    from moose_api.ai.prompt_loader import build_recap_prompt
    from moose_api.models.matchup import Matchup
    from moose_api.models.player import Player
    from moose_api.models.roster import RosterSlot

    result = await db.execute(select(Recap).where(Recap.id == recap_id))
    recap = result.scalar_one_or_none()
    if not recap:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recap not found")

    league_result = await db.execute(select(League).where(League.id == recap.league_id))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    matchups_result = await db.execute(
        select(Matchup).where(
            Matchup.league_id == league.id,
            Matchup.week == recap.week,
        )
    )
    matchups = matchups_result.scalars().all()

    teams_result = await db.execute(select(Team).where(Team.league_id == league.id))
    teams = {t.id: t for t in teams_result.scalars().all()}

    base_matchup_data = []
    for m in matchups:
        team_a = teams.get(m.team_a_id)
        team_b = teams.get(m.team_b_id)
        base_matchup_data.append(
            {
                "team_a": team_a.name if team_a else "Unknown",
                "team_b": team_b.name if team_b else "Unknown",
                "team_a_stats": m.team_a_stats or {},
                "team_b_stats": m.team_b_stats or {},
                "category_results": m.category_results or {},
                "team_a_wins": m.team_a_wins,
                "team_b_wins": m.team_b_wins,
                "ties": m.ties,
            }
        )

    historical_matchups_result = await db.execute(
        select(Matchup).where(
            Matchup.league_id == league.id,
            Matchup.week <= recap.week,
        )
    )
    historical_matchups = historical_matchups_result.scalars().all()

    team_records = {team_id: {"wins": 0, "losses": 0, "ties": 0} for team_id in teams}
    for m in historical_matchups:
        if m.team_a_wins > m.team_b_wins:
            if m.team_a_id in team_records:
                team_records[m.team_a_id]["wins"] += 1
            if m.team_b_id in team_records:
                team_records[m.team_b_id]["losses"] += 1
        elif m.team_b_wins > m.team_a_wins:
            if m.team_b_id in team_records:
                team_records[m.team_b_id]["wins"] += 1
            if m.team_a_id in team_records:
                team_records[m.team_a_id]["losses"] += 1
        else:
            if m.team_a_id in team_records:
                team_records[m.team_a_id]["ties"] += 1
            if m.team_b_id in team_records:
                team_records[m.team_b_id]["ties"] += 1

    sorted_teams = sorted(
        teams.values(),
        key=lambda t: (
            -team_records[t.id]["wins"],
            team_records[t.id]["losses"],
            -team_records[t.id]["ties"],
        ),
    )
    standings_data = []
    for rank, team in enumerate(sorted_teams, 1):
        record = team_records[team.id]
        team_records[team.id]["standing"] = rank
        standings_data.append(
            {
                "team_id": team.id,
                "team_name": team.name,
                "standing": rank,
                "wins": record["wins"],
                "losses": record["losses"],
                "ties": record["ties"],
            }
        )
    if recap.type == "league":
        stat_payload = {
            "season_week_being_recapped": recap.week,
            "matchups": base_matchup_data,
            "standings": standings_data,
        }
    else:
        # Manager recap - need team-specific data
        team = teams.get(recap.team_id) if recap.team_id else None
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found for manager recap")

        # Find team's matchup
        team_matchup = next(
            (m for m in matchups if m.team_a_id == team.id or m.team_b_id == team.id),
            None,
        )

        # Fetch roster for that week
        roster_result = await db.execute(
            select(RosterSlot).where(
                RosterSlot.team_id == team.id,
                RosterSlot.season == league.season,
                RosterSlot.week == recap.week,
                RosterSlot.is_active.is_(True),
            )
        )
        roster_slots = roster_result.scalars().all()

        roster_names = []
        for slot in roster_slots:
            player_result = await db.execute(select(Player).where(Player.id == slot.player_id))
            player = player_result.scalar_one_or_none()
            if player:
                roster_names.append(
                    {
                        "name": player.name,
                        "position": slot.position,
                        "injury_status": player.injury_status,
                    }
                )

        # Build matchup detail
        matchup_detail = None
        if team_matchup:
            is_team_a = team_matchup.team_a_id == team.id
            opp_id = team_matchup.team_b_id if is_team_a else team_matchup.team_a_id
            opp = teams.get(opp_id)
            matchup_detail = {
                "opponent": opp.name if opp else "Unknown",
                "my_stats": team_matchup.team_a_stats if is_team_a else team_matchup.team_b_stats,
                "opp_stats": team_matchup.team_b_stats if is_team_a else team_matchup.team_a_stats,
                "category_results": team_matchup.category_results or {},
                "my_wins": team_matchup.team_a_wins if is_team_a else team_matchup.team_b_wins,
                "opp_wins": team_matchup.team_b_wins if is_team_a else team_matchup.team_a_wins,
                "ties": team_matchup.ties,
                "is_complete": team_matchup.is_complete,
            }

        stat_payload = {
            "season_week_being_recapped": recap.week,
            "manager_team": team.name,
            "standing": team_records[team.id]["standing"],
            "record": team_records[team.id],
            "matchup": matchup_detail,
            "roster": roster_names,
            "league_standings": standings_data,
        }

    # Also update the stored stat_payload so future regenerations work
    recap.stat_payload = stat_payload

    template = "league_recap.md" if recap.type == "league" else "manager_recap.md"
    system_prompt, user_prompt = build_recap_prompt(template, stat_payload)

    try:
        reset_batch_quota_state()
        response = await generate_text(user_prompt, system_prompt)
        recap.content = response.content
        recap.model_used = response.model
        recap.provider_used = response.provider
        recap.tokens_used = response.input_tokens + response.output_tokens
        recap.cost_usd = estimate_cost(response)
        recap.status = "published"
        recap.published_at = datetime.now(UTC)
        await db.flush()
        await log_usage(db, response, recap_id=recap.id)
        await db.commit()
        return {"status": "regenerated", "recap_id": recap_id}
    except LLMError as e:
        recap.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM generation failed: {e}",
        ) from e


@router.get("/mappings")
async def get_ambiguous_mappings(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Get all ambiguous player mappings needing commissioner attention."""
    from moose_api.models.player import PlayerMapping

    result = await db.execute(
        select(PlayerMapping).where(PlayerMapping.status == "ambiguous").order_by(PlayerMapping.updated_at.desc())
    )
    mappings = result.scalars().all()
    out = []
    for m in mappings:
        player_result = await db.execute(select(Player).where(Player.yahoo_player_key == m.yahoo_player_key))
        player = player_result.scalar_one_or_none()
        out.append(
            {
                "id": m.id,
                "yahoo_player_key": m.yahoo_player_key,
                "player_name": player.name if player else "Unknown",
                "mlb_id": m.mlb_id,
                "lahman_id": m.lahman_id,
                "source_confidence": m.source_confidence,
                "auto_mapped": m.auto_mapped,
                "status": m.status,
                "notes": m.notes,
            }
        )
    return out


@router.get("/draft-summary", response_model=DraftSummaryResponse)
async def get_draft_summary(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the latest AI draft summary and raw draft pick data for the current season."""
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No league found")

    season = league.season or datetime.now(UTC).year

    summary_result = await db.execute(
        select(DraftSummary)
        .where(DraftSummary.league_id == league.id)
        .order_by(DraftSummary.created_at.desc())
        .limit(1)
    )
    summary = summary_result.scalar_one_or_none()

    picks_result = await db.execute(
        select(DraftPick).where(DraftPick.league_id == league.id).order_by(DraftPick.pick_number.asc())
    )
    all_picks = picks_result.scalars().all()

    teams_result = await db.execute(select(Team).where(Team.league_id == league.id))
    teams = {t.id: t for t in teams_result.scalars().all()}

    teams_by_name: dict[str, list[DraftPickResponse]] = {}
    for pick in all_picks:
        team = teams.get(pick.team_id)
        team_name = team.name if team else f"Team {pick.team_id}"
        if team_name not in teams_by_name:
            teams_by_name[team_name] = []
        teams_by_name[team_name].append(
            DraftPickResponse(
                pick_number=pick.pick_number,
                round_number=pick.round_number,
                round_pick=pick.round_pick,
                player_name=pick.player_name,
                player_position=pick.player_position,
            )
        )

    teams_draft = [TeamDraftResponse(team_name=name, picks=picks) for name, picks in teams_by_name.items()]

    available_players: list[dict] = []
    if summary and summary.stat_payload:
        available_players = summary.stat_payload.get("available_players", [])

    return DraftSummaryResponse(
        id=summary.id if summary else None,
        season=season,
        status=summary.status if summary else "none",
        content=render_markdown(summary.content) if summary and summary.content else None,
        model_used=summary.model_used if summary else None,
        provider_used=summary.provider_used if summary else None,
        tokens_used=summary.tokens_used if summary else None,
        cost_usd=str(summary.cost_usd) if summary and summary.cost_usd else None,
        created_at=summary.created_at if summary else None,
        updated_at=summary.updated_at if summary else None,
        teams_draft=teams_draft,
        available_players=available_players,
    )


@router.post(
    "/draft-summary/generate",
    dependencies=[Depends(recap_rate_limit)],
)
async def generate_draft_summary(
    force: bool = False,
    current_user: dict = Depends(require_commissioner),
):
    """Trigger inline AI draft summary generation (not via background worker)."""
    from moose_api.tasks.generate_draft_summary import run_generate_draft_summary

    try:
        await run_generate_draft_summary(force=force)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Draft summary generation failed: {e}",
        ) from e

    return {"status": "ok", "message": "Draft summary generated successfully"}


@router.put("/draft-summary/{summary_id}")
async def edit_draft_summary(
    summary_id: int,
    req: dict,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Manually edit the content of a draft summary."""
    result = await db.execute(select(DraftSummary).where(DraftSummary.id == summary_id))
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft summary not found")

    summary.content = req.get("content", summary.content)
    await db.commit()
    return {"status": "ok", "content": render_markdown(summary.content or "")}


@router.post("/draft-picks/sync")
async def sync_draft_picks_from_yahoo(
    current_user: dict = Depends(require_commissioner),
):
    """Pull draft results and player data directly from Yahoo Fantasy Sports API.

    Fetches the full /draftresults endpoint, resolves player/team references,
    and persists DraftPick rows. Replaces any existing picks for this league.
    """
    from moose_api.tasks.sync_draft_data import run_sync_draft_data

    try:
        result = await run_sync_draft_data()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Draft sync failed: {e}",
        ) from e

    return result


@router.post("/draft-picks/import")
async def import_draft_picks(
    req: dict,
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Import draft picks from a JSON payload.

    Expects a list of pick objects with pick_number, round_number, round_pick,
    team_yahoo_key or team_name, player_name, and optional player_position.
    Clears any existing draft picks for the league before importing.
    """
    league_result = await db.execute(select(League).limit(1))
    league = league_result.scalar_one_or_none()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No league found")

    picks_data: list[dict] = req.get("picks", [])
    if not picks_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No picks provided")

    teams_result = await db.execute(select(Team).where(Team.league_id == league.id))
    teams_by_name = {t.name.lower(): t for t in teams_result.scalars().all()}
    teams_by_key = {t.yahoo_team_key: t for t in teams_by_name.values() if t.yahoo_team_key}

    from sqlalchemy import delete

    await db.execute(delete(DraftPick).where(DraftPick.league_id == league.id))

    imported = 0
    errors = []
    for raw in picks_data:
        team_name = raw.get("team_name", "")
        team_key = raw.get("team_yahoo_key", "")
        team = teams_by_key.get(team_key) or teams_by_name.get(team_name.lower())
        if not team:
            errors.append(f"Pick #{raw.get('pick_number')}: team '{team_name}' not found")
            continue

        player_key = raw.get("yahoo_player_key")
        player_id = None
        if player_key:
            p_result = await db.execute(select(Player).where(Player.yahoo_player_key == player_key))
            p = p_result.scalar_one_or_none()
            if p:
                player_id = p.id

        pick = DraftPick(
            league_id=league.id,
            team_id=team.id,
            player_id=player_id,
            pick_number=raw["pick_number"],
            round_number=raw["round_number"],
            round_pick=raw["round_pick"],
            player_name=raw["player_name"],
            player_position=raw.get("player_position"),
            yahoo_player_key=player_key,
        )
        db.add(pick)
        imported += 1

    await db.commit()
    return {"imported": imported, "errors": errors}


@router.post("/mappings/confirm-all")
async def confirm_all_auto_mappings(
    current_user: dict = Depends(require_commissioner),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-confirm all auto-mapped ambiguous mappings."""
    from sqlalchemy import update

    from moose_api.models.player import PlayerMapping

    result = await db.execute(
        update(PlayerMapping)
        .where(PlayerMapping.status == "ambiguous", PlayerMapping.auto_mapped.is_(True))
        .values(status="confirmed")
    )
    await db.commit()
    return {"confirmed": result.rowcount}
