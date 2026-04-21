"""Load live season stats from the MLB Stats API.

Populates the ``StatLine`` table with season-to-date hitting and pitching
statistics for every player in the database. The job is the primary feeder
for daily z-score / value computations after FanGraphs was retired.

Design goals (post-FanGraphs pivot):

* **Fast**: use the league-wide ``/stats?stats=season`` endpoint so the
  entire league is fetched in 2 paginated calls (hitting + pitching)
  instead of thousands of per-player round-trips. A full run now takes
  seconds rather than 3+ hours.
* **Self-healing**: if the bulk endpoint fails or returns a small set,
  missing players fall back to per-player ``/people/{id}/stats`` calls,
  bounded by a hard cap and honoring the shared MLB rate-limit semaphore.
* **Idempotent**: rows are UPSERTed (``ON CONFLICT (player_id, game_date,
  source) DO UPDATE``) so a crash mid-run never leaves the table empty,
  and re-running the job simply refreshes today's values.
* **Robust**: any failure mode still produces a ``CommissionerNotification``
  so partial results are observable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import StatLine
from moose_api.services.mlb_client import MLBClient

logger = logging.getLogger(__name__)

# Upper bound on how many players we'll attempt to backfill via the slow
# per-player endpoint when the bulk league feed is missing them. Prevents
# the tail of inactive/minor-league players from dragging a run out for
# hours. The bulk feed already covers every player with any 2026 MLB PA.
MISSING_FALLBACK_MAX = int(os.getenv("LIVE_STATS_FALLBACK_MAX", "300"))


def _num(value: Any) -> float | int | None:
    """Coerce an MLB Stats API stat value to a clean numeric or None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    return None


def _build_hitting_row(player_id: int, stat: dict, today: date) -> dict:
    return {
        "player_id": player_id,
        "game_date": today,
        "source": "mlb_api",
        "is_pitcher": False,
        "runs": _num(stat.get("runs")) or 0,
        "home_runs": _num(stat.get("homeRuns")) or 0,
        "rbi": _num(stat.get("rbi")) or 0,
        "stolen_bases": _num(stat.get("stolenBases")) or 0,
        "hits": _num(stat.get("hits")) or 0,
        "at_bats": _num(stat.get("atBats")) or 0,
        "batting_avg": _num(stat.get("avg")),
        "wins": 0,
        "saves": 0,
        "strikeouts": _num(stat.get("strikeOuts")) or 0,
        "innings_pitched": 0,
        "earned_runs": 0,
        "walks": _num(stat.get("baseOnBalls")) or 0,
        "era": None,
        "whip": None,
    }


def _build_pitching_row(player_id: int, stat: dict, today: date) -> dict:
    return {
        "player_id": player_id,
        "game_date": today,
        "source": "mlb_api",
        "is_pitcher": True,
        "runs": 0,
        "home_runs": 0,
        "rbi": 0,
        "stolen_bases": 0,
        "hits": 0,
        "at_bats": 0,
        "batting_avg": None,
        "wins": _num(stat.get("wins")) or 0,
        "saves": _num(stat.get("saves")) or 0,
        "strikeouts": _num(stat.get("strikeOuts")) or 0,
        "innings_pitched": _num(stat.get("inningsPitched")) or 0,
        "earned_runs": _num(stat.get("earnedRuns")) or 0,
        "walks": _num(stat.get("baseOnBalls")) or 0,
        "era": _num(stat.get("era")),
        "whip": _num(stat.get("whip")),
    }


def _row_for_player(
    player: Player,
    hitting: dict[int, dict],
    pitching: dict[int, dict],
    today: date,
) -> dict | None:
    """Pick the best bulk stat line for a player, or None if absent."""
    mlb_id = player.mlb_id
    if not mlb_id:
        return None

    in_pitch = mlb_id in pitching
    in_hit = mlb_id in hitting

    # Two-way / ambiguous: honor the roster flag first, then whichever
    # bulk feed actually has this player.
    if player.is_pitcher and in_pitch:
        return _build_pitching_row(player.id, pitching[mlb_id], today)
    if in_hit:
        return _build_hitting_row(player.id, hitting[mlb_id], today)
    if in_pitch:
        return _build_pitching_row(player.id, pitching[mlb_id], today)
    return None


async def _fetch_player_fallback(mlb_client: MLBClient, player: Player, season: int, today: date) -> dict | None:
    """Per-player fallback when bulk feeds don't contain this player."""
    if not player.mlb_id:
        return None

    # Preferred group first to minimize the common case to one API call.
    groups = ("pitching", "hitting") if player.is_pitcher else ("hitting", "pitching")

    for group in groups:
        try:
            payload = await mlb_client.get_player_stats(player.mlb_id, season, group)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Per-player %s stats failed for %s (mlb_id=%s): %s",
                group,
                player.name,
                player.mlb_id,
                exc,
            )
            continue

        if not payload:
            continue
        for stat_entry in payload.get("stats", []) or []:
            splits = stat_entry.get("splits") or []
            if not splits:
                continue
            stat = splits[0].get("stat") or {}
            if not stat:
                continue
            if group == "pitching":
                return _build_pitching_row(player.id, stat, today)
            return _build_hitting_row(player.id, stat, today)

    return None


