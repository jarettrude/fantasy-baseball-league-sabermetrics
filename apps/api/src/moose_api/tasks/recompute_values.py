"""Player value recomputation engine.

Calculates fantasy player values based on advanced metrics, category
performance, and injury adjustments. Supports season and next-games projections.

Key fixes (2026-05 audit):
- _load_statline_stats now reads the LATEST cumulative snapshot per player
  instead of SUM()-ing all daily snapshots (which produced wildly inflated
  counting stats since StatLine stores season-to-date cumulative totals).
- Hitter schedule multipliers based on team games next 7 days (previously
  always 1.0, which meant hitters with 7 games were valued the same as
  hitters with 3 games).
- is_pitcher flag is passed through to the valuation engine for sample-size
  regression.
- xwOBA/xERA integrated as valuation signal blended into composite when
  available.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import and_, func, select
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot, StatLine, ProjectionBaseline
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


async def _load_statline_stats(
    session, player_ids: list[int], categories: list[StatCategory]
) -> dict[int, dict[str, float]]:
    """Load the latest cumulative season snapshot per player from StatLine.

    StatLine rows are season-to-date cumulative snapshots stored daily, keyed
    by (player_id, game_date, source). The LATEST row per player already
    contains the full season totals — we must NOT aggregate with SUM() as
    that would multiply every stat by the number of daily snapshots.

    Returns a dict mapping player_id -> {category_display_name: value}.
    """
    if not player_ids:
        return {}

    # Subquery: get the max game_date per player
    latest_subq = (
        select(StatLine.player_id, func.max(StatLine.game_date).label("max_date"))
        .where(StatLine.player_id.in_(player_ids))
        .group_by(StatLine.player_id)
        .subquery()
    )

    # Join to get the full StatLine rows for the latest date
    result = await session.execute(
        select(StatLine).join(
            latest_subq,
            and_(
                StatLine.player_id == latest_subq.c.player_id,
                StatLine.game_date == latest_subq.c.max_date,
            ),
        )
    )
    latest_rows = result.scalars().all()

    stats_by_player: dict[int, dict[str, float]] = {}
    for row in latest_rows:
        pid = row.player_id
        stats: dict[str, float] = {}

        if row.is_pitcher:
            ip = float(row.innings_pitched or 0)

            # Pitching counting stats — direct from the cumulative snapshot
            stats["W"] = int(row.wins or 0)
            stats["SV"] = int(row.saves or 0)
            stats["K"] = int(row.strikeouts or 0)
            stats["SO"] = int(row.strikeouts or 0)
            stats["IP"] = ip
            stats["ER"] = int(row.earned_runs or 0)
            stats["BB"] = int(row.walks or 0)

            # Rate stats — compute from components
            if ip > 0:
                stats["ERA"] = (float(row.earned_runs or 0) * 9) / ip
                stats["WHIP"] = (float(row.walks or 0) + float(row.hits or 0)) / ip
        else:
            ab = float(row.at_bats or 0)

            # Hitting counting stats
            stats["R"] = int(row.runs or 0)
            stats["HR"] = int(row.home_runs or 0)
            stats["RBI"] = int(row.rbi or 0)
            stats["SB"] = int(row.stolen_bases or 0)
            stats["H"] = int(row.hits or 0)
            stats["AB"] = int(ab)
            stats["BB"] = int(row.walks or 0)

            # Rate stats
            if ab > 0:
                stats["AVG"] = float(row.hits or 0) / ab

        if stats and any(v != 0 for v in stats.values()):
            stats_by_player[pid] = stats

    return stats_by_player


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def _fetch_team_games_next_7_days() -> dict[str, int]:
    """Count how many games each MLB team plays in the next 7 days.

    Returns a mapping of team abbreviation -> number of games. Used to
    create hitter schedule multipliers so that hitters on teams with more
    games get appropriately higher next-7-day projections.
    """
    today = date.today()
    end_date = today + timedelta(days=6)
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule?"
        f"sportId=1&startDate={today.isoformat()}&endDate={end_date.isoformat()}&hydrate=team"
    )

    games_by_team: dict[str, int] = {}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                for date_entry in data.get("dates", []):
                    for game in date_entry.get("games", []):
                        for side in ["away", "home"]:
                            team_info = game.get("teams", {}).get(side, {}).get("team", {})
                            abbr = team_info.get("abbreviation")
                            if abbr:
                                games_by_team[abbr] = games_by_team.get(abbr, 0) + 1
        except Exception as e:
            logger.warning("Failed to fetch team schedule: %s", e)

    return games_by_team


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

            statline_stats = await _load_statline_stats(session, player_ids, categories)
            has_season_data = any(any(v != 0 for v in stats.values()) for stats in statline_stats.values())

            merged_stats: dict[int, dict[str, float]] = {}
            data_sources: dict[int, list[str]] = {}

            if snapshot_type == "rest_of_season":
                # Load Steamer projections for ROS instead of using StatLine
                year = date.today().year
                proj_result = await session.execute(
                    select(ProjectionBaseline).where(
                        ProjectionBaseline.source == "steamer",
                        ProjectionBaseline.season == year
                    )
                )
                for proj in proj_result.scalars():
                    pid = proj.player_id
                    p_stats = proj.projected_stats or {}
                    
                    # Map Steamer keys to our Category keys
                    # e.g., 'SO' -> 'K', 'IP' -> 'innings_pitched'
                    mapped_stats = {}
                    for k, v in p_stats.items():
                        if k == "SO":
                            mapped_stats["K"] = float(v)
                        elif k == "IP":
                            mapped_stats["innings_pitched"] = float(v)
                        elif k == "AB":
                            mapped_stats["AB"] = float(v)
                        else:
                            try:
                                mapped_stats[k] = float(v)
                            except (ValueError, TypeError):
                                pass

                    merged_stats[pid] = mapped_stats
                    data_sources[pid] = ["steamer"]
                
                has_season_data = len(merged_stats) > 0
            else:
                for pid in player_ids:
                    sl = statline_stats.get(pid, {})

                    if has_season_data and sl and any(v != 0 for v in sl.values()):
                        merged_stats[pid] = sl
                        data_sources[pid] = ["mlb_api"]
                    else:
                        merged_stats[pid] = {}
                        data_sources[pid] = []

            # --- Pre-fetch schedule data for next_games ---
            mlb_starts: dict[int, int] = {}
            team_games: dict[str, int] = {}
            win_probs: dict[str, float] = {}
            if snapshot_type == "next_games":
                mlb_starts = await _fetch_mlb_starts(days=7)
                team_games = await _fetch_team_games_next_7_days()

                try:
                    gambling = GamblingService()
                    win_probs = await gambling.get_team_win_probabilities()
                except Exception as e:
                    logger.warning("Failed to fetch gambling odds: %s", e)

            # --- Load xwOBA/xERA from today's snapshots for signal blending ---
            today = date.today()
            xstat_result = await session.execute(
                select(
                    PlayerValueSnapshot.player_id,
                    PlayerValueSnapshot.xwoba,
                    PlayerValueSnapshot.xera,
                ).where(
                    PlayerValueSnapshot.snapshot_date == today,
                    PlayerValueSnapshot.type == "season",
                )
            )
            xstats_by_player: dict[int, tuple] = {}
            for row in xstat_result:
                if row.xwoba is not None or row.xera is not None:
                    xstats_by_player[row.player_id] = (
                        float(row.xwoba) if row.xwoba is not None else None,
                        float(row.xera) if row.xera is not None else None,
                    )

            # Average games per team in a week (used for normalization)
            avg_games = sum(team_games.values()) / len(team_games) if team_games else 6.0

            player_summaries = []
            for player_id, player in player_map.items():
                player_stats = merged_stats.get(player_id, {})
                multiplier = 1.0
                matchup_multiplier = 1.0

                if player_stats:
                    if snapshot_type == "next_games":
                        if player.is_pitcher:
                            starts = mlb_starts.get(player.mlb_id, 0) if player.mlb_id else 0
                            if player.primary_position == "SP" or "SP" in player.eligible_positions:
                                if starts >= 2:
                                    multiplier = 2.0
                                elif starts == 1:
                                    multiplier = 1.0
                                else:
                                    multiplier = 0.2 if player.primary_position == "SP" else 1.0
                            else:
                                multiplier = 1.0
                        else:
                            # Hitter schedule multiplier: scale by games/avg_games
                            if player.team_abbr and player.team_abbr in team_games:
                                team_game_count = team_games[player.team_abbr]
                                multiplier = team_game_count / avg_games
                            else:
                                multiplier = 1.0

                    if snapshot_type == "next_games" and player.team_abbr in win_probs:
                        prob = win_probs[player.team_abbr]
                        if prob >= 0.65:
                            matchup_multiplier = 1.15
                        elif prob <= 0.35:
                            matchup_multiplier = 0.85

                xstat = xstats_by_player.get(player_id, (None, None))
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
                        is_pitcher=player.is_pitcher,
                        xstat_xwoba=xstat[0],
                        xstat_xera=xstat[1],
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

            sorted_snaps = sorted(
                response.snapshots,
                key=lambda s: float(s.composite_value),
                reverse=True,
            )
            rank_map: dict[int, int] = {}
            for rank_idx, snap in enumerate(sorted_snaps, start=1):
                rank_map[snap.player_id] = rank_idx

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
