"""AI-generated draft summary analysis.

Builds a complete draft data payload from stored draft picks and top available
free agents, then generates a comprehensive AI narrative covering every team's
draft performance, sleepers, reaches, and players left on the board.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from moose_api.ai.cost_tracker import estimate_cost, log_usage
from moose_api.ai.llm_router import LLMError, generate_text, reset_batch_quota_state
from moose_api.ai.prompt_loader import build_guarded_prompt
from moose_api.core.database import async_session_factory
from moose_api.models.draft import DraftPick, DraftSummary
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.team import Team

logger = logging.getLogger(__name__)


async def _build_draft_payload(session, league: League) -> dict:
    """Build the full draft data payload for AI consumption.

    Assembles per-team draft picks (in pick order) and the top 50 remaining
    available players with their value scores.

    Args:
        session: Active async database session
        league: The league to build the payload for

    Returns:
        Structured dict with teams_draft and available_players keys
    """
    teams_result = await session.execute(select(Team).where(Team.league_id == league.id))
    teams = {t.id: t for t in teams_result.scalars().all()}

    picks_result = await session.execute(
        select(DraftPick).where(DraftPick.league_id == league.id).order_by(DraftPick.pick_number.asc())
    )
    all_picks = picks_result.scalars().all()

    teams_draft: dict[str, list[dict]] = {}
    for pick in all_picks:
        team = teams.get(pick.team_id)
        team_name = team.name if team else f"Team {pick.team_id}"
        if team_name not in teams_draft:
            teams_draft[team_name] = []
        teams_draft[team_name].append(
            {
                "pick_number": pick.pick_number,
                "round": pick.round_number,
                "round_pick": pick.round_pick,
                "player_name": pick.player_name,
                "position": pick.player_position,
            }
        )

    from moose_api.models.free_agent import FreeAgentSnapshot
    from moose_api.models.player import Player
    from moose_api.models.stats import PlayerValueSnapshot

    fa_stmt = (
        select(
            PlayerValueSnapshot,
            Player.name,
            Player.primary_position,
            Player.yahoo_rank,
            Player.team_abbr,
        )
        .join(Player, Player.id == PlayerValueSnapshot.player_id)
        .join(FreeAgentSnapshot, FreeAgentSnapshot.player_id == Player.id)
        .where(FreeAgentSnapshot.is_available.is_(True))
        .where(PlayerValueSnapshot.type == "season")
        .order_by(PlayerValueSnapshot.composite_value.desc())
        .limit(50)
    )
    fa_rows = (await session.execute(fa_stmt)).all()
    available_players = [
        {
            "name": row.name,
            "position": row.primary_position,
            "yahoo_rank": row.yahoo_rank,
            "team": row.team_abbr,
            "composite_value": float(row.PlayerValueSnapshot.composite_value),
            "our_rank": row.PlayerValueSnapshot.our_rank,
        }
        for row in fa_rows
    ]

    total_picks = len(all_picks)
    num_teams = len(teams_draft)
    rounds = (total_picks // num_teams) if num_teams else 0

    return {
        "league_name": league.name,
        "season": league.season,
        "total_picks": total_picks,
        "rounds": rounds,
        "num_teams": num_teams,
        "teams_draft": teams_draft,
        "available_players": available_players,
    }


async def run_generate_draft_summary(force: bool = False) -> None:
    """Generate the AI draft summary for the current league season.

    Skips generation if a summary already exists for the season unless force=True.
    Persists the result to draft_summary and creates a commissioner notification.

    Args:
        force: If True, regenerate even if a summary already exists
    """
    reset_batch_quota_state()
    logger.info("Starting run_generate_draft_summary (force=%s)...", force)

    try:
        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping draft summary generation")
                return

            season = league.season or datetime.now(UTC).year

            if not force:
                existing = await session.execute(
                    select(DraftSummary).where(
                        DraftSummary.league_id == league.id,
                        DraftSummary.season == season,
                        DraftSummary.status == "published",
                    )
                )
                if existing.scalar_one_or_none():
                    logger.info("Draft summary for season %d already exists. Use force=True to override.", season)
                    return

            pick_count_result = await session.execute(
                select(DraftPick).where(DraftPick.league_id == league.id).limit(1)
            )
            if not pick_count_result.scalar_one_or_none():
                logger.warning("No draft picks found for league %d — cannot generate summary.", league.id)
                notif = CommissionerNotification(
                    type="info",
                    message="Draft summary skipped: no draft picks found. Load draft data first.",
                )
                session.add(notif)
                await session.commit()
                return

            payload = await _build_draft_payload(session, league)

            system_prompt = build_guarded_prompt("draft_summary.md")
            user_prompt = "Here is the draft data payload:\n" + json.dumps(payload, indent=2, default=str)

            try:
                response = await generate_text(user_prompt, system_prompt=system_prompt)

                summary = DraftSummary(
                    league_id=league.id,
                    season=season,
                    status="published",
                    stat_payload=payload,
                    content=response.content,
                    model_used=response.model,
                    provider_used=response.provider,
                    tokens_used=response.input_tokens + response.output_tokens,
                    cost_usd=estimate_cost(response),
                )
                session.add(summary)
                await session.flush()

                await log_usage(session, response)

                notif = CommissionerNotification(
                    type="info",
                    message=(
                        f"Draft summary generated for {season} season "
                        f"({response.input_tokens + response.output_tokens} tokens)."
                    ),
                )
                session.add(notif)
                await session.commit()
                logger.info("Draft summary generated for season %d", season)

            except LLMError as e:
                summary = DraftSummary(
                    league_id=league.id,
                    season=season,
                    status="failed",
                    stat_payload=payload,
                )
                session.add(summary)

                notif = CommissionerNotification(
                    type="ai_failure",
                    message=f"Draft summary generation failed for {season}: {e}",
                )
                session.add(notif)
                await session.commit()
                logger.error("Draft summary generation failed: %s", e)

    except Exception as e:
        logger.error("run_generate_draft_summary failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="ai_failure",
                message=f"Draft summary generation failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
