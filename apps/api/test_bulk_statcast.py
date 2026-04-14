"""Test bulk Statcast fetching with corrected aggregation logic."""

import asyncio
import logging
from datetime import date

from moose_api.services.statcast_client import StatcastClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_bulk_statcast():
    """Test bulk Statcast fetching for 2026 season."""
    try:
        statcast_client = StatcastClient()
        season = 2026
        start_dt = f"{season}-03-01"
        end_dt = f"{season}-12-31"

        logger.info(f"Testing bulk Statcast fetch for season {season}")

        # Fetch hitting data
        logger.info("Fetching hitting data...")
        hitting_data = await statcast_client.fetch_bulk_stats(start_dt, end_dt, "hitting")
        logger.info(f"Fetched hitting data for {len(hitting_data)} players")

        # Fetch pitching data
        logger.info("Fetching pitching data...")
        pitching_data = await statcast_client.fetch_bulk_stats(start_dt, end_dt, "pitching")
        logger.info(f"Fetched pitching data for {len(pitching_data)} players")

        # Merge data
        bulk_data = {**hitting_data, **pitching_data}
        logger.info(f"Total unique players: {len(bulk_data)}")

        # Sample some players
        sample_player_ids = list(bulk_data.keys())[:5]
        logger.info(f"Sample player IDs: {sample_player_ids}")

        for player_id in sample_player_ids:
            stats = bulk_data[player_id]
            logger.info(f"\nPlayer {player_id}:")
            logger.info(f"  is_pitcher: {stats.get('is_pitcher')}")
            logger.info(f"  games: {stats.get('games')}")
            logger.info(f"  at_bats: {stats.get('at_bats')}")
            logger.info(f"  hits: {stats.get('hits')}")
            logger.info(f"  home_runs: {stats.get('home_runs')}")
            logger.info(f"  strikeouts: {stats.get('strikeouts')}")
            logger.info(f"  walks: {stats.get('walks')}")
            logger.info(f"  avg: {stats.get('avg')}")

        await statcast_client.close()
        logger.info("Test completed successfully")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_bulk_statcast())
