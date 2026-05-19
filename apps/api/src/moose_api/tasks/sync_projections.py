"""Sync Steamer projections from Fangraphs into ProjectionBaseline.

Hits the Fangraphs JSON API to pull Steamer rest-of-season or full-season
projections for batters and pitchers, then UPSERTs them into the
projection_baseline table. Matches players via xMLBAMID (MLB ID).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player
from moose_api.models.stats import ProjectionBaseline

logger = logging.getLogger(__name__)

FANGRAPHS_PROJECTIONS_URL = "https://www.fangraphs.com/api/projections"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


async def _fetch_projections(pos: str, stats: str) -> list[dict]:
    url = f"{FANGRAPHS_PROJECTIONS_URL}?pos={pos}&stats={stats}&type=steamer"
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.get(url, headers=HEADERS)
        res.raise_for_status()
        return res.json()


async def run_sync_projections():
    """Load Steamer hitting and pitching projections."""
    logger.info("Starting Steamer projections sync from Fangraphs API...")

    try:
        # Fetch both hitting and pitching
        batters = await _fetch_projections("all", "bat")
        pitchers = await _fetch_projections("all", "pit")

        logger.info(f"Fetched {len(batters)} batters and {len(pitchers)} pitchers from Fangraphs.")

        # Combine, prioritizing pitching stats if a player has both
        # Actually, let's keep them separate but process all. Some pitchers hit.
        proj_by_mlb_id: dict[int, dict] = {}
        
        for b in batters:
            mlb_id_str = b.get("xMLBAMID")
            if not mlb_id_str:
                continue
            try:
                mlb_id = int(mlb_id_str)
                proj_by_mlb_id[mlb_id] = b
            except ValueError:
                pass

        for p in pitchers:
            mlb_id_str = p.get("xMLBAMID")
            if not mlb_id_str:
                continue
            try:
                mlb_id = int(mlb_id_str)
                # Overwrite batter stats with pitcher stats for pitchers
                proj_by_mlb_id[mlb_id] = p
            except ValueError:
                pass

        if not proj_by_mlb_id:
            logger.warning("No projections found with MLB IDs.")
            return

        async with async_session_factory() as session:
            players = await session.execute(
                select(Player.id, Player.mlb_id).where(Player.mlb_id.is_not(None))
            )
            
            rows_to_upsert = []
            season = date.today().year
            
            for player_id, mlb_id in players:
                if mlb_id in proj_by_mlb_id:
                    stats = proj_by_mlb_id[mlb_id]
                    rows_to_upsert.append({
                        "player_id": player_id,
                        "source": "steamer",
                        "season": season,
                        "projected_stats": stats
                    })

            if rows_to_upsert:
                stmt = pg_insert(ProjectionBaseline).values(rows_to_upsert)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["player_id", "source", "season"],
                    set_={"projected_stats": stmt.excluded.projected_stats}
                )
                await session.execute(stmt)
                await session.commit()

            logger.info(f"Successfully UPSERTed {len(rows_to_upsert)} Steamer projections.")

            notif = CommissionerNotification(
                type="info",
                message=f"Steamer projection sync complete. Updated {len(rows_to_upsert)} players.",
            )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error(f"Failed to sync projections: {e}")
        async with async_session_factory() as session:
            notif = CommissionerNotification(
                type="sync_failure",
                message=f"Steamer projection sync failed: {e}",
            )
            session.add(notif)
            await session.commit()
        raise

if __name__ == "__main__":
    asyncio.run(run_sync_projections())
