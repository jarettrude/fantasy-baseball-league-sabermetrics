"""Scraper/Sync for RotoWire Injury data.

Uses a reverse-engineered JSON endpoint to fetch injury reports and
leverages Gemini LLM to estimate precisely how many games a player
will miss in the next 7 days.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player

logger = logging.getLogger(__name__)

ROTOWIRE_INJURY_URL = "https://www.rotowire.com/baseball/tables/injury-report.php"

# Community best practice: realistic browser headers + Referer to pass WAF
_ROTOWIRE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.rotowire.com/baseball/injury-report.php",
    "X-Requested-With": "XMLHttpRequest",
}


async def _estimate_missed_games(note: str, status: str) -> int:
    """Estimate missed games based on the structured status.
    Rotowire hides the actual return date behind a paywall, so an LLM
    can't help us here. We use simple heuristics to protect our free API quotas.
    """
    s = status.lower()
    if s in ["out", "60-day il", "il60"]:
        return 7
    if s in ["15-day il", "il15"]:
        return 6
    if s in ["10-day il", "il10"]:
        return 5
    if s == "day-to-day":
        return 2

    return 1


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((httpx.ReadError, httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True,
)
async def _fetch_rotowire_injuries() -> list[dict]:
    """Fetch RotoWire injury data with retries and caching."""
    from moose_api.core.redis import get_cached, set_cached

    cache_key = "rotowire:injuries:data"

    cached = await get_cached(cache_key)
    if cached:
        logger.info("Using cached RotoWire injury data")
        return cached

    params = {"team": "ALL", "pos": "ALL"}

    # Polite delay before hitting a non-official API
    await asyncio.sleep(2.0)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            ROTOWIRE_INJURY_URL,
            params=params,
            headers=_ROTOWIRE_HEADERS,
        )

        if resp.status_code == 403:
            logger.warning("RotoWire returned 403 Forbidden — WAF may be blocking.")
            resp.raise_for_status()

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "30")
            logger.warning("RotoWire rate limited (429), sleeping %ss", retry_after)
            await asyncio.sleep(float(retry_after))
            resp.raise_for_status()

        resp.raise_for_status()
        injury_data = resp.json()

    # Cache for 12 hours
    await set_cached(cache_key, injury_data, 43200)
    return injury_data


async def run_sync_rotowire_injuries():
    """Main task to sync RotoWire injuries and calculate granular weights."""
    try:
        injury_data = await _fetch_rotowire_injuries()

        # Map by player name (lowercase)
        injury_map = {item["player"].lower(): item for item in injury_data}

        async with async_session_factory() as session:
            players_res = await session.execute(select(Player))
            players = players_res.scalars().all()

            updates = 0
            # We'll batch LLM calls to prevent extreme delays or rate limits
            # but for 300 injuries, we should be okay with a small delay
            for p in players:
                name_key = p.name.lower()
                if name_key in injury_map:
                    item = injury_map[name_key]
                    note = item.get("injury", "")
                    status = item.get("status", "")

                    # Store standard fields
                    p.injury_status = status
                    p.injury_note = note
                    p.injury_updated_at = datetime.now(UTC)

                    # Calculate granular missed games
                    p.missed_games_count = await _estimate_missed_games(note, status)
                    updates += 1
                else:
                    # Healthy check
                    if p.injury_status is not None:
                        p.injury_status = None
                        p.injury_note = None
                        p.missed_games_count = 0
                        p.injury_updated_at = datetime.now(UTC)
                        updates += 1

            await session.commit()
            logger.info("RotoWire injury sync complete. Updated %d players.", updates)

            notif = CommissionerNotification(
                type="info",
                message=f"RotoWire Injury sync complete: {updates} players updated with granular weights.",
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("sync_rotowire_injuries failed: %s", e)
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"RotoWire injury sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise
