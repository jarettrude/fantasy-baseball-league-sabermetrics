"""Player-related schemas for roster, values, and mappings.

Defines Pydantic models for player information, value projections,
roster assignments, free agents, and cross-dataset ID mappings.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class ManagerBriefingResponse(BaseModel):
    """Response model for manager briefing content.

    Contains AI-generated briefing content with view status tracking.
    """

    id: int
    date: date
    content: str
    is_viewed: bool


class PlayerResponse(BaseModel):
    """Response model for basic player information.

    Contains player demographic data, position eligibility, team,
    and injury status.
    """

    id: int
    yahoo_player_key: str
    name: str
    primary_position: str
    eligible_positions: list[str]
    team_abbr: str | None
    is_pitcher: bool
    yahoo_rank: int | None = None
    injury_status: str | None
    injury_note: str | None


class PlayerValueResponse(BaseModel):
    """Response model for player value projections.

    Contains computed player values including category scores,
    composite value, advanced metrics, and injury adjustments.
    """

    player_id: int
    player_name: str
    primary_position: str
    team_abbr: str | None
    snapshot_date: date
    type: str
    category_scores: dict
    composite_value: Decimal
    yahoo_rank: int | None
    our_rank: int | None
    injury_weight: Decimal
    injury_status: str | None
    xwoba: Decimal | None = None
    xera: Decimal | None = None
    roster_percent: float | None = None
    roster_trend: float | None = None


class RosterSlotResponse(BaseModel):
    """Response model for a single roster slot.

    Contains player information with position assignment and
    associated value projections.
    """

    player: PlayerResponse
    position: str
    season_value: PlayerValueResponse | None
    next_games_value: PlayerValueResponse | None


class BenchResponse(BaseModel):
    """Response model for manager's active roster.

    Contains complete roster with values, update timestamps,
    and latest manager briefing.
    """

    team_name: str
    roster: list[RosterSlotResponse]
    season_value_updated: datetime | None
    next_games_value_updated: datetime | None
    briefing: ManagerBriefingResponse | None = None


class FreeAgentResponse(BaseModel):
    """Response model for a free agent player.

    Contains player information with value projections and
    availability status.
    """

    player: PlayerResponse
    season_value: PlayerValueResponse | None
    next_games_value: PlayerValueResponse | None
    is_available: bool


class ValuedPlayerResponse(BaseModel):
    """A player bundled with their latest season/next-games composite values.

    Compact projection used by the recommendations endpoint and briefing
    payloads so the consumer does not need to rehydrate full
    ``PlayerValueResponse`` objects for simple ranking comparisons.
    """

    id: int
    name: str
    primary_position: str
    eligible_positions: list[str]
    team_abbr: str | None
    is_pitcher: bool
    injury_status: str | None
    composite_value: Decimal
    next_games_value: Decimal | None = None
    our_rank: int | None = None
    yahoo_rank: int | None = None
    roster_slot: str | None = None


class PositionUpgradeResponse(BaseModel):
    """Per-position comparison between the weakest starter and top waivers."""

    position: str
    incumbent: ValuedPlayerResponse | None
    top_free_agents: list[ValuedPlayerResponse]
    delta: Decimal | None
    recommend: bool


class DropCandidateResponse(BaseModel):
    """A roster player the manager should consider dropping.

    ``reason`` distinguishes a purely global worst-value flag from a
    position-aligned upgrade recommendation. When ``reason`` is
    ``"upgrade_available_at_position"`` the ``replacement`` and
    ``delta`` fields describe the matched pickup.
    """

    player: ValuedPlayerResponse
    reason: str
    position: str | None = None
    replacement: ValuedPlayerResponse | None = None
    delta: Decimal | None = None


class BenchRecommendationsResponse(BaseModel):
    """Position-aligned drop/pickup plan for the authenticated manager's team."""

    team_id: int
    team_name: str
    roster: list[ValuedPlayerResponse]
    drop_candidates: list[DropCandidateResponse]
    upgrades_by_position: dict[str, PositionUpgradeResponse]
    top_fa_overall: list[ValuedPlayerResponse]


class FreeAgentListResponse(BaseModel):
    """Response model for free agent list.

    Contains list of available free agents with snapshot timestamp.
    """

    free_agents: list[FreeAgentResponse]
    snapshot_at: datetime | None


class PlayerMappingResponse(BaseModel):
    """Response model for player ID mappings.

    Contains cross-dataset player ID mappings between Yahoo,
    MLB, and Lahman datasets with confidence scores.
    """

    id: int
    yahoo_player_key: str
    player_name: str
    mlb_id: int | None
    lahman_id: str | None
    source_confidence: float
    auto_mapped: bool
    status: str
    notes: str | None


class PlayerMappingUpdateRequest(BaseModel):
    """Request model for updating player ID mappings.

    Contains fields for manual correction or confirmation of
    player ID mappings.
    """

    mlb_id: int | None = None
    lahman_id: str | None = None
    status: str = "confirmed"
    notes: str | None = None