async def run_load_live_season_stats():
    """Entrypoint for the ``load_live_season_stats_job`` arq task."""
    mlb_client: MLBClient | None = None
    try:
        mlb_client = MLBClient()
        season = date.today().year
        today = date.today()

        # --- 1. Bulk league-wide fetch (two parallel paginated requests) ---
        logger.info("Fetching bulk season stats from MLB Stats API (season=%d)", season)
        hitting_task = asyncio.create_task(mlb_client.get_bulk_season_stats(season, "hitting"))
        pitching_task = asyncio.create_task(mlb_client.get_bulk_season_stats(season, "pitching"))
        hitting_bulk, pitching_bulk = await asyncio.gather(hitting_task, pitching_task, return_exceptions=False)
        logger.info(
            "Bulk fetch done: %d hitters, %d pitchers",
            len(hitting_bulk),
            len(pitching_bulk),
        )

        # --- 2. Load roster & assemble rows ---
        async with async_session_factory() as session:
            players_result = await session.execute(select(Player).where(Player.mlb_id.isnot(None)))
            players = list(players_result.scalars().all())

            if not players:
                logger.warning("No players with MLB IDs found; nothing to sync")
                return

            rows: list[dict] = []
            missing: list[Player] = []
            for p in players:
                row = _row_for_player(p, hitting_bulk, pitching_bulk, today)
                if row is not None:
                    rows.append(row)
                else:
                    missing.append(p)

            bulk_hits = len(rows)
            logger.info(
                "Bulk-matched %d/%d players; %d candidates for per-player fallback",
                bulk_hits,
                len(players),
                len(missing),
            )

            # --- 3. Bounded fallback for players missing from bulk feeds ---
            # The shared MLB_API_MAX_CONCURRENCY semaphore inside MLBClient
            # already caps real in-flight requests; no extra gather limit needed.
            #
            # Only skip fallback on a *total* bulk outage (both feeds empty).
            # If just one feed failed, we still want to backfill the affected
            # class of players (e.g. all pitchers when pitching_bulk is empty)
            # via per-player queries rather than silently dropping them.
            fallback_hits = 0
            total_outage = not hitting_bulk and not pitching_bulk
            if missing and total_outage:
                logger.warning(
                    "Both bulk feeds empty; skipping per-player fallback for %d players to avoid long runtime",
                    len(missing),
                )
            elif missing:
                if not hitting_bulk or not pitching_bulk:
                    logger.warning(
                        "Partial bulk outage (hitting=%d, pitching=%d); backfilling via per-player fallback",
                        len(hitting_bulk),
                        len(pitching_bulk),
                    )
                capped = missing[:MISSING_FALLBACK_MAX]
                if len(missing) > MISSING_FALLBACK_MAX:
                    logger.info(
                        "Capping fallback at %d of %d missing players (env LIVE_STATS_FALLBACK_MAX)",
                        MISSING_FALLBACK_MAX,
                        len(missing),
                    )
                fallback_results = await asyncio.gather(
                    *(_fetch_player_fallback(mlb_client, p, season, today) for p in capped),
                    return_exceptions=False,
                )
                for row in fallback_results:
                    if row is not None:
                        rows.append(row)
                        fallback_hits += 1

            # --- 4. Idempotent upsert of today's rows ---
            if rows:
                stmt = pg_insert(StatLine).values(rows)
                update_cols = {
                    c.name: stmt.excluded[c.name]
                    for c in StatLine.__table__.columns
                    if c.name not in ("id", "player_id", "game_date", "source")
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=["player_id", "game_date", "source"],
                    set_=update_cols,
                )
                await session.execute(stmt)
                await session.commit()

            total = len(players)
            no_data = total - len(rows)
            logger.info(
                "Live season stats upserted: %d rows (%d bulk, %d fallback, %d without data) / %d players",
                len(rows),
                bulk_hits,
                fallback_hits,
                no_data,
                total,
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"Live season stats loaded: {len(rows)} rows upserted "
                    f"({bulk_hits} bulk + {fallback_hits} fallback) for "
                    f"{total} players, {no_data} without data (season {season})"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as exc:
        logger.error("load_live_season_stats failed: %s", exc, exc_info=True)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Live season stats load failed: {str(exc)[:500]}",
            )
            session.add(notif)
            await session.commit()
        raise
    finally:
        if mlb_client:
            await mlb_client.close()
