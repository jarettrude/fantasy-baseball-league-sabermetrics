"""Player value recomputation engine.

Calculates fantasy player values based on advanced metrics, category
performance, and injury adjustments. Supports season and next-games projections.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import func, select

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot, ProjectionBaseline, StatLine
from moose_api.services.gambling_service import GamblingService
from moose_api.services.valuation_engine import (
    ComputeZScoresRequest,
    PlayerStatSummary,
    StatCategory,
    compute_z_scores,
)

logger = logging.getLogger(__name__)


def _stat_field_for_category(display_name: str) -> str | None:
    """Map Yahoo category display_name to StatLine column name."""
    mapping = {
        "R": "runs",
        "HR": "home_runs",
        "RBI": "rbi",
        "SB": "stolen_bases",
        "AVG": "batting_avg",
        "H": "hits",
        "AB": "at_bats",
        "W": "wins",
        "SV": "saves",
        "K": "strikeouts",
        "SO": "strikeouts",
        "ERA": "era",
        "WHIP": "whip",
        "IP": "innings_pitched",
        "ER": "earned_runs",
        "BB": "walks",
    }
    return mapping.get(display_name.upper())


# Map Yahoo stat category display names to FanGraphs projected_stats keys.
# FanGraphs uses 'SO' for strikeouts while Yahoo uses 'K'.
PROJECTION_KEY_MAP = {
    "R": "R",
    "HR": "HR",
    "RBI": "RBI",
    "SB": "SB",
    "AVG": "AVG",
    "H": "H",
    "AB": "AB",
    "H/AB": "AVG",
    "W": "W",
    "SV": "SV",
    "K": "SO",
    "SO": "SO",
    "ERA": "ERA",
    "WHIP": "WHIP",
    "IP": "IP",
    "ER": "ER",
    "BB": "BB",
}


async def _load_projection_stats(
    session, player_ids: list[int], categories: list[StatCategory]
) -> dict[int, dict[str, float]]:
    """Load preseason projection stats from projection_baseline table.

    Returns a dict mapping player_id -> {category_display_name: value}.
    Only includes players who have projection baselines.
    """
    result = await session.execute(
        select(ProjectionBaseline)
        .where(ProjectionBaseline.player_id.in_(player_ids))
        .order_by(ProjectionBaseline.updated_at.desc())
    )
    baselines = result.scalars().all()

    # Keep only the most recent baseline per player
    latest: dict[int, ProjectionBaseline] = {}
    for pb in baselines:
        if pb.player_id not in latest:
            latest[pb.player_id] = pb

    stats_by_player: dict[int, dict[str, float]] = {}
    for player_id, pb in latest.items():
        projected = pb.projected_stats or {}
        player_stats: dict[str, float] = {}

        for cat in categories:
            fg_key = PROJECTION_KEY_MAP.get(cat.display_name.upper())
            if fg_key and fg_key in projected:
                val = projected[fg_key]
                if val is not None:
                    player_stats[cat.display_name] = float(val)

        if player_stats:
            stats_by_player[player_id] = player_stats

    return stats_by_player


async def _load_statline_stats(
    session, player_ids: list[int], categories: list[StatCategory]
) -> dict[int, dict[str, float]]:
    """Aggregate in-season stats from the stat_line table.

    Returns a dict mapping player_id -> {category_display_name: value}.
    """
    stat_col_map = {
        "R": StatLine.runs,
        "HR": StatLine.home_runs,
        "RBI": StatLine.rbi,
        "SB": StatLine.stolen_bases,
        "H": StatLine.hits,
        "AB": StatLine.at_bats,
        "W": StatLine.wins,
        "SV": StatLine.saves,
        "K": StatLine.strikeouts,
        "SO": StatLine.strikeouts,
        "IP": StatLine.innings_pitched,
        "ER": StatLine.earned_runs,
        "BB": StatLine.walks,
    }

    sum_queries: dict[int, dict[str, float]] = {}
    for display_name, col in stat_col_map.items():
        result = await session.execute(
            select(StatLine.player_id, func.sum(col).label("total"))
            .where(StatLine.player_id.in_(player_ids))
            .group_by(StatLine.player_id)
        )
        for row in result.all():
            if row.player_id not in sum_queries:
                sum_queries[row.player_id] = {}
            if row.total is not None:
                sum_queries[row.player_id][display_name] = float(row.total)

    # Calculate ratio stats from components
    avg_result = await session.execute(
        select(
            StatLine.player_id,
            func.sum(StatLine.hits).label("h"),
            func.sum(StatLine.at_bats).label("ab"),
        )
        .where(StatLine.player_id.in_(player_ids))
        .group_by(StatLine.player_id)
    )
    for row in avg_result.all():
        if row.ab and row.ab > 0:
            sum_queries.setdefault(row.player_id, {})["AVG"] = float(row.h or 0) / float(row.ab)

    era_result = await session.execute(
        select(
            StatLine.player_id,
            func.sum(StatLine.earned_runs).label("er"),
            func.sum(StatLine.innings_pitched).label("ip"),
        )
        .where(StatLine.player_id.in_(player_ids))
        .group_by(StatLine.player_id)
    )
    for row in era_result.all():
        if row.ip and float(row.ip) > 0:
            sum_queries.setdefault(row.player_id, {})["ERA"] = (float(row.er or 0) * 9) / float(row.ip)

    whip_result = await session.execute(
        select(
            StatLine.player_id,
            func.sum(StatLine.walks).label("bb"),
            func.sum(StatLine.hits).label("h"),
            func.sum(StatLine.innings_pitched).label("ip"),
        )
        .where(StatLine.player_id.in_(player_ids))
        .group_by(StatLine.player_id)
    )
    for row in whip_result.all():
        if row.ip and float(row.ip) > 0:
            sum_queries.setdefault(row.player_id, {})["WHIP"] = (float(row.bb or 0) + float(row.h or 0)) / float(row.ip)

    return sum_queries


async def _fetch_mlb_starts(days: int = 7) -> dict[int, int]:
    """Fetch probable pitchers for the next N days to identify 2-start pitchers."""
    today = date.today()
    end_date = today + timedelta(days=days - 1)
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule?"
        f"sportId=1&startDate={today.isoformat()}&endDate={end_date.isoformat()}&hydrate=probablePitcher"
    )

    starts_by_mlb_id: dict[int, int] = {}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                for game_date in data.get("dates", []):
                    for game in game_date.get("games", []):
                        for team_type in ["away", "home"]:
                            prob = game.get("teams", {}).get(team_type, {}).get("probablePitcher", {})
                            if "id" in prob:
                                mlb_id = int(prob["id"])
                                starts_by_mlb_id[mlb_id] = starts_by_mlb_id.get(mlb_id, 0) + 1
        except Exception as e:
            logger.warning("Failed to fetch MLB schedule for starts: %s", e)

    return starts_by_mlb_id


async def run_recompute_values(snapshot_type: str = "season"):
    try:
        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found, skipping recompute")
                return

            categories = [StatCategory(**cat) for cat in (league.stat_categories or [])]
            if not categories:
                logger.warning("No stat categories configured")
                return

            players_result = await session.execute(select(Player))
            all_players = players_result.scalars().all()

            if not all_players:
                logger.info("No players found, skipping recompute")
                return

            player_ids = [p.id for p in all_players]
            player_map = {p.id: p for p in all_players}

            # Try in-season stat_line data first
            statline_stats = await _load_statline_stats(session, player_ids, categories)
            has_season_data = any(any(v != 0 for v in stats.values()) for stats in statline_stats.values())

            # Load projection baselines as fallback/supplement
            projection_stats = await _load_projection_stats(session, player_ids, categories)

            # Merge: in-season data takes priority, projections fill gaps
            merged_stats: dict[int, dict[str, float]] = {}
            data_sources: dict[int, list[str]] = {}

            for pid in player_ids:
                sl = statline_stats.get(pid, {})
                proj = projection_stats.get(pid, {})

                if has_season_data and sl and any(v != 0 for v in sl.values()):
                    # Player has real in-season data — use it
                    merged_stats[pid] = sl
                    sources = ["yahoo_stats"]
                    if proj:
                        sources.append("fangraphs")
                    data_sources[pid] = sources
                elif proj:
                    # No in-season data — fall back to projections
                    merged_stats[pid] = proj
                    data_sources[pid] = ["fangraphs"]
                else:
                    # No data at all
                    merged_stats[pid] = {}
                    data_sources[pid] = []

            # Streamer overrides for Next 7 Days
            mlb_starts = {}
            win_probs = {}
            if snapshot_type == "next_games":
                mlb_starts = await _fetch_mlb_starts(days=7)

                # Phase 2: Gambling Vegas Odds
                try:
                    gambling = GamblingService()
                    win_probs = await gambling.get_team_win_probabilities()
                    await gambling.close()
                except Exception as e:
                    logger.warning("Failed to fetch gambling odds: %s", e)

            # Build PlayerStatSummary for each player
            player_summaries = []
            for player_id, player in player_map.items():
                player_stats = merged_stats.get(player_id, {})

                multiplier = 1.0
                if snapshot_type == "next_games" and player.is_pitcher:
                    starts = mlb_starts.get(player.mlb_id, 0) if player.mlb_id else 0
                    if player.primary_position == "SP" or "SP" in player.eligible_positions:
                        if starts >= 2:
                            multiplier = 1.8  # Huge boost for 2-start pitchers
                        elif starts == 1:
                            multiplier = 1.0  # Normal 1-start pitcher
                        else:
                            # 0 starts for a SP means they probably aren't playing much this week
                            multiplier = 0.2 if player.primary_position == "SP" else 1.0
                    else:
                        # Pure Reliever (RP) – normal multiplier
                        multiplier = 1.0

                # Matchup adjustments (Gambling API)
                matchup_multiplier = 1.0
                if snapshot_type == "next_games" and player.team_abbr in win_probs:
                    prob = win_probs[player.team_abbr]
                    if prob >= 0.65:
                        matchup_multiplier = 1.15
                    elif prob <= 0.35:
                        matchup_multiplier = 0.85

                player_summaries.append(
                    PlayerStatSummary(
                        player_id=player_id,
                        stats=player_stats,
                        injury_status=player.injury_status,
                        yahoo_rank=player.yahoo_rank,
                        data_sources=data_sources.get(player_id),
                        schedule_multiplier=multiplier,
                        missed_games_next_7_days=player.missed_games_count or 0,
                        matchup_multiplier=matchup_multiplier,
                    )
                )

            if not player_summaries:
                return

            request = ComputeZScoresRequest(
                players=player_summaries,
                categories=categories,
                snapshot_type=snapshot_type,
            )
            response = compute_z_scores(request)

            # Compute rankings from composite values
            sorted_snaps = sorted(
                response.snapshots,
                key=lambda s: float(s.composite_value),
                reverse=True,
            )
            rank_map: dict[int, int] = {}
            for rank_idx, snap in enumerate(sorted_snaps, start=1):
                rank_map[snap.player_id] = rank_idx

            today = date.today()
            src_label = "projections" if not has_season_data else "in-season stats"

            for snap in response.snapshots:
                existing = await session.execute(
                    select(PlayerValueSnapshot).where(
                        PlayerValueSnapshot.player_id == snap.player_id,
                        PlayerValueSnapshot.snapshot_date == today,
                        PlayerValueSnapshot.type == snapshot_type,
                    )
                )
                existing_snap = existing.scalar_one_or_none()

                our_rank = rank_map.get(snap.player_id)
                player_yahoo_rank = player_map[snap.player_id].yahoo_rank

                if existing_snap:
                    existing_snap.category_scores = snap.category_scores
                    existing_snap.composite_value = snap.composite_value
                    existing_snap.injury_weight = snap.injury_weight
                    existing_snap.our_rank = our_rank
                    existing_snap.yahoo_rank = player_yahoo_rank
                else:
                    new_snap = PlayerValueSnapshot(
                        player_id=snap.player_id,
                        snapshot_date=today,
                        type=snapshot_type,
                        category_scores=snap.category_scores,
                        composite_value=snap.composite_value,
                        injury_weight=snap.injury_weight,
                        our_rank=our_rank,
                        yahoo_rank=player_yahoo_rank,
                    )
                    session.add(new_snap)

            await session.commit()

            players_with_data = sum(1 for pid in player_ids if merged_stats.get(pid))
            logger.info(
                "Recompute %s values complete: %d players (%d with data from %s)",
                snapshot_type,
                len(response.snapshots),
                players_with_data,
                src_label,
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"Recompute {snapshot_type} values complete: "
                    f"{len(response.snapshots)} players ranked, "
                    f"{players_with_data} with stat data (source: {src_label})"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("recompute_values (%s) failed: %s", snapshot_type, e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Recompute {snapshot_type} values failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
