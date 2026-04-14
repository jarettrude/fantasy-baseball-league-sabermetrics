"""Background job worker using arq for task scheduling and execution.

Manages periodic data synchronization, AI content generation, and maintenance
tasks with Redis-based job tracking, admin stop controls, and bootstrap guards.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from arq import cron, func
from arq.connections import RedisSettings

from moose_api.core.bootstrap import BOOTSTRAP_GUARDED_JOBS, ensure_commissioner_bootstrap_ready
from moose_api.core.config import settings

logger = logging.getLogger(__name__)

JOB_STATUS_KEY_PREFIX = "moose:job:"
JOB_STOP_KEY = "moose:jobs:stop_all"
JOB_STATUS_TTL = 86400


class JobStopRequested(Exception):
    """Raised when an admin-issued hard stop is detected."""


async def _job_stop_requested(redis) -> bool:
    """Check if admin has requested a hard stop for all jobs.

    Args:
        redis: Redis client instance

    Returns:
        True if stop flag is set in Redis, False otherwise
    """
    if not redis:
        return False
    try:
        flag = await redis.get(JOB_STOP_KEY)
    except Exception:
        return False
    return bool(flag)


async def _ensure_not_stopped(redis):
    """Raise JobStopRequested if admin has requested a hard stop.

    Args:
        redis: Redis client instance

    Raises:
        JobStopRequested: If stop flag is set in Redis
    """
    if await _job_stop_requested(redis):
        raise JobStopRequested()


async def _run_coro_with_stop(redis, coro):
    """Execute coroutine with periodic stop checks.

    Runs the coroutine while checking every second for an admin stop request.
    If stop is requested, the task is cancelled and JobStopRequested is raised.

    Args:
        redis: Redis client instance for stop flag checks
        coro: Async callable to execute

    Returns:
        Result of the coroutine execution

    Raises:
        JobStopRequested: If admin stop flag is detected during execution
    """
    task = asyncio.create_task(coro())
    try:
        if not redis:
            return await task

        while True:
            done, _ = await asyncio.wait({task}, timeout=1.0)
            if task in done:
                return await task

            if await _job_stop_requested(redis):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise JobStopRequested()
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _track_job(ctx: dict, job_name: str, coro):
    """Wrap job function with Redis-based progress tracking and lifecycle management.

    Tracks job execution status in Redis with fields: status, started_at,
    completed_at, elapsed_seconds, error. Handles bootstrap guards for
    commissioner-dependent jobs and admin stop requests.

    Bootstrap-guarded jobs are deferred with a notification if commissioner
    OAuth bootstrap is incomplete. All jobs check for admin stop requests
    during execution.

    Args:
        ctx: Arq context dict containing redis client
        job_name: Name of the job for tracking purposes
        coro: Async callable to execute

    Raises:
        JobStopRequested: If admin stop flag is detected during execution
        Exception: Any exception from the wrapped coroutine
    """
    redis = ctx.get("redis")
    key = f"{JOB_STATUS_KEY_PREFIX}{job_name}"
    started = datetime.now(UTC)

    if job_name in BOOTSTRAP_GUARDED_JOBS:
        ready = await ensure_commissioner_bootstrap_ready(job_name)
        if not ready:
            logger.info("Skipping %s until commissioner bootstrap completes", job_name)
            if redis:
                await redis.hset(
                    key,
                    mapping={
                        "status": "deferred",
                        "started_at": started.isoformat(),
                        "completed_at": started.isoformat(),
                        "elapsed_seconds": "0",
                        "error": "Commissioner bootstrap incomplete",
                    },
                )
                await redis.expire(key, JOB_STATUS_TTL)

            try:
                from moose_api.core.database import async_session_factory
                from moose_api.models.notification import CommissionerNotification

                async with async_session_factory() as session:
                    notif = CommissionerNotification(
                        type="job_deferred",
                        message=f"Job '{job_name}' deferred — waiting for commissioner OAuth bootstrap.",
                    )
                    session.add(notif)
                    await session.commit()
            except Exception:
                pass

            return

    if redis:
        await redis.hset(
            key,
            mapping={
                "status": "processing",
                "started_at": started.isoformat(),
                "completed_at": "",
                "elapsed_seconds": "0",
                "error": "",
            },
        )
        await redis.expire(key, JOB_STATUS_TTL)

    try:
        await _ensure_not_stopped(redis)
        await _run_coro_with_stop(redis, coro)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if redis:
            await redis.hset(
                key,
                mapping={
                    "status": "success",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": str(round(elapsed, 1)),
                },
            )
            await redis.expire(key, JOB_STATUS_TTL)
        logger.info("%s completed in %.1fs", job_name, elapsed)
    except JobStopRequested:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if redis:
            await redis.hset(
                key,
                mapping={
                    "status": "stopped",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": str(round(elapsed, 1)),
                    "error": "Stopped by commissioner",
                },
            )
            await redis.expire(key, JOB_STATUS_TTL)
        logger.warning("%s stopped via admin hard stop", job_name)
    except Exception as e:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if redis:
            await redis.hset(
                key,
                mapping={
                    "status": "failed",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": str(round(elapsed, 1)),
                    "error": str(e)[:500],
                },
            )
            await redis.expire(key, JOB_STATUS_TTL)
        raise


async def sync_league_meta(ctx):
    from moose_api.tasks.sync_league import run_sync_league_meta

    await _track_job(ctx, "sync_league_meta", run_sync_league_meta)


async def sync_matchups(ctx):
    from moose_api.tasks.sync_league import run_sync_matchups

    await _track_job(ctx, "sync_matchups", run_sync_matchups)


async def sync_roster_trends(ctx):
    from moose_api.tasks.sync_roster_trends import run_sync_roster_trends

    await _track_job(ctx, "sync_roster_trends", run_sync_roster_trends)


async def sync_roster(ctx):
    from moose_api.tasks.sync_roster import run_sync_roster

    await _track_job(ctx, "sync_roster", run_sync_roster)


async def sync_free_agents(ctx):
    from moose_api.tasks.sync_free_agents import run_sync_free_agents

    await _track_job(ctx, "sync_free_agents", run_sync_free_agents)


async def sync_rotowire_injuries(ctx):
    from moose_api.tasks.sync_rotowire_injuries import run_sync_rotowire_injuries

    await _track_job(ctx, "sync_rotowire_injuries", run_sync_rotowire_injuries)


async def sync_injury_status(ctx):
    from moose_api.tasks.sync_injury_status import run_sync_injury_status

    await _track_job(ctx, "sync_injury_status", run_sync_injury_status)


async def recompute_season_values(ctx):
    from moose_api.tasks.recompute_values import run_recompute_values

    await _track_job(ctx, "recompute_season_values", lambda: run_recompute_values("season"))


async def recompute_next_games_values(ctx):
    from moose_api.tasks.recompute_values import run_recompute_values

    await _track_job(ctx, "recompute_next_games_values", lambda: run_recompute_values("next_games"))


async def sync_advanced_metrics_job(ctx):
    from moose_api.tasks.sync_advanced_metrics import run_sync_advanced_metrics

    await _track_job(ctx, "sync_advanced_metrics_job", run_sync_advanced_metrics)


async def generate_weekly_recaps(ctx):
    from moose_api.tasks.generate_recap import run_generate_recaps

    await _track_job(ctx, "generate_weekly_recaps", run_generate_recaps)


async def generate_briefings(ctx):
    from moose_api.tasks.generate_briefing import run_generate_briefings

    await _track_job(ctx, "generate_briefings", run_generate_briefings)


async def generate_briefings_force(ctx):
    from moose_api.tasks.generate_briefing import run_generate_briefings

    await _track_job(ctx, "generate_briefings_force", lambda: run_generate_briefings(force=True))


async def generate_draft_summary(ctx):
    from moose_api.tasks.generate_draft_summary import run_generate_draft_summary

    await _track_job(ctx, "generate_draft_summary", run_generate_draft_summary)


async def generate_draft_summary_force(ctx):
    from moose_api.tasks.generate_draft_summary import run_generate_draft_summary

    await _track_job(ctx, "generate_draft_summary_force", lambda: run_generate_draft_summary(force=True))


async def load_mlb_roster_data(ctx):
    from moose_api.tasks.load_mlb_roster import run_load_mlb_roster

    await _track_job(ctx, "load_mlb_roster_data", run_load_mlb_roster)


async def resolve_player_mappings(ctx):
    from moose_api.tasks.resolve_mappings import run_resolve_player_mappings

    await _track_job(ctx, "resolve_player_mappings", run_resolve_player_mappings)


async def load_live_season_stats_job(ctx):
    from moose_api.tasks.load_live_season_stats import run_load_live_season_stats

    await _track_job(ctx, "load_live_season_stats_job", run_load_live_season_stats)


async def run_preseason_setup_job(ctx):
    from moose_api.tasks.preseason_setup import run_preseason_setup

    await _track_job(ctx, "run_preseason_setup_job", lambda: run_preseason_setup(force_reset=False))


async def run_force_preseason_setup_job(ctx):
    from moose_api.tasks.preseason_setup import run_preseason_setup

    await _track_job(ctx, "run_force_preseason_setup_job", lambda: run_preseason_setup(force_reset=True))


async def run_daily_sync_job(ctx):
    from moose_api.tasks.daily_sync import run_daily_sync

    await _track_job(ctx, "run_daily_sync_job", run_daily_sync)


async def purge_session_logs(ctx):
    """Remove session log entries older than 30 days.

    Maintains database hygiene by cleaning up historical session data
    that is no longer needed for analytics or debugging.
    """

    async def _run():
        from sqlalchemy import delete

        from moose_api.core.database import async_session_factory
        from moose_api.models.session_log import SessionLog

        async with async_session_factory() as session:
            cutoff = datetime.now(UTC) - timedelta(days=30)
            result = await session.execute(delete(SessionLog).where(SessionLog.created_at < cutoff))
            await session.commit()
            logger.info("Purged %d session log rows older than 30 days", result.rowcount)

    await _track_job(ctx, "purge_session_logs", _run)


async def purge_ai_prompt_raw(ctx):
    """Redact raw AI prompts and stat payloads older than 30 days per spec S14.

    Removes sensitive AI usage data from Recap stat_payload fields and deletes
    AIUsageLog entries to comply with data retention policies and reduce storage.
    """

    async def _run():
        from sqlalchemy import delete, update

        from moose_api.core.database import async_session_factory
        from moose_api.models.ai_usage import AIUsageLog
        from moose_api.models.recap import Recap

        async with async_session_factory() as session:
            cutoff = datetime.now(UTC) - timedelta(days=30)
            result = await session.execute(update(Recap).where(Recap.created_at < cutoff).values(stat_payload={}))
            logger.info("Redacted stat_payload on %d old recaps", result.rowcount)

            result2 = await session.execute(delete(AIUsageLog).where(AIUsageLog.created_at < cutoff))
            logger.info("Purged %d AI usage log rows older than 30 days", result2.rowcount)

            await session.commit()

    await _track_job(ctx, "purge_ai_prompt_raw", _run)


async def purge_free_agent_snapshots(ctx):
    """Keep only last 48 hours of free agent snapshots per spec S5.

    Limits free agent snapshot storage to recent data to reduce database
    size while maintaining enough history for trend analysis.
    """

    async def _run():
        from sqlalchemy import delete

        from moose_api.core.database import async_session_factory
        from moose_api.models.free_agent import FreeAgentSnapshot

        async with async_session_factory() as session:
            cutoff = datetime.now(UTC) - timedelta(hours=48)
            result = await session.execute(delete(FreeAgentSnapshot).where(FreeAgentSnapshot.snapshot_at < cutoff))
            await session.commit()
            logger.info("Purged %d free agent snapshots older than 48h", result.rowcount)

    await _track_job(ctx, "purge_free_agent_snapshots", _run)


class WorkerSettings:
    """Arq worker configuration for background job scheduling.

    Defines all available job functions and their cron schedules. Jobs include
    data synchronization from Yahoo/MLB APIs, AI content generation, value
    recomputation, and periodic data purging for storage management.

    Cron jobs run at specific times (e.g., sync_league_meta every 6 hours),
    while functions can be triggered manually via the admin interface.
    """

    functions = [
        sync_league_meta,
        sync_matchups,
        sync_roster,
        sync_roster_trends,
        sync_free_agents,
        sync_rotowire_injuries,
        sync_injury_status,
        recompute_season_values,
        recompute_next_games_values,
        sync_advanced_metrics_job,
        generate_weekly_recaps,
        purge_session_logs,
        purge_ai_prompt_raw,
        purge_free_agent_snapshots,
        load_mlb_roster_data,
        func(resolve_player_mappings, timeout=900),
        func(load_live_season_stats_job, timeout=900),
        run_preseason_setup_job,
        run_force_preseason_setup_job,
        func(run_daily_sync_job, timeout=900),
        generate_briefings,
        generate_briefings_force,
        generate_draft_summary,
        generate_draft_summary_force,
    ]

    cron_jobs = [
        cron(sync_league_meta, hour={0, 6, 12, 18}, minute=0),
        cron(sync_matchups, minute={0, 15, 30, 45}),
        cron(sync_roster, hour={0, 6, 12, 18}, minute=10),
        cron(sync_free_agents, hour=2, minute=45, run_at_startup=True),
        cron(sync_rotowire_injuries, hour={0, 6, 12, 18}, minute=30),
        cron(sync_injury_status, hour={0, 6, 12, 18}, minute=45),
        cron(sync_roster_trends, hour=3, minute=15, run_at_startup=True),
        cron(load_live_season_stats_job, hour=1, minute=0, run_at_startup=True),
        cron(load_mlb_roster_data, hour=1, minute=30),
        cron(recompute_season_values, hour=4, minute=0, run_at_startup=True),
        cron(recompute_next_games_values, hour=4, minute=15, run_at_startup=True),
        cron(sync_advanced_metrics_job, hour=4, minute=30),
        cron(purge_session_logs, hour=3, minute=0),
        cron(purge_ai_prompt_raw, hour=3, minute=0),
        cron(purge_free_agent_snapshots, hour=3, minute=30),
        cron(generate_briefings, hour=6, minute=30),
    ]

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
