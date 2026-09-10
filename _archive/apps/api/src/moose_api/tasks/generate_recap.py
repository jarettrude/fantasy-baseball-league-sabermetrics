"""AI-generated recap content production.

Generates league-wide and team-specific recaps using AI language models
with cost tracking and content management.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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
from moose_api.models.stats import PlayerValueSnapshot
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


def _compute_standings_from_matchups(matchups_list, teams_dict):
    """Compute win/loss/tie standings from a set of matchups."""
    records = {tid: {"wins": 0, "losses": 0, "ties": 0} for tid in teams_dict}
    for m in matchups_list:
        if m.team_a_wins > m.team_b_wins:
            if m.team_a_id in records:
                records[m.team_a_id]["wins"] += 1
            if m.team_b_id in records:
                records[m.team_b_id]["losses"] += 1
        elif m.team_b_wins > m.team_a_wins:
            if m.team_b_id in records:
                records[m.team_b_id]["wins"] += 1
            if m.team_a_id in records:
                records[m.team_a_id]["losses"] += 1
        else:
            if m.team_a_id in records:
                records[m.team_a_id]["ties"] += 1
            if m.team_b_id in records:
                records[m.team_b_id]["ties"] += 1

    sorted_teams = sorted(
        teams_dict.values(),
        key=lambda t: (
            -records[t.id]["wins"],
            records[t.id]["losses"],
            -records[t.id]["ties"],
        ),
    )
    standings = []
    for rank, team in enumerate(sorted_teams, 1):
        rec = records[team.id]
        standings.append(
            {
                "team": team.name,
                "wins": rec["wins"],
                "losses": rec["losses"],
                "ties": rec["ties"],
                "standing": rank,
            }
        )
    return standings


async def _detect_deep_cuts(session, teams_dict, recap_week, league):
    """Find rostered players with low league-wide ownership (< 65%).

    Uses historical PlayerValueSnapshot data scoped to the recap week so
    the ownership percentage reflects what it was at the time, not today.
    These are either brilliant flyer picks or complete disasters.

    Batched: loads all roster slots, players, and snapshots in 3 queries
    instead of per-player round-trips (was N+1 for 12-team × 25-player).
    """
    deep_cuts = []
    bench_slots = {"BN", "IL", "IL+", "NA", "DL", "DL+"}

    # Approximate the end date of the recap week for historical snapshot lookup.
    current_week = league.current_week if league.current_week is not None else 1
    today = datetime.now(UTC).date()
    current_week_start = today - timedelta(days=today.weekday())
    recap_week_end = current_week_start - timedelta(weeks=(current_week - recap_week)) + timedelta(days=6)
    recap_window_start = recap_week_end - timedelta(days=3)

    # --- Batch 1: load all roster slots for all teams in one query ---
    all_team_ids = list(teams_dict.keys())
    roster_result = await session.execute(
        select(RosterSlot).where(
            RosterSlot.team_id.in_(all_team_ids),
            RosterSlot.week == recap_week,
            RosterSlot.is_active.is_(True),
        )
    )
    all_slots = roster_result.scalars().all()

    # Filter out bench slots and collect player IDs
    starter_slots: list[tuple] = []  # (team_id, player_id, position)
    player_ids_needed: set[int] = set()
    for slot in all_slots:
        if slot.position in bench_slots:
            continue
        starter_slots.append((slot.team_id, slot.player_id, slot.position))
        player_ids_needed.add(slot.player_id)

    if not player_ids_needed:
        return deep_cuts

    # --- Batch 2: load all players in one query ---
    player_result = await session.execute(
        select(Player).where(Player.id.in_(list(player_ids_needed)))
    )
    player_map = {p.id: p for p in player_result.scalars().all()}

    # --- Batch 3: load snapshots for all players in the recap window ---
    snap_result = await session.execute(
        select(PlayerValueSnapshot)
        .where(
            PlayerValueSnapshot.player_id.in_(list(player_ids_needed)),
            PlayerValueSnapshot.type == "season",
            PlayerValueSnapshot.snapshot_date >= recap_window_start,
            PlayerValueSnapshot.snapshot_date <= recap_week_end,
        )
        .order_by(PlayerValueSnapshot.snapshot_date.desc())
    )
    # Keep only the latest snapshot per player
    snap_map: dict[int, PlayerValueSnapshot] = {}
    for snap in snap_result.scalars():
        if snap.player_id not in snap_map:
            snap_map[snap.player_id] = snap

    # --- Fold results in Python ---
    for team_id, player_id, position in starter_slots:
        player = player_map.get(player_id)
        if not player:
            continue

        snap = snap_map.get(player_id)
        roster_pct = float(snap.roster_percent) if snap and snap.roster_percent is not None else None
        if roster_pct is None or roster_pct >= 65.0:
            continue

        composite = float(snap.composite_value) if snap else None
        team = teams_dict[team_id]

        deep_cuts.append(
            {
                "team": team.name,
                "player": player.name,
                "position": position,
                "roster_percent": round(roster_pct, 1),
                "composite_value": round(composite, 3) if composite is not None else None,
            }
        )

    return deep_cuts


async def run_generate_recaps():
    reset_batch_quota_state()
    try:
        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping recap generation")
                return

            current_week = league.current_week if league.current_week is not None else 1
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

            # --- Current standings ---
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

            # --- Historical matchups for standings history ---
            all_historical_result = await session.execute(
                select(Matchup).where(
                    Matchup.league_id == league.id,
                    Matchup.week <= recap_week,
                )
            )
            all_historical_matchups = all_historical_result.scalars().all()

            # --- Previous week standings (for upset detection) ---
            prev_week_standings = []
            if recap_week > 1:
                prev_matchups = [m for m in all_historical_matchups if m.week <= recap_week - 1]
                prev_week_standings = _compute_standings_from_matchups(prev_matchups, teams)

            # --- Weekly standings history (full arc, one entry per completed week) ---
            # Gives the model visibility into roller-coaster trends, flukes correcting,
            # sustained rises/falls, and multi-week momentum shifts.
            weekly_standings_history = []
            for w in range(1, recap_week + 1):
                week_matchups = [m for m in all_historical_matchups if m.week <= w]
                week_standings = _compute_standings_from_matchups(week_matchups, teams)
                weekly_standings_history.append({
                    "after_week": w,
                    "standings": [{"team": s["team"], "standing": s["standing"]} for s in week_standings],
                })


            # --- Deep cuts (low-ownership players in active lineups) ---
            deep_cuts = await _detect_deep_cuts(session, teams, recap_week, league)

            # --- Upset alerts (lower-ranked team beats higher-ranked team) ---
            upsets = []
            # Use previous week standings as the "going-in" ranking for upset detection
            ranking_source = prev_week_standings if prev_week_standings else standings_data
            team_rank_map = {entry["team"]: entry["standing"] for entry in ranking_source}
            num_teams = len(team_rank_map)
            midpoint = num_teams // 2  # "above the fold" threshold

            for m in matchups:
                team_a = teams.get(m.team_a_id)
                team_b = teams.get(m.team_b_id)
                if not team_a or not team_b:
                    continue

                a_rank = team_rank_map.get(team_a.name, 999)
                b_rank = team_rank_map.get(team_b.name, 999)

                # Determine winner and loser
                if m.team_a_wins > m.team_b_wins:
                    winner_name, winner_rank = team_a.name, a_rank
                    loser_name, loser_rank = team_b.name, b_rank
                elif m.team_b_wins > m.team_a_wins:
                    winner_name, winner_rank = team_b.name, b_rank
                    loser_name, loser_rank = team_a.name, a_rank
                else:
                    continue  # tie, not an upset

                # Upset = winner was ranked lower (higher number) than loser,
                # AND the loser was in the top half of the standings
                spread = winner_rank - loser_rank
                if spread > 0 and loser_rank <= midpoint:
                    upsets.append(
                        {
                            "winner": winner_name,
                            "winner_entering_rank": winner_rank,
                            "loser": loser_name,
                            "loser_entering_rank": loser_rank,
                            "standings_spread": spread,
                            "winner_cats": m.team_a_wins if winner_name == team_a.name else m.team_b_wins,
                            "loser_cats": m.team_b_wins if winner_name == team_a.name else m.team_a_wins,
                        }
                    )

            # Sort upsets by spread descending — biggest upsets first
            upsets.sort(key=lambda u: u["standings_spread"], reverse=True)

            existing_league = await session.execute(
                select(Recap).where(
                    Recap.league_id == league.id,
                    Recap.week == recap_week,
                    Recap.type == "league",
                )
            )
            if not existing_league.scalar_one_or_none():
                # Cap deep cuts: top 4 hidden gems + top 4 worst busts by composite value
                gems = sorted(
                    [d for d in deep_cuts if (d["composite_value"] or 0) > 0],
                    key=lambda d: d["composite_value"] or 0,
                    reverse=True,
                )[:4]
                busts = sorted(
                    [d for d in deep_cuts if (d["composite_value"] or 0) <= 0],
                    key=lambda d: d["composite_value"] or 0,
                )[:4]
                league_deep_cuts = gems + busts

                league_payload = {
                    "season_week_being_recapped": recap_week,
                    "matchups": base_matchup_data,
                    "standings": standings_data,
                    "previous_week_standings": prev_week_standings,
                    "weekly_standings_history": weekly_standings_history,
                    "deep_cuts": league_deep_cuts,
                    "upsets": upsets,
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
                    "league_standings": standings_data,
                    "weekly_standings_history": weekly_standings_history,
                    "your_deep_cuts": [d for d in deep_cuts if d["team"] == team.name],
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
