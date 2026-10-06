"""League-related schemas for standings, matchups, and configuration.

Defines Pydantic models for league metadata, standings tables, weekly
matchup results, and scoring category information.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LeagueResponse(BaseModel):
    """Response model for league configuration and metadata.

    Contains league settings including scoring type, roster positions,
    stat categories, and current week information.
    """

    id: int
    yahoo_league_key: str
    name: str
    season: int
    num_teams: int
    scoring_type: str
    current_week: int | None
    start_week: int | None
    end_week: int | None
    stat_categories: list[dict]
    roster_positions: list[dict]
    updated_at: datetime | None


class StandingsEntry(BaseModel):
    """Single team entry in the league standings.

    Contains team performance metrics including wins, losses, ties,
    and current rank/standing position.
    """

    team_id: int
    team_name: str
    logo_url: str | None
    wins: int
    losses: int
    ties: int
    standing: int | None


class StandingsResponse(BaseModel):
    """Response model for league standings table.

    Contains ordered list of team standings with league metadata
    and generation timestamp for caching purposes.
    """

    league_name: str
    season: int
    current_week: int | None
    standings: list[StandingsEntry]
    generated_at: datetime | None = None


class MatchupResponse(BaseModel):
    """Response model for a single head-to-head matchup.

    Contains matchup details including team names, stats, category
    results, and win/loss/tie breakdown.
    """

    id: int
    week: int
    team_a_name: str
    team_a_logo: str | None
    team_b_name: str
    team_b_logo: str | None
    team_a_stats: dict
    team_b_stats: dict
    category_results: dict
    team_a_wins: int
    team_b_wins: int
    ties: int
    is_complete: bool


class WeekMatchupsResponse(BaseModel):
    """Response model for all matchups in a given week.

    Contains list of all matchups for a specific week with generation
    timestamp for caching purposes.
    """

    week: int
    matchups: list[MatchupResponse]
    generated_at: datetime | None = None
