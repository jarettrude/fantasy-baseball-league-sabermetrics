"""Fetch gambling odds and player props from The Odds API.

Provides implied win probabilities and player prop over/unders (Pitcher K's, etc)
to serve as 'matchup_multipliers' for the Next Val calculation.
"""

from __future__ import annotations

import logging

import httpx

from moose_api.core.config import settings

logger = logging.getLogger(__name__)


class GamblingService:
    """Service to interact with The Odds API (https://the-odds-api.com/)."""

    BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

    TEAM_MAP = {
        "Arizona Diamondbacks": "ARI",
        "Atlanta Braves": "ATL",
        "Baltimore Orioles": "BAL",
        "Boston Red Sox": "BOS",
        "Chicago Cubs": "CHC",
        "Chicago White Sox": "CWS",
        "Cincinnati Reds": "CIN",
        "Cleveland Guardians": "CLE",
        "Colorado Rockies": "COL",
        "Detroit Tigers": "DET",
        "Houston Astros": "HOU",
        "Kansas City Royals": "KC",
        "Los Angeles Angels": "LAA",
        "Los Angeles Dodgers": "LAD",
        "Miami Marlins": "MIA",
        "Milwaukee Brewers": "MIL",
        "Minnesota Twins": "MIN",
        "New York Mets": "NYM",
        "New York Yankees": "NYY",
        "Oakland Athletics": "OAK",
        "Philadelphia Phillies": "PHI",
        "Pittsburgh Pirates": "PIT",
        "San Diego Padres": "SD",
        "San Francisco Giants": "SF",
        "Seattle Mariners": "SEA",
        "St. Louis Cardinals": "STL",
        "Tampa Bay Rays": "TB",
        "Texas Rangers": "TEX",
        "Toronto Blue Blue Jays": "TOR",
        "Washington Nationals": "WSH",
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or getattr(settings, "THE_ODDS_API_KEY", None)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_team_win_probabilities(self) -> dict[str, float]:
        """Fetch implied win probabilities from H2H moneyline odds."""
        if not self.api_key:
            return {}

        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            resp = await self._client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            # E.g. decimal 1.5 -> 1/1.5 = 66% win prob
            probabilities = {}
            for game in data:
                for bookmaker in game.get("bookmakers", []):
                    # We'll just take the first one (e.g. FanDuel) for now
                    for market in bookmaker.get("markets", []):
                        if market["key"] == "h2h":
                            for outcome in market.get("outcomes", []):
                                team_name = outcome["name"]
                                team_abbr = self.TEAM_MAP.get(team_name, team_name)
                                price = float(outcome.get("price", 2.0))
                                if price > 0:
                                    prob = 1.0 / price
                                    probabilities[team_abbr] = prob
                    break  # Just use the first bookmaker
            return probabilities
        except Exception as e:
            logger.warning(f"Failed to fetch team odds: {e}")
            return {}

    async def get_pitcher_prop_multipliers(self) -> dict[str, float]:
        """Fetch pitcher prop multipliers (e.g. Strikeout Over/Under vs Projected).

        Currently a placeholder for a more complex mapping of 'expected K's / season avg'.
        For now, returns 1.0 for everyone until we have a good prop history.
        """
        return {}

    async def close(self):
        await self._client.aclose()
