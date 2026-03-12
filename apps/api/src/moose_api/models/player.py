from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class Player(Base):
    """Core player model representing MLB players in the fantasy league.

    Stores demographic data, position eligibility, injury status, and cross-platform
    identifiers (Yahoo, MLB, Lahman) for player mapping and data synchronization.

    Injury tracking includes status, notes, expected return date, and missed games
    count to inform roster decisions and player availability.
    """

    __tablename__ = "player"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yahoo_player_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    yahoo_player_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_position: Mapped[str] = mapped_column(Text, nullable=False)
    eligible_positions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    mlb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lahman_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_abbr: Mapped[str | None] = mapped_column(Text, nullable=True)
    bats: Mapped[str | None] = mapped_column(Text, nullable=True)
    throws: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_pitcher: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    yahoo_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    injury_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    injury_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    injury_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    expected_return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    missed_games_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlayerMapping(Base):
    """Bridge table mapping Yahoo player identifiers to external datasets.

    Links Yahoo fantasy players to MLB and Lahman database IDs for cross-referencing
    statistical data. Supports both automatic mapping algorithms and manual overrides
    with confidence scoring and status tracking.

    Auto-mapped entries can be reviewed and confirmed or rejected by commissioners
    to ensure data accuracy for valuation calculations.
    """

    __tablename__ = "player_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yahoo_player_key: Mapped[str] = mapped_column(Text, ForeignKey("player.yahoo_player_key"), nullable=False)
    mlb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lahman_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    auto_mapped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="'confirmed'")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
