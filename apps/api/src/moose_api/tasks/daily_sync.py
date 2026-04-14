"""Daily synchronization orchestrator for in-season data updates.

Coordinates sequential execution of daily data sync jobs including roster updates,
free agent polling, injury status updates, value recomputation, and briefing generation.
Provides failure handling and notification for commissioner awareness.
"""

import logging

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification

logger = logging.getLogger(__name__)


async def run_daily_sync():
    """Run all in-season daily sync jobs sequentially."""
    logger.info("=== DAILY SYNC ORCHESTRATOR STARTED ===")

    steps = [
        ("sync_roster", "moose_api.tasks.sync_roster", "run_sync_roster"),
        ("sync_free_agents", "moose_api.tasks.sync_free_agents", "run_sync_free_agents"),
        (
            "sync_rotowire_injuries",
            "moose_api.tasks.sync_rotowire_injuries",
            "run_sync_rotowire_injuries",
        ),
        ("sync_injury_status", "moose_api.tasks.sync_injury_status", "run_sync_injury_status"),
        ("load_live_season_stats_job", "moose_api.tasks.load_live_season_stats", "run_load_live_season_stats"),
        (
            "recompute_season_values",
            "moose_api.tasks.recompute_values",
            "run_recompute_values",
            ["season"],
        ),
        (
            "recompute_next_games_values",
            "moose_api.tasks.recompute_values",
            "run_recompute_values",
            ["next_games"],
        ),
        ("generate_briefings", "moose_api.tasks.generate_briefing", "run_generate_briefings"),
    ]

    completed = []
    failed = []

    for step in steps:
        name = step[0]
        mod_name = step[1]
        func_name = step[2]
        args = step[3] if len(step) > 3 else []

        logger.info(f"Running daily sync step: {name}")
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            await func(*args)
            completed.append(name)
        except Exception as e:
            logger.error(f"Daily sync step {name} failed: {e}")
            failed.append(f"{name}: {str(e)}")
            if name in ["sync_roster", "sync_free_agents", "recompute_season_values"]:
                logger.error("Critical daily sync step failed. Halting orchestrator.")
                break

    async with async_session_factory() as session:
        if failed:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Daily Sync Orchestrator partial: {len(completed)} OK, failures: {', '.join(failed)}",
            )
        else:
            notif = CommissionerNotification(
                type="info",
                message=(
                    f"Daily Sync Orchestrator complete: {len(completed)} steps executed successfully. "
                    "Cached data was utilized natively."
                ),
            )
        session.add(notif)
        await session.commit()

    logger.info("=== DAILY SYNC ORCHESTRATOR FINISHED ===")
