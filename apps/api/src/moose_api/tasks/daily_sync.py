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
    """Run all in-season daily sync jobs sequentially with graceful degradation."""
    logger.info("=== DAILY SYNC ORCHESTRATOR STARTED ===")

    steps = [
        ("sync_roster", "moose_api.tasks.sync_roster", "run_sync_roster", [], True),
        ("sync_free_agents", "moose_api.tasks.sync_free_agents", "run_sync_free_agents", [], True),
        (
            "sync_rotowire_injuries",
            "moose_api.tasks.sync_rotowire_injuries",
            "run_sync_rotowire_injuries",
            [],
            False,
        ),
        ("sync_injury_status", "moose_api.tasks.sync_injury_status", "run_sync_injury_status", [], False),
        (
            "load_live_season_stats_job",
            "moose_api.tasks.load_live_season_stats",
            "run_load_live_season_stats",
            [],
            False,
        ),
        (
            "recompute_season_values",
            "moose_api.tasks.recompute_values",
            "run_recompute_values",
            ["season"],
            True,
        ),
        (
            "recompute_next_games_values",
            "moose_api.tasks.recompute_values",
            "run_recompute_values",
            ["next_games"],
            False,
        ),
        ("generate_briefings", "moose_api.tasks.generate_briefing", "run_generate_briefings", [], False),
    ]

    completed = []
    failed = []
    skipped = []

    for step in steps:
        name = step[0]
        mod_name = step[1]
        func_name = step[2]
        args = step[3] if len(step) > 3 else []
        is_critical = step[4] if len(step) > 4 else False

        logger.info("Running daily sync step: %s (critical: %s)", name, is_critical)
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            await func(*args)
            completed.append(name)
            logger.info("Daily sync step %s completed successfully", name)
        except Exception as e:
            logger.error("Daily sync step %s failed: %s", name, e, exc_info=True)
            failed.append(f"{name}: {str(e)[:200]}")

            if is_critical:
                logger.error("Critical daily sync step failed. Halting orchestrator.")
                break
            else:
                logger.warning("Non-critical step failed. Continuing with remaining steps.")

    # Calculate remaining steps that were skipped due to critical failure
    if failed and any("critical" in str(s) for s in failed):
        completed_names = set(completed)
        failed_names = set(f.split(":")[0] for f in failed)
        all_names = set(s[0] for s in steps)
        skipped = list(all_names - completed_names - failed_names)

    async with async_session_factory() as session:
        if failed:
            notif = CommissionerNotification(
                type="sync_failure",
                message=(
                    f"Daily Sync Orchestrator partial: {len(completed)} OK, {len(failed)} failed"
                    f"{f', {len(skipped)} skipped' if skipped else ''}. "
                    f"Failures: {', '.join(failed[:3])}"
                    f"{f'... ({len(failed)} total)' if len(failed) > 3 else ''}"
                ),
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

    logger.info(
        "=== DAILY SYNC ORCHESTRATOR FINISHED: %d completed, %d failed, %d skipped ===",
        len(completed),
        len(failed),
        len(skipped),
    )
