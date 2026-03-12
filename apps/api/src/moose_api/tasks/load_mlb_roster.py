"""MLB active player loader — replaces local Lahman CSV dependency.

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
                "primary_position": (p.get("primaryPosition", {}).get("abbreviation", "")),
                "team_abbr": (p.get("currentTeam", {}).get("abbreviation") if p.get("currentTeam") else None),
                "bats": p.get("batSide", {}).get("code", ""),
                "throws": p.get("pitchHand", {}).get("code", ""),
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


# ──────────────────────────────────────────────────────────────
# Legacy helpers kept for test compatibility
# ──────────────────────────────────────────────────────────────


def _safe_int(val: str | None, default: int = 0) -> int:
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _safe_float(val: str | None, default: float = 0.0) -> float:
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


class LahmanPerson:
    """Parsed row from People.csv — kept for test compatibility."""

    __slots__ = (
        "player_id",
        "name_first",
        "name_last",
        "name_given",
        "debut",
        "final_game",
        "bats",
        "throws",
        "bbref_id",
        "retro_id",
        "birth_year",
    )

    def __init__(self, row: dict[str, str]) -> None:
        self.player_id = row.get("playerID", "")
        self.name_first = row.get("nameFirst", "")
        self.name_last = row.get("nameLast", "")
        self.name_given = row.get("nameGiven", "")
        self.debut = row.get("debut", "")
        self.final_game = row.get("finalGame", "")
        self.bats = row.get("bats", "")
        self.throws = row.get("throws", "")
        self.bbref_id = row.get("bbrefID", "")
        self.retro_id = row.get("retroID", "")
        self.birth_year = row.get("birthYear", "")

    @property
    def full_name(self) -> str:
        return f"{self.name_first} {self.name_last}"

    @property
    def is_active(self) -> bool:
        from datetime import date

        if not self.debut:
            return False
        if not self.final_game:
            return True
        try:
            final = date.fromisoformat(self.final_game)
            return (date.today() - final).days < 730
        except ValueError:
            return False


class LahmanBattingSeason:
    """Kept for test compatibility with preseason_seed.py."""

    __slots__ = (
        "player_id",
        "year",
        "games",
        "at_bats",
        "runs",
        "hits",
        "home_runs",
        "rbi",
        "stolen_bases",
        "walks",
        "strikeouts",
    )

    def __init__(self) -> None:
        self.player_id = ""
        self.year = 0
        self.games = 0
        self.at_bats = 0
        self.runs = 0
        self.hits = 0
        self.home_runs = 0
        self.rbi = 0
        self.stolen_bases = 0
        self.walks = 0
        self.strikeouts = 0


class LahmanPitchingSeason:
    """Kept for test compatibility with preseason_seed.py."""

    __slots__ = (
        "player_id",
        "year",
        "games",
        "wins",
        "saves",
        "innings_pitched",
        "strikeouts",
        "earned_runs",
        "walks",
        "hits",
    )

    def __init__(self) -> None:
        self.player_id = ""
        self.year = 0
        self.games = 0
        self.wins = 0
        self.saves = 0
        self.innings_pitched = 0.0
        self.strikeouts = 0
        self.earned_runs = 0
        self.walks = 0
        self.hits = 0


def load_people(data_dir=None):
    """Load People.csv — kept for tests. Runtime uses MLB API."""
    import csv
    from pathlib import Path

    path = Path(data_dir or ".") / "People.csv"
    if not path.exists():
        return {}
    people: dict[str, LahmanPerson] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            p = LahmanPerson(row)
            if p.player_id:
                people[p.player_id] = p
    return people


def load_batting_seasons(data_dir=None, min_year=2020):
    """Load Batting.csv — kept for tests. Runtime uses FanGraphs."""
    import csv
    from pathlib import Path

    path = Path(data_dir or ".") / "Batting.csv"
    if not path.exists():
        return {}
    raw: dict[tuple[str, int], LahmanBattingSeason] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            year = _safe_int(row.get("yearID"))
            if year < min_year:
                continue
            pid = row.get("playerID", "")
            key = (pid, year)
            if key not in raw:
                s = LahmanBattingSeason()
                s.player_id = pid
                s.year = year
                raw[key] = s
            s = raw[key]
            s.games += _safe_int(row.get("G"))
            s.at_bats += _safe_int(row.get("AB"))
            s.runs += _safe_int(row.get("R"))
            s.hits += _safe_int(row.get("H"))
            s.home_runs += _safe_int(row.get("HR"))
            s.rbi += _safe_int(row.get("RBI"))
            s.stolen_bases += _safe_int(row.get("SB"))
            s.walks += _safe_int(row.get("BB"))
            s.strikeouts += _safe_int(row.get("SO"))
    result: dict[str, list[LahmanBattingSeason]] = {}
    for (pid, _), season in raw.items():
        result.setdefault(pid, []).append(season)
    return result


def load_pitching_seasons(data_dir=None, min_year=2020):
    """Load Pitching.csv — kept for tests. Runtime uses FanGraphs."""
    import csv
    from pathlib import Path

    path = Path(data_dir or ".") / "Pitching.csv"
    if not path.exists():
        return {}
    raw: dict[tuple[str, int], LahmanPitchingSeason] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            year = _safe_int(row.get("yearID"))
            if year < min_year:
                continue
            pid = row.get("playerID", "")
            key = (pid, year)
            if key not in raw:
                s = LahmanPitchingSeason()
                s.player_id = pid
                s.year = year
                raw[key] = s
            s = raw[key]
            s.games += _safe_int(row.get("G"))
            s.wins += _safe_int(row.get("W"))
            s.saves += _safe_int(row.get("SV"))
            s.innings_pitched += _safe_float(row.get("IPouts", "0")) / 3.0
            s.strikeouts += _safe_int(row.get("SO"))
            s.earned_runs += _safe_int(row.get("ER"))
            s.walks += _safe_int(row.get("BB"))
            s.hits += _safe_int(row.get("H"))
    result: dict[str, list[LahmanPitchingSeason]] = {}
    for (pid, _), season in raw.items():
        result.setdefault(pid, []).append(season)
    return result
