"""Manager briefing generation.

Produces a personalized daily briefing per managed fantasy team using the
shared ``roster_optimizer`` so the LLM receives the same position-aligned
drop/pickup analysis that the web UI surfaces. Enriched with matchup context,
recent form, hot/cold player trends, two-start pitcher alerts, and Vegas odds.

Generation runs in parallel across teams under a bounded semaphore to stay
within LLM provider rate limits while keeping wall-clock time roughly constant
as the league grows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select

from moose_api.ai.llm_router import generate_text
from moose_api.ai.prompt_loader import build_guarded_prompt
from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.manager_briefing import ManagerBriefing
from moose_api.models.matchup import Matchup
from moose_api.models.notification import CommissionerNotification
from moose_api.models.stats import StatLine
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


async def _get_matchup_context(
    db, team: Team, league: League, current_week: int, teams_by_id: dict
) -> dict | None:
    """Get the current week's opponent info for matchup context."""
    result = await db.execute(
        select(Matchup).where(
            Matchup.league_id == league.id,
            Matchup.week == current_week,
            or_(Matchup.team_a_id == team.id, Matchup.team_b_id == team.id),
        )
    )
    matchup = result.scalar_one_or_none()
    if not matchup:
        return None

    opp_id = matchup.team_b_id if matchup.team_a_id == team.id else matchup.team_a_id
    opp = teams_by_id.get(opp_id)
    if not opp:
        return None

    return {
        "opponent": opp.name,
        "opponent_standing": opp.standing,
        "opponent_record": {"wins": opp.wins, "losses": opp.losses, "ties": opp.ties},
    }


async def _get_recent_form(
    db, team: Team, league: League, current_week: int
) -> str:
    """Compute W/L/T string for the last 5 weeks of results."""
    if current_week <= 1:
        return ""

    lookback = max(1, current_week - 5)
    result = await db.execute(
        select(Matchup)
        .where(
            Matchup.league_id == league.id,
            Matchup.week >= lookback,
            Matchup.week < current_week,
            or_(Matchup.team_a_id == team.id, Matchup.team_b_id == team.id),
        )
        .order_by(Matchup.week.asc())
    )
    matchups = result.scalars().all()

    form = []
    for m in matchups:
        is_team_a = m.team_a_id == team.id
        my_wins = m.team_a_wins if is_team_a else m.team_b_wins
        opp_wins = m.team_b_wins if is_team_a else m.team_a_wins
        if my_wins > opp_wins:
            form.append("W")
        elif opp_wins > my_wins:
            form.append("L")
        else:
            form.append("T")

    return "".join(form)


