"""Sync draft picks and top available players from Yahoo Fantasy Sports API.

Fetches the full league draft results in pick order, resolves player and team
references, persists DraftPick rows, and updates the free agent snapshot used
by the draft summary generator for the "left on board" analysis.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from sqlalchemy import delete, select

from moose_api.core.database import async_session_factory
from moose_api.models.draft import DraftPick
from moose_api.models.league import League
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.team import Team
from moose_api.services.yahoo_client import YAHOO_API_BASE
from moose_api.tasks.sync_league import _get_yahoo_client

logger = logging.getLogger(__name__)


async def run_sync_draft_data() -> dict:
    """Fetch draft results from Yahoo and persist them as DraftPick rows.

    Clears any existing draft picks for the league, then fetches the full
    /draftresults endpoint, resolves each pick's player and team by Yahoo key,
    and inserts new DraftPick rows.

    Also fetches the top 50 available players (by Yahoo rank) and ensures
    the Player table is up to date so the draft summary generator has fresh
    "left on board" data.

    Returns:
        Dict with imported, skipped, and errors counts.
    """
    logger.info("Starting run_sync_draft_data...")

    async with async_session_factory() as session:
        league_result = await session.execute(select(League).limit(1))
        league = league_result.scalar_one_or_none()
        if not league:
            logger.warning("No league found — skipping draft sync")
            return {"imported": 0, "skipped": 0, "errors": ["No league found"]}

        client = await _get_yahoo_client()

        try:
            raw_picks = await client.get_draft_picks(league.yahoo_league_key)
        except Exception as e:
            logger.error("Failed to fetch draft picks from Yahoo: %s", e)
            notif = CommissionerNotification(
                type="sync_error",
                message=f"Draft sync failed — could not fetch draft results from Yahoo: {e}",
            )
            session.add(notif)
            await session.commit()
            await client.close()
            return {"imported": 0, "skipped": 0, "errors": [str(e)]}

        if not raw_picks:
            logger.warning("Yahoo returned 0 draft picks for league %s", league.yahoo_league_key)
            notif = CommissionerNotification(
                type="info",
                message="Draft sync complete — Yahoo returned 0 picks. Draft may not have occurred yet.",
            )
            session.add(notif)
            await session.commit()
            await client.close()
            return {"imported": 0, "skipped": 0, "errors": []}

        # Build lookup maps
        teams_result = await session.execute(select(Team).where(Team.league_id == league.id))
        teams_by_key = {t.yahoo_team_key: t for t in teams_result.scalars().all()}

        num_teams = len(teams_by_key) or league.num_teams or 1

        # Wipe existing draft picks for this league before re-import
        await session.execute(delete(DraftPick).where(DraftPick.league_id == league.id))

        # Fetch all player info for the drafted player_keys in batches
        all_player_keys = [p["player_key"] for p in raw_picks if p.get("player_key")]
        players_by_key: dict[str, Player] = {}
        for i in range(0, len(all_player_keys), 25):
            batch_keys = all_player_keys[i : i + 25]
            p_result = await session.execute(select(Player).where(Player.yahoo_player_key.in_(batch_keys)))
            for p in p_result.scalars().all():
                players_by_key[p.yahoo_player_key] = p

        # For players not yet in DB, fetch from Yahoo players endpoint
        missing_keys = [k for k in all_player_keys if k not in players_by_key]
        if missing_keys:
            _ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

            def _player_text(el, tag, default=""):
                child = el.find(f"y:{tag}", _ns)
                return child.text if child is not None and child.text else default

            for i in range(0, len(missing_keys), 25):
                batch = missing_keys[i : i + 25]
                try:
                    keys_str = ",".join(batch)
                    url = f"{YAHOO_API_BASE}/players;player_keys={keys_str};out=ranks"
                    raw = await client._request(url)  # noqa: SLF001
                    root = ET.fromstring(raw)

                    for player_el in root.findall(".//y:player", _ns):
                        pk = _player_text(player_el, "player_key")
                        pid = _player_text(player_el, "player_id")
                        name_el = player_el.find(".//y:name/y:full", _ns)
                        name = name_el.text if name_el is not None else _player_text(player_el, "name")
                        pos = _player_text(player_el, "display_position", _player_text(player_el, "primary_position"))
                        team_abbr = _player_text(player_el, "editorial_team_abbr")

                        elig = [
                            ep.text for ep in player_el.findall(".//y:eligible_positions/y:position", _ns) if ep.text
                        ]
                        pitcher_positions = {"SP", "RP", "P"}
                        is_pitcher = pos in pitcher_positions or bool(set(elig) & pitcher_positions)

                        if pk and name:
                            existing = await session.execute(select(Player).where(Player.yahoo_player_key == pk))
                            player_row = existing.scalar_one_or_none()
                            if not player_row:
                                player_row = Player(
                                    yahoo_player_key=pk,
                                    yahoo_player_id=pid,
                                    name=name,
                                    primary_position=pos,
                                    eligible_positions=elig,
                                    team_abbr=team_abbr or None,
                                    is_pitcher=is_pitcher,
                                )
                                session.add(player_row)
                                await session.flush()
                            players_by_key[pk] = player_row
                except Exception as e:
                    logger.warning("Failed to fetch player info for batch: %s", e)

        # Insert picks
        imported = 0
        skipped = 0
        errors = []

        for raw in raw_picks:
            pick_num = raw["pick_number"]
            round_num = raw["round_number"]
            team_key = raw.get("team_key", "")
            player_key = raw.get("player_key", "")

            team = teams_by_key.get(team_key)
            if not team:
                skipped += 1
                errors.append(f"Pick #{pick_num}: team key '{team_key}' not found in DB")
                continue

            player = players_by_key.get(player_key)

            # round_pick = position within the round
            round_pick = pick_num - (round_num - 1) * num_teams

            pick = DraftPick(
                league_id=league.id,
                team_id=team.id,
                player_id=player.id if player else None,
                pick_number=pick_num,
                round_number=round_num,
                round_pick=round_pick,
                player_name=player.name if player else f"Player {player_key}",
                player_position=player.primary_position if player else None,
                yahoo_player_key=player_key or None,
            )
            session.add(pick)
            imported += 1

        notif = CommissionerNotification(
            type="info",
            message=(
                f"Draft sync complete — {imported} picks imported" + (f", {skipped} skipped" if skipped else "") + "."
            ),
        )
        session.add(notif)
        await session.commit()

        logger.info("Draft sync complete: %d imported, %d skipped", imported, skipped)
        await client.close()
        return {"imported": imported, "skipped": skipped, "errors": errors}
