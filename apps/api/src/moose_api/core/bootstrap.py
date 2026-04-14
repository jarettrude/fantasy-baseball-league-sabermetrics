"""Application bootstrap and initialization.

Handles application startup tasks including database initialization,
league setup, and initial data loading for new deployments.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import import_module

from sqlalchemy import select

from moose_api.core.config import settings
from moose_api.core.redis import get_redis

logger = logging.getLogger(__name__)

BOOTSTRAP_READY_KEY = "moose:bootstrap:ready"
BOOTSTRAP_READY_AT_KEY = "moose:bootstrap:ready_at"

# Jobs that must wait for the commissioner to complete OAuth bootstrap.
# Yahoo-dependent jobs need the commissioner token; others need players in DB.
BOOTSTRAP_GUARDED_JOBS: tuple[str, ...] = (
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
    "generate_briefings",
    "generate_briefings_force",
    "run_preseason_setup_job",
    "run_force_preseason_setup_job",
    "run_daily_sync_job",
)

# Jobs to enqueue immediately after bootstrap completes.
# Only the orchestrator is needed — it internally runs sync_league_meta,
# sync_roster, etc. in the correct order.
POST_BOOTSTRAP_JOBS: tuple[str, ...] = ("run_preseason_setup_job",)


async def is_bootstrap_ready() -> bool:
    redis = await get_redis()
    return bool(await redis.exists(BOOTSTRAP_READY_KEY))


async def ensure_commissioner_bootstrap_ready(job_name: str) -> bool:
    """Return True when commissioner OAuth bootstrap is complete, else log/skip.

    Checks Redis flag first, falls back to database check if Redis was flushed.
    """
    ready = await is_bootstrap_ready()

    # If Redis flag is missing, check database as fallback (handles Redis flush)
    if not ready:
        try:
            from moose_api.core.database import async_session_factory
            from moose_api.models.user import User
            from moose_api.models.yahoo_token import YahooToken

            async with async_session_factory() as session:
                commissioner_result = await session.execute(
                    select(User).where(User.yahoo_guid == settings.commissioner_yahoo_guid)
                )
                commissioner = commissioner_result.scalar_one_or_none()

                if commissioner:
                    token_result = await session.execute(
                        select(YahooToken).where(YahooToken.user_id == commissioner.id)
                    )
                    token = token_result.scalar_one_or_none()
                    if token:
                        # Commissioner exists in DB with valid token - restore Redis flag
                        await mark_bootstrap_ready()
                        logger.info("Redis bootstrap flag missing but commissioner exists in DB. Restoring flag.")
                        ready = True
        except Exception as e:
            logger.warning("Database fallback check for bootstrap failed: %s", e)

    if not ready:
        logger.info(
            "Job %s deferred until commissioner completes OAuth bootstrap.",
            job_name,
        )
    return ready


async def mark_bootstrap_ready() -> bool:
    """Mark the system as bootstrap-ready. Returns True if this is the first time."""
    redis = await get_redis()
    was_ready = bool(await redis.exists(BOOTSTRAP_READY_KEY))
    await redis.set(BOOTSTRAP_READY_KEY, "1")
    await redis.set(BOOTSTRAP_READY_AT_KEY, datetime.now(UTC).isoformat())
    if not was_ready:
        logger.info("Commissioner bootstrap completed. Enabling guarded jobs.")
    return not was_ready


async def clear_bootstrap_ready() -> None:
    """Remove the bootstrap-ready flag from Redis.

    Must be called whenever the database is reset so that
    guarded jobs are deferred until the commissioner logs in again.
    """
    redis = await get_redis()
    await redis.delete(BOOTSTRAP_READY_KEY)
    await redis.delete(BOOTSTRAP_READY_AT_KEY)
    logger.info("Bootstrap-ready flag cleared. Guarded jobs will defer until next commissioner login.")


async def enqueue_post_bootstrap_jobs(job_names: Iterable[str] | None = None) -> None:
    """Queue the initial sync jobs once bootstrap completes."""
    names = tuple(job_names) if job_names is not None else POST_BOOTSTRAP_JOBS
    if not names:
        return

    try:
        connections = import_module("arq.connections")
        create_pool = connections.create_pool

        from moose_api.worker import WorkerSettings

        pool = await create_pool(WorkerSettings.redis_settings)
        try:
            for job_name in names:
                await pool.enqueue_job(job_name)
                logger.info("Queued %s as part of commissioner bootstrap warmup.", job_name)
        finally:
            await pool.close()
    except (ImportError, AttributeError, ConnectionError, OSError, RuntimeError) as exc:
        logger.error("Failed to enqueue post-bootstrap jobs: %s", exc)