async def _get_hot_cold_trends(
    db, roster_player_ids: list[int]
) -> list[dict]:
    """Identify hot and cold players based on last 7 days of StatLine data.

    ``StatLine`` stores **season-to-date cumulative** snapshots per day, not
    per-game box scores. To extract the actual 7-day contribution we find the
    earliest and latest snapshot in the window and subtract.
    """
    if not roster_player_ids:
        return []

    today = date.today()
    seven_days_ago = today - timedelta(days=7)

    # Grab all snapshots in the window — we'll pick min/max per player in Python
    # to avoid complicated SQL window functions that vary across dialects.
    result = await db.execute(
        select(StatLine)
        .where(
            StatLine.player_id.in_(roster_player_ids),
            StatLine.game_date >= seven_days_ago,
        )
        .order_by(StatLine.player_id, StatLine.game_date.asc())
    )
    rows = result.scalars().all()

    # Group by (player_id, is_pitcher) — grab first and last snapshot
    from collections import defaultdict

    player_snaps: dict[tuple[int, bool], list] = defaultdict(list)
    for row in rows:
        player_snaps[(row.player_id, row.is_pitcher)].append(row)

    trends = []
    for (player_id, is_pitcher), snaps in player_snaps.items():
        if len(snaps) < 2:
            continue  # Need at least 2 snapshots to compute a delta

        earliest = snaps[0]
        latest = snaps[-1]
        days_span = (latest.game_date - earliest.game_date).days
        if days_span < 3:
            continue  # Not enough spread for a meaningful trend

        if is_pitcher:
            ip_delta = float(latest.innings_pitched or 0) - float(earliest.innings_pitched or 0)
            if ip_delta <= 0:
                continue
            er_delta = float(latest.earned_runs or 0) - float(earliest.earned_runs or 0)
            k_delta = int((latest.strikeouts or 0) - (earliest.strikeouts or 0))
            w_delta = int((latest.wins or 0) - (earliest.wins or 0))
            sv_delta = int((latest.saves or 0) - (earliest.saves or 0))

            era_7d = (er_delta * 9) / ip_delta
            parts = [f"{ip_delta:.1f} IP", f"{era_7d:.2f} ERA"]
            if k_delta >= 6:
                parts.append(f"{k_delta} K")
            if w_delta >= 1:
                parts.append(f"{w_delta} W")
            if sv_delta >= 1:
                parts.append(f"{sv_delta} SV")

            if era_7d <= 2.50 and ip_delta >= 5:
                signal = "hot"
            elif era_7d >= 6.00 and ip_delta >= 4:
                signal = "cold"
            else:
                continue
        else:
            ab_delta = float(latest.at_bats or 0) - float(earliest.at_bats or 0)
            if ab_delta < 10:
                continue
            h_delta = float(latest.hits or 0) - float(earliest.hits or 0)
            hr_delta = int((latest.home_runs or 0) - (earliest.home_runs or 0))
            rbi_delta = int((latest.rbi or 0) - (earliest.rbi or 0))
            sb_delta = int((latest.stolen_bases or 0) - (earliest.stolen_bases or 0))

            avg_7d = h_delta / ab_delta
            parts = [f".{int(avg_7d * 1000):03d} AVG ({int(ab_delta)} AB)"]
            if hr_delta >= 2:
                parts.append(f"{hr_delta} HR")
            if rbi_delta >= 4:
                parts.append(f"{rbi_delta} RBI")
            if sb_delta >= 2:
                parts.append(f"{sb_delta} SB")

            if avg_7d >= 0.350 or hr_delta >= 3:
                signal = "hot"
            elif avg_7d <= 0.150:
                signal = "cold"
            else:
                continue

        trends.append({
            "player_id": player_id,
            "signal": signal,
            "days_span": days_span,
            "highlights": ", ".join(parts),
        })

    return trends


def _identify_two_start_pitchers(
    roster_payload: list[dict], mlb_starts: dict[int, int]
) -> list[dict]:
    """Cross-reference roster pitchers with MLB probable starters."""
    two_starters = []
    for player in roster_payload:
        if not player or not player.get("is_pitcher"):
            continue
        mlb_id = player.get("mlb_id")
        if not mlb_id:
            continue
        starts = mlb_starts.get(mlb_id, 0)
        if starts >= 2:
            two_starters.append({
                "name": player["name"],
                "starts_this_week": starts,
            })
    return two_starters


def _get_vegas_for_roster(
    roster_payload: list[dict], vegas_probs: dict[str, float]
) -> list[dict]:
    """Match roster players to their MLB team's Vegas win probability."""
    if not vegas_probs:
        return []

    seen_teams = set()
    favorable = []
    for player in roster_payload:
        if not player:
            continue
        abbr = player.get("team_abbr")
        if not abbr or abbr in seen_teams:
            continue
        seen_teams.add(abbr)
        prob = vegas_probs.get(abbr)
        if prob and prob >= 0.60:
            favorable.append({
                "team": abbr,
                "win_probability": round(prob * 100, 1),
            })

    favorable.sort(key=lambda x: x["win_probability"], reverse=True)
    return favorable[:3]


