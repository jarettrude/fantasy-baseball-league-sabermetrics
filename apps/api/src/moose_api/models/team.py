from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class Team(Base):
    """Fantasy team model representing a franchise in the league.

    Links to a league and optionally to a user account for manager authentication.
    Tracks win-loss-tie record and current standing for leaderboard display.
    """

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"))
    yahoo_team_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    yahoo_manager_guid: Mapped[str | None] = mapped_column(Text, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    manager_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    wins: Mapped[int] = mapped_column(Integer, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, server_default="0")
    ties: Mapped[int] = mapped_column(Integer, server_default="0")
    standing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
