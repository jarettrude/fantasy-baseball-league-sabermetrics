"""Manager briefing generation.

Produces a personalized daily briefing per managed fantasy team using the
shared ``roster_optimizer`` so the LLM receives the same position-aligned
drop/pickup analysis that the web UI surfaces. Generation runs in parallel
across teams under a bounded semaphore to stay within LLM provider
rate limits while keeping wall-clock time roughly constant as the league
grows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from moose_api.ai.llm_router import generate_text
from moose_api.ai.prompt_loader import build_guarded_prompt
from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.manager_briefing import ManagerBriefing
from moose_api.models.notification import CommissionerNotification
from moose_api.models.team import Team
from moose_api.services.roster_optimizer import (
    build_recommendations,
    recommendations_to_prompt_payload,
)

logger = logging.getLogger(__name__)

# Cap concurrent LLM calls so we respect provider per-minute quotas
# without serializing league-wide generation behind a single request.
# A 3-way fan-out keeps p95 latency bounded by roughly one LLM round-trip
# per three teams regardless of league size.
BRIEFING_CONCURRENCY = max(1, int(os.getenv("BRIEFING_CONCURRENCY", "3")))


async def _generate_one(
    team: Team,
    league: League | None,
    current_week: int,
    today_iso: str,
    system_prompt: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, str | None]:
    """Generate and persist a single team's briefing.

    Each call runs in its own DB session so parallel invocations do not
    share a ``AsyncSession`` (SQLAlchemy async sessions are not
    concurrency-safe). Returns ``(team_name, success, error_message)``.
    """
    async with semaphore:
        try:
            async with async_session_factory() as db:
                rec = await build_recommendations(db, team, league, current_week)
                payload = recommendations_to_prompt_payload(rec)
                user_prompt = "Here is the data payload:\n" + json.dumps(payload, indent=2)

                response = await generate_text(user_prompt, system_prompt=system_prompt)

                briefing = ManagerBriefing(
                    team_id=team.id,
                    date=datetime.fromisoformat(today_iso).date(),
                    content=response.content,
                    is_viewed=False,
                )
                db.add(briefing)
                await db.commit()
                logger.info("Briefing generated for team %s", team.name)
                return team.name, True, None
        except Exception as exc:  # noqa: BLE001 - per-team failures must not kill the batch
            logger.error("Failed to generate briefing for team %s: %s", team.name, exc)
            return team.name, False, str(exc)[:200]


async def run_generate_briefings(force: bool = False) -> None:
    """Generate today's manager briefings for every managed team.

    Args:
        force: When True, regenerate even if briefings already exist for
            today. When False (default) the job exits early the moment a
            briefing for today is found, since daily-sync retries should
            not re-bill the LLM provider.
    """
    logger.info("Starting run_generate_briefings (force=%s)", force)

    try:
        today = datetime.now(UTC).date()

        async with async_session_factory() as db:
            if not force:
                existing = await db.execute(select(ManagerBriefing).where(ManagerBriefing.date == today).limit(1))
                if existing.scalar_one_or_none() is not None:
                    logger.info(
                        "Briefings for %s already exist. Use force=True to override. Exiting.",
                        today,
                    )
                    return

            teams_result = await db.execute(select(Team).where(Team.manager_user_id.is_not(None)))
            managed_teams = list(teams_result.scalars().all())

            league_result = await db.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            current_week = league.current_week if league and league.current_week is not None else 1

        if not managed_teams:
            logger.info("No managed teams found to brief.")
            return

        system_prompt = build_guarded_prompt("morning_briefing.md", {"date": str(today)})
        semaphore = asyncio.Semaphore(BRIEFING_CONCURRENCY)

        results = await asyncio.gather(
            *(
                _generate_one(team, league, current_week, today.isoformat(), system_prompt, semaphore)
                for team in managed_teams
            )
        )

        generated = sum(1 for _, ok, _ in results if ok)
        failed = [(name, err) for name, ok, err in results if not ok]

        async with async_session_factory() as db:
            if failed:
                sample = ", ".join(f"{name}: {err}" for name, err in failed[:3])
                notif = CommissionerNotification(
                    type="sync_failure",
                    message=(
                        f"Briefing generation partial: {generated}/{len(managed_teams)} teams briefed. "
                        f"Failures: {sample}"
                        f"{f' (+{len(failed) - 3} more)' if len(failed) > 3 else ''}"
                    ),
                )
            else:
                notif = CommissionerNotification(
                    type="info",
                    message=(
                        f"Morning briefings generated: {generated}/{len(managed_teams)} teams briefed for {today}."
                    ),
                )
            db.add(notif)
            await db.commit()

    except Exception as exc:
        logger.error("generate_briefings failed: %s", exc, exc_info=True)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Briefing generation failed: {str(exc)[:500]}",
            )
            session.add(notif)
            await session.commit()
        raise
