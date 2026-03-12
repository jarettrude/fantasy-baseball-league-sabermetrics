"""Manager briefing generation.

Generates personalized daily briefings for fantasy team managers
with actionable insights and recommendations.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from moose_api.ai.llm_router import generate_text
from moose_api.core.database import async_session_factory
from moose_api.models.manager_briefing import ManagerBriefing
from moose_api.models.notification import CommissionerNotification
from moose_api.models.team import Team

logger = logging.getLogger(__name__)


async def run_generate_briefings(force: bool = False) -> None:
    logger.info("Starting run_generate_briefings (force=%s)...", force)

    try:
        async with async_session_factory() as db:
            today = datetime.now(UTC).date()

            if not force:
                existing_check = await db.execute(select(ManagerBriefing).where(ManagerBriefing.date == today).limit(1))
                if existing_check.scalar_one_or_none() is not None:
                    logger.info(
                        "Briefings for %s already exist. Use force=True to override. Exiting.",
                        today,
                    )
                    return

            teams_result = await db.execute(select(Team).where(Team.manager_user_id.is_not(None)))
            managed_teams = teams_result.scalars().all()

            if not managed_teams:
                logger.info("No managed teams found to brief.")
                return

            from moose_api.models.free_agent import FreeAgentSnapshot
            from moose_api.models.player import Player
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

            from moose_api.ai.prompt_loader import build_guarded_prompt

            system_prompt = build_guarded_prompt("morning_briefing.md", {"date": str(today)})

            generated = 0
            failed_teams = []
            for team in managed_teams:
                logger.info(f"Generating briefing for Team {team.name}")

                from moose_api.models.roster import RosterSlot

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

                payload = {
                    "team_name": team.name,
                    "roster": roster_data,
                    "top_free_agents": free_agents_data,
                }

                user_prompt = "Here is the data payload:\n" + json.dumps(payload, indent=2)

                try:
                    response = await generate_text(user_prompt, system_prompt=system_prompt)

                    briefing = ManagerBriefing(team_id=team.id, date=today, content=response.content, is_viewed=False)
                    db.add(briefing)
                    await db.commit()
                    generated += 1
                    logger.info(f"Successfully generated briefing for Team {team.name}")
                except Exception as e:
                    await db.rollback()
                    failed_teams.append(team.name)
                    logger.error(f"Failed to generate briefing for Team {team.name}: {e}")

                await asyncio.sleep(1)

            if failed_teams:
                notif = CommissionerNotification(
                    type="sync_failure",
                    message=(
                        f"Briefing generation partial: {generated}/{len(managed_teams)} teams briefed. "
                        f"Failed: {', '.join(failed_teams)}"
                    ),
                )
            else:
                notif = CommissionerNotification(
                    type="info",
                    message=f"Morning briefings generated: {generated}/{len(managed_teams)} teams briefed for {today}.",
                )
            db.add(notif)
            await db.commit()

    except Exception as e:
        logger.error("generate_briefings failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Briefing generation failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
