from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class ManagerBriefing(Base):
    """Daily AI-generated briefings for fantasy team managers.

    Stores personalized morning briefings with actionable insights,
    waiver wire recommendations, and roster optimization suggestions.

    Viewed status tracking ensures managers only see new briefings each day.
    Briefings are generated nightly and displayed on the dashboard.
    """

    __tablename__ = "manager_briefing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_viewed: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
