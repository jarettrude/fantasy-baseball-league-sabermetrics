"""AI-generated recap content production.

Generates league-wide and team-specific recaps using AI language models
with cost tracking and content management.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from moose_api.ai.cost_tracker import estimate_cost, log_usage
from moose_api.ai.llm_router import LLMError, generate_text, reset_batch_quota_state
from moose_api.ai.prompt_loader import build_recap_prompt
from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.matchup import Matchup
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.recap import Recap
from moose_api.models.roster import RosterSlot
from moose_api.models.team import Team

logger = logging.getLogger(__name__)


async def _generate_one_recap(
    session,
    league,
    recap_week: int,
    recap_type: str,
    team: Team | None,
    stat_payload: dict,
) -> None:
    """Generate a single recap (league or manager) and persist it."""
    system_prompt, user_prompt = build_recap_prompt(
        "league_recap.md" if recap_type == "league" else "manager_recap.md",
        stat_payload,
    )

    try:
        response = await generate_text(user_prompt, system_prompt)

        recap = Recap(
            league_id=league.id,
            week=recap_week,
            type=recap_type,
            team_id=team.id if team else None,
            status="published",
            stat_payload=stat_payload,
            content=response.content,
            model_used=response.model,
            provider_used=response.provider,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=estimate_cost(response),
            published_at=datetime.now(UTC),
        )
        session.add(recap)
        await session.flush()

        await log_usage(session, response, recap_id=recap.id)
        logger.info(
            "%s recap generated for week %d (team: %s)",
            recap_type,
            recap_week,
            team.name if team else "league",
        )

    except LLMError as e:
        recap = Recap(
            league_id=league.id,
            week=recap_week,
            type=recap_type,
            team_id=team.id if team else None,
            status="failed",
            stat_payload=stat_payload,
        )
        session.add(recap)

        notif = CommissionerNotification(
            type="ai_failure",
            message=(
                f"Failed to generate {recap_type} recap for week {recap_week}"
                + (f" (team: {team.name})" if team else "")
                + f": {e}"
            ),
        )
        session.add(notif)
        logger.error(
            "Recap generation failed (%s, week %d, team %s): %s",
            recap_type,
            recap_week,
            team.name if team else "league",
            e,
        )


async def run_generate_recaps():
    reset_batch_quota_state()
    try:
        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping recap generation")
                return

            current_week = league.current_week or 1
            recap_week = current_week - 1

            # Fallback: if current_week hasn't advanced but 8+ days since last recap
            if recap_week < 1:
                logger.info("No completed weeks to recap")
                return

            matchups_result = await session.execute(
                select(Matchup).where(
                    Matchup.league_id == league.id,
                    Matchup.week == recap_week,
                )
            )
            matchups = matchups_result.scalars().all()

            teams_result = await session.execute(select(Team).where(Team.league_id == league.id))
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

            standings_data = []
            for team in sorted(teams.values(), key=lambda t: t.standing or 999):
                standings_data.append(
                    {
                        "team": team.name,
                        "wins": team.wins,
                        "losses": team.losses,
                        "ties": team.ties,
                        "standing": team.standing,
                    }
                )

            existing_league = await session.execute(
                select(Recap).where(
                    Recap.league_id == league.id,
                    Recap.week == recap_week,
                    Recap.type == "league",
                )
            )
            if not existing_league.scalar_one_or_none():
                league_payload = {
                    "season_week_being_recapped": recap_week,
                    "matchups": base_matchup_data,
                    "standings": standings_data,
                }
                await _generate_one_recap(session, league, recap_week, "league", None, league_payload)
                await session.commit()
            else:
                logger.info("League recap for week %d already exists", recap_week)

            for team_id, team in teams.items():
                existing_manager = await session.execute(
                    select(Recap).where(
                        Recap.league_id == league.id,
                        Recap.week == recap_week,
                        Recap.type == "manager",
                        Recap.team_id == team_id,
                    )
                )
                if existing_manager.scalar_one_or_none():
                    logger.info("Manager recap for week %d team %s already exists", recap_week, team.name)
                    continue

                team_matchup = next(
                    (m for m in matchups if m.team_a_id == team_id or m.team_b_id == team_id),
                    None,
                )

                roster_result = await session.execute(
                    select(RosterSlot).where(
                        RosterSlot.team_id == team_id,
                        RosterSlot.week == recap_week,
                        RosterSlot.is_active.is_(True),
                    )
                )
                roster_slots = roster_result.scalars().all()

                roster_names = []
                for slot in roster_slots:
                    player_result = await session.execute(select(Player).where(Player.id == slot.player_id))
                    player = player_result.scalar_one_or_none()
                    if player:
                        roster_names.append(
                            {
                                "name": player.name,
                                "position": slot.position,
                                "injury_status": player.injury_status,
                            }
                        )

                matchup_detail = None
                if team_matchup:
                    is_team_a = team_matchup.team_a_id == team_id
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

                manager_payload = {
                    "season_week_being_recapped": recap_week,
                    "manager_team": team.name,
                    "standing": team.standing,
                    "record": {"wins": team.wins, "losses": team.losses, "ties": team.ties},
                    "matchup": matchup_detail,
                    "roster": roster_names,
                    "league_standings": standings_data,
                }

                await _generate_one_recap(session, league, recap_week, "manager", team, manager_payload)
                await session.commit()

            logger.info("All recaps generated for week %d", recap_week)

    except Exception as e:
        logger.error("generate_recaps failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="ai_failure",
                message=f"Recap generation failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
