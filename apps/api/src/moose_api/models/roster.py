from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class RosterSlot(Base):
    """Historical roster assignment tracking for teams and players.

    Records which players were assigned to which roster positions for each
    team, season, and week. Supports historical analysis and trend tracking.

    The unique constraint ensures a player cannot be in multiple positions
    on the same team in the same week. Active flag indicates whether the
    assignment was current at snapshot time.
    """

    __tablename__ = "roster_slot"
    __table_args__ = (UniqueConstraint("team_id", "player_id", "season", "week", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.id"))
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.id"))
    position: Mapped[str] = mapped_column(Text, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
