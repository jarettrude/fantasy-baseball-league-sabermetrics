"""Baseball Savant Statcast API client for fast bulk player statistics.

Uses pybaseball to fetch Statcast data from Baseball Savant, which is significantly
faster than the MLB Stats API for bulk data retrieval. Serves as primary data source
with fallback to MLB Stats API.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)

try:
    from pybaseball import statcast, statcast_batter, statcast_pitcher
except ImportError:
    logger.error("pybaseball not installed. Run: pip install pybaseball")
    statcast = None
    statcast_batter = None
    statcast_pitcher = None


class StatcastClient:
    """Client for Baseball Savant Statcast API."""

    def __init__(self):
        """Initialize Statcast client."""
        if statcast is None:
            raise ImportError("pybaseball is required but not installed")

    async def fetch_player_stats(
        self, mlb_id: int, season: int, stat_type: str = "hitting"
    ) -> dict | None:
        """Fetch season stats for a specific player.

        Args:
            mlb_id: MLB player ID
            season: Season year
            stat_type: 'hitting' or 'pitching'

        Returns:
            Dictionary with stats data or None if no data found
        """
        try:
            # Determine date range for the season
            start_dt = f"{season}-03-01"
            end_dt = f"{season}-12-31"

            if stat_type == "hitting":
                df = statcast_batter(start_dt=start_dt, end_dt=end_dt, player_id=mlb_id)
            else:
                df = statcast_pitcher(start_dt=start_dt, end_dt=end_dt, player_id=mlb_id)

            if df is None or len(df) == 0:
                return None

            # Aggregate season stats from pitch-level data
            stats = self._aggregate_stats(df, stat_type)
            return stats

        except Exception as e:
            logger.warning(f"Statcast fetch failed for MLB ID {mlb_id}: {e}")
            return None

    async def fetch_bulk_stats(
        self, start_dt: str, end_dt: str, stat_type: str = "hitting"
    ) -> dict:
        """Fetch bulk stats for all players in a date range.

        Args:
            start_dt: Start date (YYYY-MM-DD)
            end_dt: End date (YYYY-MM-DD)
            stat_type: 'hitting' or 'pitching'

        Returns:
            Dictionary with aggregated stats by player
        """
        try:
            df = statcast(start_dt=start_dt, end_dt=end_dt)
            
            if df is None or len(df) == 0:
                return {}

            # Aggregate stats by player
            player_stats = {}
            for player_id in df["batter"].unique():
                player_df = df[df["batter"] == player_id]
                player_stats[player_id] = self._aggregate_stats(player_df, stat_type)

            return player_stats

        except Exception as e:
            logger.warning(f"Statcast bulk fetch failed: {e}")
            return {}

    def _aggregate_stats(self, df, stat_type: str) -> dict:
        """Aggregate pitch-level data to season stats.

        Args:
            df: DataFrame with Statcast data
            stat_type: 'hitting' or 'pitching'

        Returns:
            Dictionary with aggregated stats
        """
        if len(df) == 0:
            return {}

        # For hitting stats
        if stat_type == "hitting":
            return {
                "games": df["game_date"].nunique(),
                "at_bats": len(df[df["events"].notna()]),
                "hits": len(df[df["events"] == "single"]) + len(df[df["events"] == "double"]) + len(df[df["events"] == "triple"]) + len(df[df["events"] == "home_run"]),
                "doubles": len(df[df["events"] == "double"]),
                "triples": len(df[df["events"] == "triple"]),
                "home_runs": len(df[df["events"] == "home_run"]),
                "runs": len(df[df["events"].notna() & df["events"].isin(["single", "double", "triple", "home_run", "walk", "hit_by_pitch"])]),
                "rbi": df["rbi"].sum() if "rbi" in df.columns else 0,
                "strikeouts": len(df[df["description"] == "swinging_strike"]) + len(df[df["description"] == "called_strike"]) + len(df[df["description"] == "foul_tip"]),
                "walks": len(df[df["events"] == "walk"]),
                "avg": (len(df[df["events"].isin(["single", "double", "triple", "home_run"])]) / len(df[df["events"].notna()])) if len(df[df["events"].notna()]) > 0 else 0,
            }
        else:
            # For pitching stats
            return {
                "games": df["game_date"].nunique(),
                "innings_pitched": len(df) / 3,  # Approximate
                "hits": len(df[df["events"].notna()]),
                "runs": df["rbi"].sum() if "rbi" in df.columns else 0,
                "home_runs": len(df[df["events"] == "home_run"]),
                "strikeouts": len(df[df["description"] == "swinging_strike"]) + len(df[df["description"] == "called_strike"]) + len(df[df["description"] == "foul_tip"]),
                "walks": len(df[df["events"] == "walk"]),
            }

    async def close(self):
        """Close client (no-op for Statcast)."""
        pass
