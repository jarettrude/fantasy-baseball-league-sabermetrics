"""Automated preseason setup orchestrator.

Chains all preseason jobs in the correct order, with status tracking
so it only runs once. Designed to be triggered:
1. Automatically on commissioner's first login when setup is incomplete
2. Manually from the admin panel as a single "Run Preseason Setup" button

The orchestrator checks each step's completion status before running it,
so it's safe to call multiple times — it will resume where it left off.

Steps (in order):
1. Sync league meta (teams, positions, stat categories)
2. Sync rosters (populates players in DB)
3. Load Lahman crosswalk (sets lahman_id for Stage 3 mapping)
4. Resolve player ID mappings (5-stage pipeline)
5. Load FanGraphs 2025 stats (primary valuations)
6. Lahman fallback seeding (for players not in FanGraphs)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player, PlayerMapping
from moose_api.models.roster import RosterSlot
from moose_api.models.stats import PlayerValueSnapshot, ProjectionBaseline
from moose_api.models.team import Team

logger = logging.getLogger(__name__)

# Redis key to track setup status
SETUP_STATUS_KEY = "moose:preseason_setup"


class SetupStatus:
    """Tracks which preseason setup steps have been completed."""

    def __init__(self) -> None:
        self.has_league = False
        self.has_teams = False
        self.has_players = False
        self.has_mappings = False
        self.has_projections = False
        self.has_valuations = False

    @property
    def is_complete(self) -> bool:
        # Exclude has_mappings and has_projections from completion check since they're slow/optional processes
        # Core functionality works without these - they're nice-to-have enhancements
        return all(
            [
                self.has_league,
                self.has_teams,
                self.has_players,
                self.has_valuations,
            ]
        )

    @property
    def next_step(self) -> str | None:
        if not self.has_league:
            return "sync_league_meta"
        if not self.has_teams or not self.has_players:
            return "sync_rosters"
        if not self.has_mappings:
            return "resolve_mappings"
        if not self.has_projections:
            return "load_fangraphs"
        if not self.has_valuations:
            return "compute_values"
        return None

    def to_dict(self) -> dict:
        return {
            "has_league": self.has_league,
            "has_teams": self.has_teams,
            "has_players": self.has_players,
            "has_mappings": self.has_mappings,
            "has_projections": self.has_projections,
            "has_valuations": self.has_valuations,
            "is_complete": self.is_complete,
            "next_step": self.next_step,
        }


async def check_setup_status() -> SetupStatus:
    """Check current preseason setup status from the database."""
    status = SetupStatus()

    async with async_session_factory() as session:
        league_count = await session.execute(select(func.count(League.id)))
        status.has_league = (league_count.scalar() or 0) > 0

        team_count = await session.execute(select(func.count(Team.id)))
        status.has_teams = (team_count.scalar() or 0) > 0

        player_count = await session.execute(select(func.count(Player.id)))
        status.has_players = (player_count.scalar() or 0) > 0

        mapping_count = await session.execute(select(func.count(PlayerMapping.id)))
        status.has_mappings = (mapping_count.scalar() or 0) > 0

        proj_count = await session.execute(select(func.count(ProjectionBaseline.id)))
        status.has_projections = (proj_count.scalar() or 0) > 0

        val_count = await session.execute(select(func.count(PlayerValueSnapshot.id)))
        status.has_valuations = (val_count.scalar() or 0) > 0

    return status


async def run_preseason_setup(force_reset: bool = False):
    """Run full preseason setup, skipping completed steps.

    If force_reset is True, completely flushes preseason data first.
    Safe to call multiple times without force_reset — resumes where it left off.
    Each step logs progress and sends commissioner notifications.
    """
    start_time = datetime.now(UTC)
    logger.info("=== PRESEASON SETUP STARTED ===")

    try:
        if force_reset:
            logger.info("Force reset requested. Flushing preseason data...")
            await reset_preseason_data()

            from moose_api.core.bootstrap import clear_bootstrap_ready

            await clear_bootstrap_ready()

        status = await check_setup_status()
        if status.is_complete and not force_reset:
            logger.info("Preseason setup already complete — skipping")
            async with async_session_factory() as session:
                notif = CommissionerNotification(
                    type="info",
                    message=("Preseason setup already complete — all steps previously finished. No action taken."),
                )
                session.add(notif)
                await session.commit()
            return

        completed_steps: list[str] = []
        failed_steps: list[str] = []

        # ── Step 1: Sync League Meta ──
        if not status.has_league:
            logger.info("[1/6] Syncing league metadata...")
            try:
                from moose_api.tasks.sync_league import (
                    run_sync_league_meta,
                )

                await run_sync_league_meta()
                completed_steps.append("league_meta")
                logger.info("[1/6] ✓ League metadata synced")
            except Exception as e:
                logger.error("[1/6] ✗ League meta sync failed: %s", e)
                failed_steps.append(f"league_meta: {e}")
                # Can't continue without league
                await _notify_result(start_time, completed_steps, failed_steps)
                return
        else:
            logger.info("[1/6] ✓ League metadata exists (skipping)")

        # ── Step 2: Sync Rosters ──
        status = await check_setup_status()
        if not status.has_players:
            logger.info("[2/6] Syncing rosters...")
            try:
                from moose_api.tasks.sync_roster import run_sync_roster

                await run_sync_roster()
                completed_steps.append("roster_sync")
                logger.info("[2/6] ✓ Rosters synced")
            except Exception as e:
                logger.error("[2/6] ✗ Roster sync failed: %s", e)
                failed_steps.append(f"roster_sync: {e}")
        else:
            logger.info("[2/6] ✓ Players exist (skipping)")

        # ── Step 3: Load Lahman Crosswalk ──
        status = await check_setup_status()
        if status.has_players:
            logger.info("[3/6] Loading Lahman ID crosswalk...")
            try:
                from moose_api.tasks.load_mlb_roster import run_load_mlb_roster

                await run_load_mlb_roster()
                completed_steps.append("lahman_crosswalk")
                logger.info("[3/6] ✓ Lahman crosswalk loaded")
            except Exception as e:
                logger.error("[3/6] ✗ Lahman load failed: %s", e)
                failed_steps.append(f"lahman_crosswalk: {e}")
                # Non-fatal — can continue without Lahman IDs
        else:
            logger.info("[3/6] ⊘ Skipping Lahman (no players)")

        # ── Step 4: Resolve Player Mappings ──
        status = await check_setup_status()
        if status.has_players and not status.has_mappings:
            logger.info("[4/6] Resolving player ID mappings...")
            try:
                from moose_api.tasks.resolve_mappings import (
                    run_resolve_player_mappings,
                )

                await run_resolve_player_mappings()
                completed_steps.append("player_mappings")
                logger.info("[4/6] ✓ Player mappings resolved")
            except Exception as e:
                logger.error("[4/6] ✗ Mapping resolution failed: %s", e)
                failed_steps.append(f"player_mappings: {e}")
        else:
            logger.info("[4/6] ✓ Mappings exist (skipping)")

        # ── Step 5: Load FanGraphs Stats (Primary) ──
        status = await check_setup_status()
        if status.has_players and not status.has_projections:
            logger.info("[5/5] Loading FanGraphs 2025 stats...")
            try:
                from moose_api.tasks.load_fangraphs import (
                    run_load_fangraphs_stats,
                )

                await run_load_fangraphs_stats()
                completed_steps.append("fangraphs_stats")
                logger.info("[5/5] ✓ FanGraphs data loaded")
            except Exception as e:
                logger.error("[5/5] ✗ FanGraphs load failed: %s", e)
                failed_steps.append(f"fangraphs_stats: {e}")
        else:
            logger.info("[5/5] ✓ Projections exist (skipping)")

        await _notify_result(start_time, completed_steps, failed_steps)

    except Exception as e:
        logger.error("PRESEASON SETUP FATAL ERROR: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Preseason setup failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise


async def _notify_result(
    start_time: datetime,
    completed: list[str],
    failed: list[str],
) -> None:
    """Notify commissioner of setup results."""
    elapsed = (datetime.now(UTC) - start_time).total_seconds()

    if failed:
        msg = (
            f"Preseason setup partial: "
            f"{len(completed)} steps OK, "
            f"{len(failed)} failed ({', '.join(failed)}) "
            f"in {elapsed:.1f}s"
        )
        notif_type = "sync_failure"
    else:
        msg = f"Preseason setup complete: {len(completed)} steps executed in {elapsed:.1f}s"
        notif_type = "info"

    logger.info("=== PRESEASON SETUP FINISHED: %s ===", msg)

    async with async_session_factory() as session:
        notif = CommissionerNotification(
            type=notif_type,
            message=msg,
        )
        session.add(notif)
        await session.commit()


async def reset_preseason_data() -> None:
    """Flush all synced preseason data to allow a clean restart."""
    async with async_session_factory() as session:
        # Import models that have foreign key dependencies
        from moose_api.models.free_agent import FreeAgentSnapshot
        from moose_api.models.manager_briefing import ManagerBriefing
        from moose_api.models.matchup import Matchup
        from moose_api.models.recap import Recap
        from moose_api.models.stats import StatLine

        # Delete in reverse dependency order
        await session.execute(delete(ManagerBriefing))
        await session.execute(delete(Matchup))
        await session.execute(delete(Recap))
        await session.execute(delete(FreeAgentSnapshot))
        await session.execute(delete(StatLine))

        await session.execute(delete(PlayerValueSnapshot))
        await session.execute(delete(ProjectionBaseline))
        await session.execute(delete(PlayerMapping))
        await session.execute(delete(RosterSlot))
        await session.execute(delete(Player))
        await session.execute(delete(Team))
        # Keep league meta and stat categories as they are foundational
        await session.commit()
