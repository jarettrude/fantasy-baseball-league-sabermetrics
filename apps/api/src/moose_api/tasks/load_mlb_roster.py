"""MLB active player loader.

Loads all active MLB players from the MLB Stats API, giving us:
- MLB ID (primary identifier for cross-referencing)
- Full name, first name, last name
- Primary position
- Current team abbreviation
- Bat side, throw hand

This data serves two purposes:
1. Stage 3 of the 5-stage Player ID Mapping Pipeline (spec §8)
   — matches Yahoo players to MLB IDs via name + team + position
2. Enriches Player records with bats/throws data

Data source: https://statsapi.mlb.com/api/v1/sports/1/players
No API key required. Always returns current active roster.

The local Lahman CSVs in /data are retained as offline test fixtures
but are no longer needed for runtime operation.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification

logger = logging.getLogger(__name__)

MLB_PLAYERS_URL = "https://statsapi.mlb.com/api/v1/sports/1/players"


async def fetch_active_mlb_players(
    season: int = 2025,
) -> list[dict[str, Any]]:
    """Fetch all active MLB players from the Stats API.

    Returns a list of dicts with normalized fields:
    - mlb_id (int)
    - full_name (str)
    - first_name, last_name (str)
    - primary_position (str, e.g. "P", "SS", "OF")
    - team_abbr (str or None)
    - bats (str, e.g. "R", "L", "S")
    - throws (str, e.g. "R", "L")
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            MLB_PLAYERS_URL,
            params={"season": season},
        )
        if resp.status_code != 200:
            logger.error("MLB players API returned %d", resp.status_code)
            return []

    data = resp.json()
    raw_players = data.get("people", [])
    logger.info(
        "Fetched %d active MLB players from Stats API (season=%d)",
        len(raw_players),
        season,
    )

    players: list[dict[str, Any]] = []
    for p in raw_players:
        players.append(
            {
                "mlb_id": p.get("id"),
                "full_name": p.get("fullName", ""),
                "first_name": p.get("firstName", ""),
                "last_name": p.get("lastName", ""),
                "primary_position": (p.get("primaryPosition") or {}).get("abbreviation", ""),
                "team_abbr": (p.get("currentTeam") or {}).get("abbreviation") if p.get("currentTeam") else None,
                "bats": (p.get("batSide") or {}).get("code", ""),
                "throws": (p.get("pitchHand") or {}).get("code", ""),
            }
        )

    return players


def get_active_lahman_players(
    data_dir=None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper — now uses cached API data.

    Falls back to local CSVs if available and API data isn't cached.
    This function is called by resolve_mappings.py Stage 3.
    """
    # If called synchronously (from tests), try local CSVs
    import csv
    from pathlib import Path

    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[5] / "data"

    csv_path = Path(data_dir) / "People.csv"
    if not csv_path.exists():
        logger.info("No local Lahman CSVs — use fetch_active_mlb_players() instead")
        return []

    active = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            debut = row.get("debut", "")
            final = row.get("finalGame", "")
            if debut and not final:
                active.append(
                    {
                        "lahman_id": row.get("playerID", ""),
                        "full_name": (f"{row.get('nameFirst', '')} {row.get('nameLast', '')}"),
                        "name_first": row.get("nameFirst", ""),
                        "name_last": row.get("nameLast", ""),
                        "bats": row.get("bats", ""),
                        "throws": row.get("throws", ""),
                        "bbref_id": row.get("bbrefID", ""),
                    }
                )
    return active


async def run_load_mlb_roster():
    """Load active MLB players from the API and cross-reference.

    Replaces the old CSV-based Lahman loader. Uses the MLB Stats API
    to fetch all active players, then matches them to our DB players
    by name to set mlb_id, bats, and throws.
    """
    try:
        mlb_players = await fetch_active_mlb_players()
        if not mlb_players:
            logger.warning("No players returned from MLB API — skipping")
            return

        async with async_session_factory() as session:
            from moose_api.models.player import Player

            all_players_result = await session.execute(select(Player))
            db_players = all_players_result.scalars().all()

            if not db_players:
                logger.info("No players in DB yet — load skipped")
                return

            # Build lookup by normalized name
            mlb_by_name: dict[str, dict] = {}
            for p in mlb_players:
                key = p["full_name"].strip().lower()
                mlb_by_name[key] = p

            matched = 0
            for player in db_players:
                name_key = player.name.strip().lower()
                if name_key in mlb_by_name:
                    mlb_p = mlb_by_name[name_key]
                    if not player.mlb_id:
                        player.mlb_id = mlb_p["mlb_id"]
                    if mlb_p.get("bats") and not player.bats:
                        player.bats = mlb_p["bats"]
                    if mlb_p.get("throws") and not player.throws:
                        player.throws = mlb_p["throws"]
                    matched += 1

            await session.commit()
            logger.info(
                "MLB player load complete: matched %d / %d DB players from %d active MLB players",
                matched,
                len(db_players),
                len(mlb_players),
            )

            notif = CommissionerNotification(
                type="info",
                message=(
                    f"MLB player data loaded: {len(mlb_players)} active "
                    f"players from API, {matched}/{len(db_players)} "
                    f"matched by name"
                ),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("load_mlb_players failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"MLB player data load failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