async def _generate_one(
    team: Team,
    league: League | None,
    current_week: int,
    today_iso: str,
    system_prompt: str,
    semaphore: asyncio.Semaphore,
    teams_by_id: dict[int, Team],
    mlb_starts: dict[int, int],
    vegas_probs: dict[str, float],
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

                # --- Matchup context ---
                if league:
                    matchup_ctx = await _get_matchup_context(
                        db, team, league, current_week, teams_by_id
                    )
                    if matchup_ctx:
                        payload["current_matchup"] = matchup_ctx

                # --- Recent form ---
                if league:
                    form = await _get_recent_form(db, team, league, current_week)
                    if form:
                        payload["recent_form"] = form

                # --- Hot/cold player trends ---
                roster_ids = [p["id"] for p in payload.get("roster", []) if p]
                hot_cold = await _get_hot_cold_trends(db, roster_ids)
                if hot_cold:
                    # Attach player names from roster for the LLM
                    id_to_name = {
                        p["id"]: p["name"]
                        for p in payload.get("roster", []) if p
                    }
                    for trend in hot_cold:
                        trend["name"] = id_to_name.get(trend["player_id"], "Unknown")
                    payload["hot_cold_report"] = hot_cold

                # --- Two-start pitchers ---
                two_starters = _identify_two_start_pitchers(
                    payload.get("roster", []), mlb_starts
                )
                if two_starters:
                    payload["two_start_pitchers"] = two_starters

                # --- Vegas odds flavor ---
                vegas_flavor = _get_vegas_for_roster(
                    payload.get("roster", []), vegas_probs
                )
                if vegas_flavor:
                    payload["vegas_favorable_matchups"] = vegas_flavor

                user_prompt = "Here is the data payload:\n" + json.dumps(
                    payload, indent=2
                )

                # --- Enrich roster entries with per-player category z-scores ---
                # This lets the LLM identify which specific players drag down
                # specific categories, enabling much more targeted analysis.
                for player_entry in payload.get("roster", []):
                    if not player_entry:
                        continue
                    pid = player_entry.get("id")
                    if not pid:
                        continue
                    from moose_api.models.stats import PlayerValueSnapshot
                    snap_result = await db.execute(
                        select(PlayerValueSnapshot)
                        .where(
                            PlayerValueSnapshot.player_id == pid,
                            PlayerValueSnapshot.type == "season",
                        )
                        .order_by(PlayerValueSnapshot.snapshot_date.desc())
                        .limit(1)
                    )
                    snap = snap_result.scalar_one_or_none()
                    if snap and snap.category_scores:
                        player_entry["category_zscores"] = {
                            cat: round(float(score), 3)
                            for cat, score in snap.category_scores.items()
                        }

                # --- Bench swap suggestions ---
                if payload.get("bench_swaps"):
                    # Already serialized by recommendations_to_prompt_payload
                    pass

                user_prompt = "Here is the data payload:\n" + json.dumps(
                    payload, indent=2
                )

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
                existing = await db.execute(
                    select(ManagerBriefing)
                    .where(ManagerBriefing.date == today)
                    .limit(1)
                )
                if existing.scalar_one_or_none() is not None:
                    logger.info(
                        "Briefings for %s already exist. Use force=True to override. Exiting.",
                        today,
                    )
                    return

            teams_result = await db.execute(
                select(Team).where(Team.manager_user_id.is_not(None))
            )
            managed_teams = list(teams_result.scalars().all())

            # Also load all teams for matchup context lookups
            all_teams_result = await db.execute(select(Team))
            teams_by_id = {t.id: t for t in all_teams_result.scalars().all()}

            league_result = await db.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            current_week = (
                league.current_week
                if league and league.current_week is not None
                else 1
            )

        if not managed_teams:
            logger.info("No managed teams found to brief.")
            return

        # --- Pre-fetch shared data (once for all teams) ---
        mlb_starts: dict[int, int] = {}
        try:
            from moose_api.tasks.recompute_values import _fetch_mlb_starts

            mlb_starts = await _fetch_mlb_starts(days=7)
            logger.info("Fetched MLB starts for %d pitchers", len(mlb_starts))
        except Exception as exc:
            logger.warning("Failed to fetch MLB starts: %s", exc)

        vegas_probs: dict[str, float] = {}
        try:
            from moose_api.services.gambling_service import GamblingService

            gambling = GamblingService()
            vegas_probs = await gambling.get_team_win_probabilities()
            await gambling.close()
            logger.info("Fetched Vegas odds for %d teams", len(vegas_probs))
        except Exception as exc:
            logger.warning("Failed to fetch Vegas odds: %s", exc)

        system_prompt = build_guarded_prompt(
            "morning_briefing.md", {"date": str(today)}
        )
        semaphore = asyncio.Semaphore(BRIEFING_CONCURRENCY)

        results = await asyncio.gather(
            *(
                _generate_one(
                    team,
                    league,
                    current_week,
                    today.isoformat(),
                    system_prompt,
                    semaphore,
                    teams_by_id,
                    mlb_starts,
                    vegas_probs,
                )
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
