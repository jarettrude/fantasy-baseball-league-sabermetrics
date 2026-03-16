"""League configuration and metadata model.

Stores fantasy league settings, scoring rules, and current season state.
Manages league-wide configuration that affects gameplay and calculations.
"""

from datetime import datetime

from sqlalchemy import Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class League(Base):
    """Fantasy league model representing a Yahoo fantasy baseball league.

    Stores league metadata, scoring configuration, roster position rules, and
    stat categories. The JSONB fields store complex Yahoo API responses for
    scoring rules and position eligibility.

    Current week tracking enables period-based operations like weekly recaps
    and matchup calculations.
    """

    __tablename__ = "league"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yahoo_league_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    num_teams: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_type: Mapped[str] = mapped_column(Text, nullable=False)
    current_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stat_categories: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    roster_positions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
