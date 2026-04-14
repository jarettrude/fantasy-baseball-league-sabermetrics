"""Baseball Savant Statcast API client for fast bulk player statistics.

Uses pybaseball to fetch Statcast data from Baseball Savant, which is significantly
faster than the MLB Stats API for bulk data retrieval. Serves as primary data source
with fallback to MLB Stats API.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# Suppress pybaseball INFO logging to reduce noise
logging.getLogger("pybaseball").setLevel(logging.WARNING)

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

    async def fetch_player_stats(self, mlb_id: int, season: int, stat_type: str = "hitting") -> dict | None:
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

    async def fetch_bulk_stats(self, start_dt: str, end_dt: str, stat_type: str = "hitting") -> dict:
        """Fetch bulk stats for all players in a date range.

        Args:
            start_dt: Start date (YYYY-MM-DD)
            end_dt: End date (YYYY-MM-DD)
            stat_type: 'hitting' or 'pitching'

        Returns:
            Dictionary with aggregated stats by player ID
        """
        try:
            df = statcast(start_dt=start_dt, end_dt=end_dt)

            if df is None or len(df) == 0:
                return {}

            # Aggregate stats by player
            player_stats = {}
            if stat_type == "hitting":
                # For hitting, use batter IDs (filter out NaN values)
                for player_id in df["batter"].dropna().unique():
                    player_df = df[df["batter"] == player_id]
                    stats = self._aggregate_stats(player_df, stat_type)
                    if stats:
                        stats["is_pitcher"] = False
                        player_stats[player_id] = stats
            else:
                # For pitching, use pitcher IDs (filter out NaN values)
                for player_id in df["pitcher"].dropna().unique():
                    player_df = df[df["pitcher"] == player_id]
                    stats = self._aggregate_stats(player_df, stat_type)
                    if stats:
                        stats["is_pitcher"] = True
                        player_stats[player_id] = stats

            return player_stats

        except Exception as e:
            logger.warning(f"Statcast bulk fetch failed: {e}")
            return {}

    async def close(self):
        """Close client (no-op for Statcast)."""
        pass

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
            # Count actual events, not pitch descriptions
            at_bats = len(
                df[df["events"].notna() & ~df["events"].isin(["walk", "hit_by_pitch", "sac_bunt", "sac_fly"])]
            )
            hits = len(df[df["events"].isin(["single", "double", "triple", "home_run"])])
            strikeouts = len(df[df["events"] == "strikeout"])
            walks = len(df[df["events"] == "walk"])

            return {
                "games": df["game_date"].nunique(),
                "at_bats": at_bats,
                "hits": hits,
                "doubles": len(df[df["events"] == "double"]),
                "triples": len(df[df["events"] == "triple"]),
                "home_runs": len(df[df["events"] == "home_run"]),
                "runs": 0,  # Runs scored cannot be derived from batter's own hit events in pitch-level data
                "rbi": df["rbi"].sum() if "rbi" in df.columns else 0,
                "strikeouts": strikeouts,
                "walks": walks,
                "avg": hits / at_bats if at_bats > 0 else 0,
            }
        else:
            # For pitching stats
            outs = len(df[df["events"].isin(["strikeout", "ground_out", "fly_out", "line_out", "pop_out"])])
            innings_pitched = outs / 3

            return {
                "games": df["game_date"].nunique(),
                "innings_pitched": innings_pitched,
                "hits": len(df[df["events"].isin(["single", "double", "triple", "home_run"])]),
                "runs": 0,  # Runs allowed calculation from pitch-level data requires tracking game state
                "home_runs": len(df[df["events"] == "home_run"]),
                "strikeouts": len(df[df["events"] == "strikeout"]),
                "walks": len(df[df["events"] == "walk"]),
                "wins": 0,  # Not available in pitch-level data
                "saves": 0,  # Not available in pitch-level data
                "earned_runs": 0,  # Not available in pitch-level data
                "era": None,  # Not available in pitch-level data
                "whip": None,  # Not available in pitch-level data
            }
