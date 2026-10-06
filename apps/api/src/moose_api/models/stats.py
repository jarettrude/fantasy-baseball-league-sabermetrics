from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class StatLine(Base):
    """Individual game statistics for MLB players.

    Stores box score stats for both batters and pitchers from various sources
    (Yahoo, MLB, Fangraphs). The unique constraint prevents duplicate stat lines
    from the same source for the same player and game.

    Extra_stats JSONB field captures source-specific metrics not in the core schema.
    """

    __tablename__ = "stat_line"
    __table_args__ = (UniqueConstraint("player_id", "game_date", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.id"))
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    game_datetime_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    game_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    is_pitcher: Mapped[bool] = mapped_column(Boolean, nullable=False)
    runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rbi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stolen_bases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batting_avg: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)
    hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at_bats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strikeouts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    era: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    whip: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)
    innings_pitched: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    earned_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_stats: Mapped[dict] = mapped_column(JSONB, server_default="{}")


class ProjectionBaseline(Base):
    """Season-long player projections from external sources.

    Stores projected stat lines from sources like Fangraphs ROS, Steamer, or ZiPS.
    Used as baseline inputs for valuation calculations and trade analysis.

    Projections are source-specific and season-bound to support comparison
    across different projection systems.
    """

    __tablename__ = "projection_baseline"
    __table_args__ = (UniqueConstraint("player_id", "source", "season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.id"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    projected_stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlayerValueSnapshot(Base):
    """Historical player valuation snapshots for trend analysis.

    Captures computed player values at specific points in time with category
    breakdowns, rankings, and advanced metrics. Enables tracking value changes
    over the season and identifying buy-low/sell-high opportunities.

    Type field distinguishes between season-long and next-N-games projections.
    Injury weight adjusts value based on current injury status.
    """

    __tablename__ = "player_value_snapshot"
    __table_args__ = (UniqueConstraint("player_id", "snapshot_date", "type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    category_scores: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    composite_value: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    yahoo_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    our_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    injury_weight: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="1.0")
    xwoba: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    xera: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    roster_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    roster_trend: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
