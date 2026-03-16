"""Weekly matchup tracking model.

Stores head-to-head matchups between teams for each week of the
fantasy season, including scores and winner determination.
"""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class Matchup(Base):
    """Weekly head-to-head matchup tracking between fantasy teams.

    Records category-by-category results for each matchup, tracking wins,
    losses, and ties per scoring category. Supports both complete and
    in-progress matchups for real-time standings calculation.

    Team stats JSONB fields store raw category scores for detailed analysis.
    """

    __tablename__ = "matchup"
    __table_args__ = (UniqueConstraint("league_id", "week", "team_a_id", "team_b_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"))
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    team_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.id"))
    team_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.id"))
    team_a_stats: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    team_b_stats: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    category_results: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    team_a_wins: Mapped[int] = mapped_column(Integer, server_default="0")
    team_b_wins: Mapped[int] = mapped_column(Integer, server_default="0")
    ties: Mapped[int] = mapped_column(Integer, server_default="0")
    is_complete: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
