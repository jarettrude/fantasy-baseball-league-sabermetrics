"""Load real preseason data from FanGraphs via pybaseball.

Pulls full 2025 season batting and pitching stats — the most recent
complete season — and uses them as projection baselines for the
upcoming 2026 season. This gives us industry-standard advanced metrics
(wOBA, wRC+, FIP, xFIP, K/9, BB/9, etc.) instead of just raw
counting stats from the Lahman historical database.

Data sources (all free, no API key needed):
- pybaseball.batting_stats(2025) → FanGraphs batting leaderboard
- pybaseball.pitching_stats(2025) → FanGraphs pitching leaderboard
- MLB Stats API → spring training roster, injuries (via mlb_client.py)

The Lahman loader remains as the Stage 3 ID crosswalk tool. This
module replaces Lahman as the primary valuation data source.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import PlayerValueSnapshot, ProjectionBaseline
from moose_api.services.valuation_engine import (
    ComputeZScoresRequest,
    PlayerStatSummary,
    StatCategory,
    compute_z_scores,
)

logger = logging.getLogger(__name__)

# Minimum qualifiers to filter out noise
MIN_PA_BATTERS = 50  # plate appearances
MIN_IP_PITCHERS = 20.0  # innings pitched


def _fetch_batting_data(season: int = 2025) -> pd.DataFrame:
    """Fetch batting stats from FanGraphs via pybaseball."""
    from pybaseball import batting_stats

    logger.info("Fetching %d batting stats from FanGraphs...", season)
    df = batting_stats(season, qual=MIN_PA_BATTERS)
    logger.info(
        "Fetched %d batters (%d columns) from FanGraphs",
        len(df),
        len(df.columns),
    )
    return df


def _fetch_pitching_data(season: int = 2025) -> pd.DataFrame:
    """Fetch pitching stats from FanGraphs via pybaseball."""
    from pybaseball import pitching_stats

    logger.info("Fetching %d pitching stats from FanGraphs...", season)
    df = pitching_stats(season, qual=MIN_IP_PITCHERS)
    logger.info(
        "Fetched %d pitchers (%d columns) from FanGraphs",
        len(df),
        len(df.columns),
    )
    return df


def extract_batting_stats(row: pd.Series) -> dict[str, float]:
    """Extract fantasy-relevant batting stats from a FanGraphs row.

    Maps FanGraphs column names to our standard stat categories.
    """
    stats: dict[str, float] = {}
    mapping = {
        "R": "R",
        "HR": "HR",
        "RBI": "RBI",
        "SB": "SB",
        "H": "H",
        "AB": "AB",
        "AVG": "AVG",
        "OBP": "OBP",
        "SLG": "SLG",
        "BB": "BB",
        "SO": "SO",
    }
    for our_key, fg_col in mapping.items():
        val = row.get(fg_col)
        if val is not None and pd.notna(val):
            stats[our_key] = float(val)
    return stats


def extract_pitching_stats(row: pd.Series) -> dict[str, float]:
    """Extract fantasy-relevant pitching stats from a FanGraphs row."""
    stats: dict[str, float] = {}
    mapping = {
        "W": "W",
        "SV": "SV",
        "K": "SO",
        "ERA": "ERA",
        "WHIP": "WHIP",
        "IP": "IP",
    }
    for our_key, fg_col in mapping.items():
        val = row.get(fg_col)
        if val is not None and pd.notna(val):
            stats[our_key] = float(val)
    return stats


def extract_advanced_stats(
    row: pd.Series,
    is_pitcher: bool,
) -> dict[str, float]:
    """Extract advanced metrics for richer projection baselines.

    These are stored in ProjectionBaseline.projected_stats alongside
    the standard stats, giving us FIP, xFIP, wOBA, wRC+, etc.
    """
    advanced: dict[str, float] = {}
    if is_pitcher:
        for col in ["FIP", "xFIP", "K/9", "BB/9", "HR/9", "BABIP", "LOB%", "WAR", "K%", "BB%"]:
            val = row.get(col)
            if val is not None and pd.notna(val):
                advanced[col] = float(val)
    else:
        for col in ["wOBA", "wRC+", "BABIP", "ISO", "WAR", "K%", "BB%", "Hard%", "Barrel%"]:
            val = row.get(col)
            if val is not None and pd.notna(val):
                advanced[col] = float(val)
    return advanced


def _match_fg_to_player(
    fg_name: str,
    fg_team: str,
    db_players: list,
) -> Player | None:
    """Match a FanGraphs player to a DB player by name + team.

    Returns the first match. Handles common name differences.
    """
    fg_name_lower = fg_name.strip().lower()
    fg_team_upper = (fg_team or "").strip().upper()

    # First pass: exact name + team match
    for player in db_players:
        db_name_lower = player.name.strip().lower()
        db_team_upper = (player.team_abbr or "").strip().upper()
        if db_name_lower == fg_name_lower:
            if not fg_team_upper or not db_team_upper:
                return player
            if fg_team_upper == db_team_upper:
                return player

    # Second pass: name-only match (team may differ due to trades)
    for player in db_players:
        if player.name.strip().lower() == fg_name_lower:
            return player

    return None


async def run_load_fangraphs_stats(season: int = 2025):
    """Load FanGraphs stats and create projection baselines + values.

    This is the primary preseason data source. Steps:
    1. Fetch batting + pitching stats from FanGraphs via pybaseball
    2. Match FanGraphs players to our DB players by name + team
    3. Store ProjectionBaseline records (source="fangraphs_2025")
    4. Run matched players through z-score valuation engine
    5. Persist PlayerValueSnapshot records
    """
    try:
        batting_df = _fetch_batting_data(season)
        pitching_df = _fetch_pitching_data(season)

        async with async_session_factory() as session:
            league_result = await session.execute(select(League).limit(1))
            league = league_result.scalar_one_or_none()
            if not league:
                logger.warning("No league found — sync league first")
                return

            categories = [StatCategory(**cat) for cat in (league.stat_categories or [])]
            if not categories:
                logger.warning("No stat categories configured")
                return

            players_result = await session.execute(select(Player))
            all_players = players_result.scalars().all()
            if not all_players:
                logger.info("No players in DB — sync rosters first")
                return

            source_name = f"fangraphs_{season}"
            player_summaries: list[PlayerStatSummary] = []
            matched_batters = 0
            matched_pitchers = 0

            # ── Process batters ──
            for _, row in batting_df.iterrows():
                fg_name = str(row.get("Name", ""))
                fg_team = str(row.get("Team", ""))
                player = _match_fg_to_player(fg_name, fg_team, all_players)
                if not player:
                    continue

                stats = extract_batting_stats(row)
                if not stats:
                    continue

                advanced = extract_advanced_stats(row, is_pitcher=False)
                full_projection = {**stats, **advanced}

                # Upsert ProjectionBaseline
                existing = await session.execute(
                    select(ProjectionBaseline).where(
                        ProjectionBaseline.player_id == player.id,
                        ProjectionBaseline.source == source_name,
                        ProjectionBaseline.season == league.season,
                    )
                )
                baseline = existing.scalar_one_or_none()
                if baseline:
                    baseline.projected_stats = full_projection
                else:
                    baseline = ProjectionBaseline(
                        player_id=player.id,
                        source=source_name,
                        season=league.season,
                        projected_stats=full_projection,
                    )
                    session.add(baseline)

                player_summaries.append(
                    PlayerStatSummary(
                        player_id=player.id,
                        stats=stats,
                        injury_status=player.injury_status,
                    )
                )
                matched_batters += 1

            # ── Process pitchers ──
            for _, row in pitching_df.iterrows():
                fg_name = str(row.get("Name", ""))
                fg_team = str(row.get("Team", ""))
                player = _match_fg_to_player(fg_name, fg_team, all_players)
                if not player:
                    continue

                stats = extract_pitching_stats(row)
                if not stats:
                    continue

                advanced = extract_advanced_stats(row, is_pitcher=True)
                full_projection = {**stats, **advanced}

                existing = await session.execute(
                    select(ProjectionBaseline).where(
                        ProjectionBaseline.player_id == player.id,
                        ProjectionBaseline.source == source_name,
                        ProjectionBaseline.season == league.season,
                    )
                )
                baseline = existing.scalar_one_or_none()
                if baseline:
                    baseline.projected_stats = full_projection
                else:
                    baseline = ProjectionBaseline(
                        player_id=player.id,
                        source=source_name,
                        season=league.season,
                        projected_stats=full_projection,
                    )
                    session.add(baseline)

                player_summaries.append(
                    PlayerStatSummary(
                        player_id=player.id,
                        stats=stats,
                        injury_status=player.injury_status,
                    )
                )
                matched_pitchers += 1

            if not player_summaries:
                logger.info("No FanGraphs players matched DB — sync rosters first")
                return

            # ── Run through valuation engine ──
            request = ComputeZScoresRequest(
                players=player_summaries,
                categories=categories,
                snapshot_type="season",
            )
            response = compute_z_scores(request)

            today = date.today()
            for snap in response.snapshots:
                existing = await session.execute(
                    select(PlayerValueSnapshot).where(
                        PlayerValueSnapshot.player_id == snap.player_id,
                        PlayerValueSnapshot.snapshot_date == today,
                        PlayerValueSnapshot.type == "season",
                    )
                )
                existing_snap = existing.scalar_one_or_none()

                if existing_snap:
                    existing_snap.category_scores = snap.category_scores
                    existing_snap.composite_value = snap.composite_value
                    existing_snap.injury_weight = snap.injury_weight
                else:
                    new_snap = PlayerValueSnapshot(
                        player_id=snap.player_id,
                        snapshot_date=today,
                        type="season",
                        category_scores=snap.category_scores,
                        composite_value=snap.composite_value,
                        injury_weight=snap.injury_weight,
                    )
                    session.add(new_snap)

            await session.commit()

            logger.info(
                "FanGraphs preseason load complete: %d batters, %d pitchers matched, %d value snapshots created",
                matched_batters,
                matched_pitchers,
                len(response.snapshots),
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"FanGraphs {season} data loaded: "
                    f"{matched_batters} batters, "
                    f"{matched_pitchers} pitchers matched. "
                    f"{len(response.snapshots)} value snapshots "
                    f"created with advanced metrics "
                    f"(wOBA, wRC+, FIP, xFIP, etc.)"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("load_fangraphs_stats failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"FanGraphs data load failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
