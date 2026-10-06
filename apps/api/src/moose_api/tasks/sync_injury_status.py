"""Player injury status synchronization.

Fetches injury information from Yahoo Fantasy Sports API and updates
player records with current injury status and notes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.services.mlb_client import MLBClient

logger = logging.getLogger(__name__)

# Map MLB injury type strings to our internal codes
_MLB_INJURY_MAP = {
    "10-Day IL": "IL10",
    "15-Day IL": "IL10",
    "60-Day IL": "IL60",
    "Day-to-Day": "DTD",
    "Suspended": "OUT",
    "Bereavement": "OUT",
    "Paternity": "OUT",
}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((httpx.ReadError, httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
async def _fetch_injuries(mlb: MLBClient):
    try:
        return await mlb.get_injuries()
    except Exception as e:
        logger.warning(f"MLB API fetch attempt failed: {e}")
        raise


async def run_sync_injury_status():
    """Sync player injury status from MLB Stats API IL/transaction data."""
    try:
        mlb = MLBClient()
        try:
            injuries = await _fetch_injuries(mlb)
        finally:
            await mlb.close()

        if not injuries:
            logger.info("No injury data returned from MLB Stats API")
            return

        injury_by_mlb_id: dict[int, tuple[str, str | None]] = {}
        injury_by_name: dict[str, tuple[str, str | None]] = {}
        for entry in injuries:
            status_str = entry.injury_status or "UNKNOWN"
            internal_status = _MLB_INJURY_MAP.get(status_str, "UNKNOWN")
            info = (internal_status, entry.injury_description)

            if entry.player_id:
                injury_by_mlb_id[entry.player_id] = info
            name = entry.full_name.strip().lower()
            if name:
                injury_by_name[name] = info

        logger.info(
            "Injury data fetched: %d entries (%d by MLB ID, %d by name)",
            len(injuries),
            len(injury_by_mlb_id),
            len(injury_by_name),
        )

        async with async_session_factory() as session:
            players_result = await session.execute(select(Player))
            players = players_result.scalars().all()

            updated = 0
            matched_by_id = 0
            matched_by_name = 0
            for player in players:
                injury_info = None

                if player.mlb_id and player.mlb_id in injury_by_mlb_id:
                    injury_info = injury_by_mlb_id[player.mlb_id]
                    matched_by_id += 1
                else:
                    key = player.name.strip().lower()
                    if key in injury_by_name:
                        injury_info = injury_by_name[key]
                        matched_by_name += 1

                if injury_info:
                    new_status, note = injury_info
                    if player.injury_status != new_status:
                        player.injury_status = new_status
                        player.injury_note = note
                        player.injury_updated_at = datetime.now(UTC)
                        updated += 1
                else:
                    if player.injury_status is not None:
                        player.injury_status = None
                        player.injury_note = None
                        player.injury_updated_at = datetime.now(UTC)
                        updated += 1

            await session.commit()
            logger.info(
                "Injury status sync complete: %d updated (%d matched by MLB ID, %d by name, %d total players)",
                updated,
                matched_by_id,
                matched_by_name,
                len(players),
            )

            notif = CommissionerNotification(
                type="info",
                message=(f"Injury status sync complete: {updated} players updated out of {len(players)} total"),
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_injury_status failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Injury status sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
